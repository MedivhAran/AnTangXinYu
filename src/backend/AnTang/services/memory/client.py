import asyncio
import hashlib
import json
import logging
import uuid
import pytz
from enum import Enum
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional


from AnTang.core.models.manager import ModelManager
from AnTang.services.memory.config import MemoryItem
from AnTang.services.memory.prompts import (
    PROCEDURAL_MEMORY_SYSTEM_PROMPT,
    get_update_memory_messages,
)
from AnTang.services.memory.base import MemoryBase
from AnTang.database.dao.memory_history import MemoryHistoryDao
from AnTang.services.memory.utils import (
    get_fact_retrieval_messages,
    parse_messages,
    parse_vision_messages,
    remove_code_blocks,
)

from langchain_core.messages import HumanMessage, SystemMessage
from AnTang.services.memory.vector_stores import VectorStoreManager


class MemoryType(Enum):
    """
    三种记忆类型
    """

    SEMANTIC = "semantic_memory"  # 语义记忆（用户事实和偏好）
    EPISODIC = "episodic_memory"  # 情节记忆（具体对话片段）
    PROCEDURAL = "procedural_memory"  # 程序记忆（存的是怎么做某件事的操作流程。）


def _build_filters_and_metadata(
    *,  # 强制后面的参数只能按关键字传
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_id: Optional[str] = None,  # 仅用于查询时过滤
    input_metadata: Optional[Dict[str, Any]] = None,
    input_filters: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """根据会话标识和 actor 标识，构造“存储用的元数据”和“查询用的过滤条件”。

    这个辅助函数支持多个会话标识（`user_id`、`agent_id`、`run_id`）来灵活划定会话范围，
    并可选地把查询收窄到某个 `actor_id`。返回两个字典：

    1. `base_metadata_template`：存新记忆时作为元数据模板，
       包含所有传入的会话标识以及 `input_metadata`。
    2. `effective_query_filters`：查询已有记忆时使用，包含所有传入的会话标识、
       `input_filters`，以及（若指定了 actor 相关输入）解析出的 actor 标识用于定向过滤。

    actor 过滤优先级：显式的 `actor_id` 参数 → `filters["actor_id"]`。
    解析出的 actor ID 只用于查询，不会写进 `base_metadata_template`，
    因为存储用的 actor 通常在后续阶段从消息内容里推导。

    Args:
        user_id (Optional[str]): 用户标识，用于划定会话范围。
        agent_id (Optional[str]): Agent 标识，用于划定会话范围。
        run_id (Optional[str]): Run 标识，用于划定会话范围。
        actor_id (Optional[str]): 显式 actor 标识，可作为 actor 过滤的来源，优先级见上文。
        input_metadata (Optional[Dict[str, Any]]): 存储元数据模板的基础字典，
            会被补充上会话标识。默认空字典。
        input_filters (Optional[Dict[str, Any]]): 查询过滤条件的基础字典，
            会被补充上会话标识与 actor 标识。默认空字典。

    Returns:
        tuple[Dict[str, Any], Dict[str, Any]]: 包含两项的元组：
            - base_metadata_template (Dict[str, Any]): 存记忆用的元数据模板，限定在给定会话范围内。
            - effective_query_filters (Dict[str, Any]): 查记忆用的过滤条件，限定在给定会话（及可能的 actor）范围内。
    """

    base_metadata_template = deepcopy(input_metadata) if input_metadata else {}
    effective_query_filters = deepcopy(input_filters) if input_filters else {}

    # ---------- 加入所有传入的会话 id ----------
    session_ids_provided = []

    if user_id:
        base_metadata_template["user_id"] = user_id
        effective_query_filters["user_id"] = user_id
        session_ids_provided.append("user_id")

    if agent_id:
        base_metadata_template["agent_id"] = agent_id
        effective_query_filters["agent_id"] = agent_id
        session_ids_provided.append("agent_id")

    if run_id:
        base_metadata_template["run_id"] = run_id
        effective_query_filters["run_id"] = run_id
        session_ids_provided.append("run_id")

    if not session_ids_provided:
        raise ValueError("At least one of 'user_id', 'agent_id', or 'run_id' must be provided.")

    # ---------- 可选的 actor 过滤 ----------
    resolved_actor_id = actor_id or effective_query_filters.get("actor_id")
    if resolved_actor_id:
        effective_query_filters["actor_id"] = resolved_actor_id

    return base_metadata_template, effective_query_filters


logger = logging.getLogger(__name__)


class AsyncMemory(MemoryBase):
    def __init__(self):

        self.embedding_model = ModelManager.get_embedding_model()
        self.vector_store = VectorStoreManager.get_chroma_vector()
        self.llm = ModelManager.get_conversation_model()
        self.db = MemoryHistoryDao

        # 目前暂不支持Graph
        self.enable_graph = False
        self.graph = None

    async def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        llm=None,
    ):
        """异步创建一条新记忆。

        Args:
            messages (str or List[Dict[str, str]]): 要存入记忆的消息。
            user_id (str, optional): 创建记忆的用户 ID。
            agent_id (str, optional): 创建记忆的 agent ID。默认 None。
            run_id (str, optional): 创建记忆的 run ID。默认 None。
            metadata (dict, optional): 随记忆一起存储的元数据。默认 None。
            infer (bool, optional): 是否对消息做事实推断。默认 True。
            memory_type (str, optional): 要创建的记忆类型。默认 None。
                                         传 "procedural_memory" 可创建程序记忆。
            prompt (str, optional): 创建记忆时使用的 prompt。默认 None。
            llm (BaseChatModel, optional): 生成程序记忆时使用的 LLM。默认 None。在使用 LangChain ChatModel 时有用。
        Returns:
            dict: 包含本次记忆写入结果的字典。
        """
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_metadata=metadata
        )

        if memory_type is not None and memory_type != MemoryType.PROCEDURAL.value:
            raise ValueError(
                f"Invalid 'memory_type'. Please pass {MemoryType.PROCEDURAL.value} to create procedural memories."
            )

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        elif isinstance(messages, dict):
            messages = [messages]

        elif not isinstance(messages, list):
            raise ValueError("messages must be str, dict, or list[dict]")

        if agent_id is not None and memory_type == MemoryType.PROCEDURAL.value:
            results = await self._create_procedural_memory(
                messages, metadata=processed_metadata, prompt=prompt, llm=llm
            )
            return results

        messages = parse_vision_messages(messages)

        vector_store_task = asyncio.create_task(
            self._add_to_vector_store(messages, processed_metadata, effective_filters, infer)
        )
        graph_task = asyncio.create_task(self._add_to_graph(messages, effective_filters))

        vector_store_result, graph_result = await asyncio.gather(vector_store_task, graph_task)

        if self.enable_graph:
            return {
                "results": vector_store_result,
                "relations": graph_result,
            }

        return {"results": vector_store_result}

    async def _add_to_vector_store(
        self,
        messages: list,
        metadata: dict,
        effective_filters: dict,
        infer: bool,
    ):
        """记忆写入核心：infer=False 时原样入库；infer=True 时先用 LLM 抽事实，
        再检索相似的旧记忆，交给 LLM 决策 ADD/UPDATE/DELETE/NONE 后落库。"""
        # ── 分支 A：infer=False，不抽事实，每条消息原样存成一条记忆 ──
        if not infer:
            returned_memories = []
            for message_dict in messages:
                # 跳过格式不合法的消息（不是 dict，或缺 role / content）
                if (
                    not isinstance(message_dict, dict)
                    or message_dict.get("role") is None
                    or message_dict.get("content") is None
                ):
                    logger.warning(f"Skipping invalid message format (async): {message_dict}")
                    continue

                # system 消息不写入记忆
                if message_dict["role"] == "system":
                    continue

                # 每条记忆带上自己的 role；actor_id（说话人）取自消息的 name 字段
                per_msg_meta = deepcopy(metadata)
                per_msg_meta["role"] = message_dict["role"]

                actor_name = message_dict.get("name")
                if actor_name:
                    per_msg_meta["actor_id"] = actor_name

                # 消息内容向量化后直接落库（一条消息 = 一条记忆）
                msg_content = message_dict["content"]
                msg_embeddings = await asyncio.to_thread(self.embedding_model.embed, msg_content)
                mem_id = await self._create_memory(msg_content, msg_embeddings, per_msg_meta)

                returned_memories.append(
                    {
                        "id": mem_id,
                        "memory": msg_content,
                        "event": "ADD",
                        "actor_id": actor_name if actor_name else None,
                        "role": message_dict["role"],
                    }
                )
            return returned_memories

        # ── 分支 B：infer=True，把对话浓缩成“事实”，再和旧记忆智能合并 ──
        # 步骤 1：把消息拼成纯文本，第一次调 LLM，抽取本轮的原子事实列表（如“喜欢芝士披萨”）
        parsed_messages = parse_messages(messages)
        system_prompt, user_prompt = get_fact_retrieval_messages(parsed_messages)

        # 这批新事实后面会和旧记忆一起交给第二个 LLM，逐条判定新增/更新/删除/不动
        response = await asyncio.to_thread(
            self.llm.invoke,
            input=[SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
            config=None,
            response_format={"type": "json_object"},
        )
        # 解析 LLM 返回的 {"facts": [...]}；解析失败就按“没抽到事实”处理
        try:
            response = remove_code_blocks(response.content)
            new_retrieved_facts = json.loads(response)["facts"]
        except Exception as e:
            logger.error(f"Error in new_retrieved_facts: {e}")
            new_retrieved_facts = []

        if not new_retrieved_facts:
            logger.debug("No new facts retrieved from input. Skipping memory update LLM call.")

        # 步骤 2：为每条新事实，在向量库里检索出最相似的旧记忆，作为候选合并对象
        retrieved_old_memory = []
        new_message_embeddings = {}  # 顺手缓存每条事实的向量，落库时复用，省一次 embed

        async def process_fact_for_search(new_mem_content):
            # 把一条事实向量化，并且在同一作用域下检索最相似的 5 条旧记忆
            embeddings = await asyncio.to_thread(self.embedding_model.embed, new_mem_content)
            # 缓存一下新事实的向量
            new_message_embeddings[new_mem_content] = embeddings
            # 检索旧记忆，选出最相似的五条
            existing_mems = await asyncio.to_thread(
                self.vector_store.search,
                query=new_mem_content,
                vectors=embeddings,
                limit=5,
                filters=effective_filters,  # 这里的 filters 即推断阶段用的查询过滤条件
            )

            return [{"id": mem.id, "text": mem.payload["data"]} for mem in existing_mems]

        # 所有事实并发检索，再把结果汇总到一起
        search_tasks = [process_fact_for_search(fact) for fact in new_retrieved_facts]
        search_results_list = await asyncio.gather(*search_tasks)

        for result_group in search_results_list:
            retrieved_old_memory.extend(result_group)

        # 不同事实可能召回同一条旧记忆，按真实 id 去重
        unique_data = {}
        for item in retrieved_old_memory:
            unique_data[item["id"]] = item

        retrieved_old_memory = list(unique_data.values())

        logger.info(f"Total existing memories: {len(retrieved_old_memory)}")
        # 关键技巧：把真实 uuid 临时替换成简单序号 "0"/"1"/"2"… 再喂给 LLM，
        # 防止 LLM 抄错或编造长 uuid；temp_uuid_mapping 负责事后把序号还原成真实 id
        temp_uuid_mapping = {}
        for idx, item in enumerate(retrieved_old_memory):
            temp_uuid_mapping[str(idx)] = item["id"]
            retrieved_old_memory[idx]["id"] = str(idx)

        # 步骤 3：第二次调 LLM——把[旧记忆 + 新事实]交给它，逐条决定增/改/删/不动
        if new_retrieved_facts:
            function_calling_prompt = get_update_memory_messages(retrieved_old_memory, new_retrieved_facts)

            try:
                response = await asyncio.to_thread(
                    self.llm.invoke,
                    input=[{"role": "user", "content": function_calling_prompt}],
                    config=None,
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.error(f"Error in new memory actions response: {e}")
                response = ""
            # 解析 LLM 给出的操作清单 {"memory": [{id, text, event}, ...]}
            try:
                response = remove_code_blocks(response.content)
                new_memories_with_actions = json.loads(response)
            except Exception as e:
                logger.error(f"Invalid JSON response: {e}")
                new_memories_with_actions = {}
        else:
            new_memories_with_actions = {}  # 没抽到事实，无需改动记忆

        # 步骤 4：按 LLM 的操作清单并发落库（先把任务都建好，后面统一 await）
        returned_memories = []
        try:
            memory_tasks = []
            for resp in new_memories_with_actions.get("memory", []):
                logger.info(resp)
                try:
                    action_text = resp.get("text")
                    if not action_text:
                        continue
                    event_type = resp.get("event")

                    if event_type == "ADD":
                        # 新事实 → 新增一条记忆
                        task = asyncio.create_task(
                            self._create_memory(
                                data=action_text,
                                existing_embeddings=new_message_embeddings,
                                metadata=deepcopy(metadata),
                            )
                        )
                        memory_tasks.append((task, resp, "ADD", None))
                    elif event_type == "UPDATE":
                        # 改写旧记忆 → 先用序号映射回真实 uuid，再更新
                        task = asyncio.create_task(
                            self._update_memory(
                                memory_id=temp_uuid_mapping[resp["id"]],
                                data=action_text,
                                existing_embeddings=new_message_embeddings,
                                metadata=deepcopy(metadata),
                            )
                        )
                        memory_tasks.append((task, resp, "UPDATE", temp_uuid_mapping[resp["id"]]))
                    elif event_type == "DELETE":
                        # 旧记忆作废 → 同样映射回真实 uuid 后删除
                        task = asyncio.create_task(self._delete_memory(memory_id=temp_uuid_mapping[resp.get("id")]))
                        memory_tasks.append((task, resp, "DELETE", temp_uuid_mapping[resp.get("id")]))
                    elif event_type == "NONE":
                        # 已有相同信息，不做任何改动
                        logger.info("NOOP for Memory (async).")
                except Exception as e:
                    logger.error(f"Error processing memory action (async): {resp}, Error: {e}")

            # 等所有增删改任务执行完，整理成统一的返回结构
            for task, resp, event_type, mem_id in memory_tasks:
                try:
                    result_id = await task
                    if event_type == "ADD":
                        returned_memories.append({"id": result_id, "memory": resp.get("text"), "event": event_type})
                    elif event_type == "UPDATE":
                        returned_memories.append(
                            {
                                "id": mem_id,
                                "memory": resp.get("text"),
                                "event": event_type,
                                "previous_memory": resp.get("old_memory"),
                            }
                        )
                    elif event_type == "DELETE":
                        returned_memories.append({"id": mem_id, "memory": resp.get("text"), "event": event_type})
                except Exception as e:
                    logger.error(f"Error awaiting memory task (async): {e}")
        except Exception as e:
            logger.error(f"Error in memory processing loop (async): {e}")

        return returned_memories

    async def _add_to_graph(self, messages, filters):
        added_entities = []
        if self.enable_graph:
            if filters.get("user_id") is None:
                filters["user_id"] = "user"

            data = "\n".join([msg["content"] for msg in messages if "content" in msg and msg["role"] != "system"])
            added_entities = await asyncio.to_thread(self.graph.add, data, filters)

        return added_entities

    async def get(self, memory_id):
        """异步按 ID 读取一条记忆。

        Args:
            memory_id (str): 要读取的记忆 ID。

        Returns:
            dict: 读取到的记忆。
        """
        memory = await asyncio.to_thread(self.vector_store.get, vector_id=memory_id)
        if not memory:
            return None

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
        ]

        core_and_promoted_keys = {"data", "hash", "created_at", "updated_at", "id", *promoted_payload_keys}

        result_item = MemoryItem(
            id=memory.id,
            memory=memory.payload["data"],
            hash=memory.payload.get("hash"),
            created_at=memory.payload.get("created_at"),
            updated_at=memory.payload.get("updated_at"),
        ).model_dump()

        for key in promoted_payload_keys:
            if key in memory.payload:
                result_item[key] = memory.payload[key]

        additional_metadata = {k: v for k, v in memory.payload.items() if k not in core_and_promoted_keys}
        if additional_metadata:
            result_item["metadata"] = additional_metadata

        return result_item

    async def get_all(
        self,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ):
        """列出全部记忆。

        Args:
            user_id (str, optional): 用户 id
            agent_id (str, optional): agent id
            run_id (str, optional): run id
            filters (dict, optional): 额外的自定义键值过滤条件，会与基于 ID 的会话过滤合并。
                例如 `filters={"actor_id": "some_user"}`。
            limit (int, optional): 返回记忆的最大数量。默认 100。

        Returns:
            dict: 在 "results" 键下包含一组记忆的字典；若启用图谱存储还会带 "relations"。
                  v1.0 接口可能直接返回列表（见弃用提示）。
                  v1.1+ 示例：`{"results": [{"id": "...", "memory": "...", ...}]}`
        """

        _, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_filters=filters
        )

        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError(
                "When 'conversation_id' is not provided (classic mode), "
                "at least one of 'user_id', 'agent_id', or 'run_id' must be specified for get_all."
            )

        vector_store_task = asyncio.create_task(self._get_all_from_vector_store(effective_filters, limit))

        graph_task = None
        if self.enable_graph:
            graph_get_all = getattr(self.graph, "get_all", None)
            if callable(graph_get_all):
                if asyncio.iscoroutinefunction(graph_get_all):
                    graph_task = asyncio.create_task(graph_get_all(effective_filters, limit))
                else:
                    graph_task = asyncio.create_task(asyncio.to_thread(graph_get_all, effective_filters, limit))

        results_dict = {}
        if graph_task:
            vector_store_result, graph_entities_result = await asyncio.gather(vector_store_task, graph_task)
            results_dict.update({"results": vector_store_result, "relations": graph_entities_result})
        else:
            results_dict.update({"results": await vector_store_task})

        return results_dict

    async def _get_all_from_vector_store(self, filters, limit):
        memories_result = await asyncio.to_thread(self.vector_store.list, filters=filters, limit=limit)
        actual_memories = (
            memories_result[0]
            if isinstance(memories_result, (tuple, list)) and len(memories_result) > 0
            else memories_result
        )

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
        ]
        core_and_promoted_keys = {"data", "hash", "created_at", "updated_at", "id", *promoted_payload_keys}

        formatted_memories = []
        for mem in actual_memories:
            memory_item_dict = MemoryItem(
                id=mem.id,
                memory=mem.payload["data"],
                hash=mem.payload.get("hash"),
                created_at=mem.payload.get("created_at"),
                updated_at=mem.payload.get("updated_at"),
            ).model_dump(exclude={"score"})

            for key in promoted_payload_keys:
                if key in mem.payload:
                    memory_item_dict[key] = mem.payload[key]

            additional_metadata = {k: v for k, v in mem.payload.items() if k not in core_and_promoted_keys}
            if additional_metadata:
                memory_item_dict["metadata"] = additional_metadata

            formatted_memories.append(memory_item_dict)

        return formatted_memories

    async def search(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        threshold: Optional[float] = None,
    ):
        """根据查询检索记忆。

        Args:
            query (str): 要检索的查询文本。
            user_id (str, optional): 要检索的用户 ID。默认 None。
            agent_id (str, optional): 要检索的 agent ID。默认 None。
            run_id (str, optional): 要检索的 run ID。默认 None。
            limit (int, optional): 限制返回结果数量。默认 100。
            filters (dict, optional): 检索时应用的过滤条件。默认 None。
            threshold (float, optional): 可接受的最大距离（越小越严格）。默认 None。
                ChromaDB 默认 L2 距离，越小越相似；为 None 时不过滤。

        Returns:
            dict: 包含检索结果的字典，一般在 "results" 键下；若启用图谱存储还会带 "relations"。
                  v1.1+ 示例：`{"results": [{"id": "...", "memory": "...", "score": 0.8, ...}]}`
        """

        _, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_filters=filters
        )

        if not any(key in effective_filters for key in ("user_id", "agent_id", "run_id")):
            raise ValueError("at least one of 'user_id', 'agent_id', or 'run_id' must be specified ")

        vector_store_task = asyncio.create_task(self._search_vector_store(query, effective_filters, limit, threshold))

        graph_task = None
        if self.enable_graph:
            if hasattr(self.graph.search, "__await__"):  # Check if graph search is async
                graph_task = asyncio.create_task(self.graph.search(query, effective_filters, limit))
            else:
                graph_task = asyncio.create_task(asyncio.to_thread(self.graph.search, query, effective_filters, limit))

        if graph_task:
            original_memories, graph_entities = await asyncio.gather(vector_store_task, graph_task)
        else:
            original_memories = await vector_store_task
            graph_entities = None

        if self.enable_graph:
            return {"results": original_memories, "relations": graph_entities}

        return {"results": original_memories}

    async def _search_vector_store(self, query, filters, limit, threshold: Optional[float] = None):
        """把查询向量化后在 Chroma 里检索，整形成 MemoryItem，并按 threshold（距离上限）过滤。"""
        embeddings = await asyncio.to_thread(self.embedding_model.embed, query)
        memories = await asyncio.to_thread(
            self.vector_store.search, query=query, vectors=embeddings, limit=limit, filters=filters
        )

        promoted_payload_keys = [
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "role",
        ]

        core_and_promoted_keys = {"data", "hash", "created_at", "updated_at", "id", *promoted_payload_keys}

        original_memories = []
        for mem in memories:
            memory_item_dict = MemoryItem(
                id=mem.id,
                memory=mem.payload["data"],
                hash=mem.payload.get("hash"),
                created_at=mem.payload.get("created_at"),
                updated_at=mem.payload.get("updated_at"),
                score=mem.score,
            ).model_dump()

            for key in promoted_payload_keys:
                if key in mem.payload:
                    memory_item_dict[key] = mem.payload[key]

            additional_metadata = {k: v for k, v in mem.payload.items() if k not in core_and_promoted_keys}
            if additional_metadata:
                memory_item_dict["metadata"] = additional_metadata

            # ChromaDB 返回的是距离（越小越相似），threshold 当作距离上限，<= 才是"足够相似"。
            if threshold is None or mem.score <= threshold:
                original_memories.append(memory_item_dict)

        return original_memories

    async def update(self, memory_id, data):
        """异步按 ID 更新一条记忆。

        Args:
            memory_id (str): 要更新的记忆 ID。
            data (str): 用于覆盖的新内容。

        Returns:
            dict: 表示更新成功的提示信息。

        Example:
            await m.update(memory_id="mem_123", data="Likes to play tennis on weekends")
            {'message': 'Memory updated successfully!'}
        """

        embeddings = await asyncio.to_thread(self.embedding_model.embed, data)
        existing_embeddings = {data: embeddings}

        await self._update_memory(memory_id, data, existing_embeddings)
        return {"message": "Memory updated successfully!"}

    async def delete(self, memory_id):
        """异步按 ID 删除一条记忆。

        Args:
            memory_id (str): 要删除的记忆 ID。
        """
        await self._delete_memory(memory_id)
        return {"message": "Memory deleted successfully!"}

    async def delete_all(self, user_id=None, agent_id=None, run_id=None):
        """异步删除符合条件的全部记忆。

        Args:
            user_id (str, optional): 要删除其记忆的用户 ID。默认 None。
            agent_id (str, optional): 要删除其记忆的 agent ID。默认 None。
            run_id (str, optional): 要删除其记忆的 run ID。默认 None。
        """
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id

        if not filters:
            raise ValueError(
                "At least one filter is required to delete all memories. If you want to delete all memories, use the `reset()` method."
            )

        memories = await asyncio.to_thread(self.vector_store.list, filters=filters)

        delete_tasks = []
        for memory in memories[0]:
            delete_tasks.append(self._delete_memory(memory.id))

        await asyncio.gather(*delete_tasks)

        logger.info(f"Deleted {len(memories[0])} memories")

        if self.enable_graph:
            await asyncio.to_thread(self.graph.delete_all, filters)

        return {"message": "Memories deleted successfully!"}

    async def history(self, memory_id):
        """异步获取某条记忆的变更历史。

        Args:
            memory_id (str): 要查询历史的记忆 ID。

        Returns:
            list: 该记忆的变更记录列表。
        """
        return await asyncio.to_thread(self.db.get_history, memory_id)

    async def _create_memory(self, data, existing_embeddings, metadata=None):
        """新增一条记忆：写入向量库，同时往 MySQL 历史表记一条 ADD。"""
        logger.debug(f"Creating memory with {data=}")
        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = await asyncio.to_thread(self.embedding_model.embed, data)

        memory_id = str(uuid.uuid4())
        metadata = metadata or {}
        metadata["data"] = data
        metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        # Chroma payload 只接受 JSON 原生类型，所以存 ISO；MySQL DateTime 列直接收 datetime 对象。
        now = datetime.now(pytz.timezone("Asia/Shanghai"))
        metadata["created_at"] = now.isoformat()

        await asyncio.to_thread(
            self.vector_store.insert,
            vectors=[embeddings],
            ids=[memory_id],
            payloads=[metadata],
        )

        await asyncio.to_thread(
            self.db.add_history,
            memory_id,
            None,
            data,
            "ADD",
            created_at=now,
            actor_id=metadata.get("actor_id"),
            role=metadata.get("role"),
        )

        return memory_id

    async def _create_procedural_memory(self, messages, metadata=None, llm=None, prompt=None):
        """异步创建一条程序记忆。

        Args:
            messages (list): 用于生成程序记忆的消息列表。
            metadata (dict): 用于生成程序记忆的元数据。
            llm (llm, optional): 生成程序记忆使用的 LLM。默认 None。
            prompt (str, optional): 生成程序记忆使用的 prompt。默认 None。
        """
        try:
            from langchain_core.messages.utils import (
                convert_to_messages,  # type: ignore
            )
        except Exception:
            logger.error(
                "Import error while loading langchain-core. Please install 'langchain-core' to use procedural memory."
            )
            raise

        logger.info("Creating procedural memory")

        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {"role": "user", "content": "Create procedural memory of the above conversation."},
        ]

        try:
            if llm is not None:
                parsed_messages = convert_to_messages(parsed_messages)
                response = await asyncio.to_thread(llm.invoke, input=parsed_messages)
                procedural_memory = response.content
            else:
                procedural_memory = await asyncio.to_thread(self.llm.invoke, input=parsed_messages, config=None)
        except Exception as e:
            logger.error(f"Error generating procedural memory summary: {e}")
            raise

        if metadata is None:
            raise ValueError("Metadata cannot be done for procedural memory.")

        metadata["memory_type"] = MemoryType.PROCEDURAL.value
        embeddings = await asyncio.to_thread(self.embedding_model.embed, procedural_memory)
        memory_id = await self._create_memory(procedural_memory, {procedural_memory: embeddings}, metadata=metadata)

        result = {"results": [{"id": memory_id, "memory": procedural_memory, "event": "ADD"}]}

        return result

    async def _update_memory(self, memory_id, data, existing_embeddings, metadata=None):
        """更新一条记忆：覆盖向量库里的向量与 payload，同时往历史表记一条 UPDATE。"""
        logger.info(f"Updating memory with {data=}")

        try:
            existing_memory = await asyncio.to_thread(self.vector_store.get, vector_id=memory_id)
        except Exception:
            logger.error(f"Error getting memory with ID {memory_id} during update.")
            raise ValueError(f"Error getting memory with ID {memory_id}. Please provide a valid 'memory_id'")

        prev_value = existing_memory.payload.get("data")

        new_metadata = deepcopy(metadata) if metadata is not None else {}

        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        new_metadata["created_at"] = existing_memory.payload.get("created_at")
        updated_now = datetime.now(pytz.timezone("Asia/Shanghai"))
        new_metadata["updated_at"] = updated_now.isoformat()

        if "user_id" in existing_memory.payload:
            new_metadata["user_id"] = existing_memory.payload["user_id"]
        if "agent_id" in existing_memory.payload:
            new_metadata["agent_id"] = existing_memory.payload["agent_id"]
        if "run_id" in existing_memory.payload:
            new_metadata["run_id"] = existing_memory.payload["run_id"]

        if "actor_id" in existing_memory.payload:
            new_metadata["actor_id"] = existing_memory.payload["actor_id"]
        if "role" in existing_memory.payload:
            new_metadata["role"] = existing_memory.payload["role"]

        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = await asyncio.to_thread(self.embedding_model.embed, data)

        await asyncio.to_thread(
            self.vector_store.update,
            vector_id=memory_id,
            vector=embeddings,
            payload=new_metadata,
        )
        logger.info(f"Updating memory with ID {memory_id=} with {data=}")

        await asyncio.to_thread(
            self.db.add_history,
            memory_id,
            prev_value,
            data,
            "UPDATE",
            created_at=None,  # 老 memory 的 created_at 是字符串，写历史不再回传，避免 DateTime 列拿到 str
            updated_at=updated_now,
            actor_id=new_metadata.get("actor_id"),
            role=new_metadata.get("role"),
        )
        return memory_id

    async def _delete_memory(self, memory_id):
        """删除一条记忆：从向量库移除，同时往历史表记一条 DELETE（is_deleted=True）。"""
        logger.info(f"Deleting memory with {memory_id=}")
        existing_memory = await asyncio.to_thread(self.vector_store.get, vector_id=memory_id)
        prev_value = existing_memory.payload["data"]

        await asyncio.to_thread(self.vector_store.delete, vector_id=memory_id)
        await asyncio.to_thread(
            self.db.add_history,
            memory_id,
            prev_value,
            None,
            "DELETE",
            actor_id=existing_memory.payload.get("actor_id"),
            role=existing_memory.payload.get("role"),
            is_deleted=True,
        )

        return memory_id


memory_client = AsyncMemory()
