"""Run the opt-in, destructive-only-to-temporary-banks Hindsight acceptance set."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from hindsight_client import Hindsight


RETAIN_CONTEXT = "安糖心语的陪伴对话。助手内容只用于理解用户话语，不能当作用户事实。"


def require_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


async def retain(
    client: Hindsight,
    *,
    bank_id: str,
    document_id: str,
    content: str,
    timestamp: datetime,
) -> None:
    response = await client.aretain(
        bank_id=bank_id,
        content=content,
        timestamp=timestamp,
        context=RETAIN_CONTEXT,
        document_id=document_id,
        metadata={"source": "antang_hindsight_acceptance"},
        update_mode="replace",
        retain_async=False,
    )
    if not response.success or response.var_async or response.items_count != 1:
        raise AssertionError(f"Hindsight 没有同步写入 {document_id}")


async def recall(client: Hindsight, *, bank_id: str, query: str) -> list[str]:
    response = await client.arecall(
        bank_id=bank_id,
        query=query,
        types=["world", "experience", "observation"],
        max_tokens=2_048,
        budget="low",
        trace=False,
        query_timestamp=datetime.now(timezone.utc).isoformat(),
        include_entities=False,
        include_chunks=False,
        include_source_facts=False,
        prefer_observations=True,
    )
    return [result.text.strip() for result in response.results if result.text.strip()]


def assert_contains(results: list[str], *choices: str) -> None:
    text = "\n".join(results)
    if not any(choice in text for choice in choices):
        raise AssertionError(f"召回结果缺少任一关键词：{choices!r}\n实际结果：{text}")


async def run() -> None:
    suffix = uuid4().hex[:12]
    primary_bank = f"antang-acceptance-primary-{suffix}"
    first_user_bank = f"antang-acceptance-first-{suffix}"
    second_user_bank = f"antang-acceptance-second-{suffix}"
    banks = (primary_bank, first_user_bank, second_user_bank)
    client = Hindsight(
        base_url=require_environment("HINDSIGHT_BASE_URL"),
        api_key=require_environment("HINDSIGHT_API_KEY"),
        timeout=float(os.getenv("HINDSIGHT_TIMEOUT_SECONDS", "300")),
        user_agent="antang-hindsight-acceptance",
    )
    started_at = datetime.now(timezone.utc) - timedelta(days=2)

    try:
        await retain(
            client,
            bank_id=primary_bank,
            document_id="short-reply",
            content=(
                "助手：你说睡前担心低血糖时会打开“小海豚白噪音”，对吗？\n用户：对"
            ),
            timestamp=started_at,
        )
        short_reply = await recall(
            client,
            bank_id=primary_bank,
            query="用户睡前担心低血糖时会打开什么声音？",
        )
        assert_contains(short_reply, "小海豚", "白噪音")
        print("PASS  短回复保留了助手问题中的语境")

        await retain(
            client,
            bank_id=primary_bank,
            document_id="old-habit",
            content="用户：我以前每晚睡前会吃两块苏打饼干。",
            timestamp=started_at + timedelta(hours=1),
        )
        await retain(
            client,
            bank_id=primary_bank,
            document_id="corrected-habit",
            content=(
                "助手：你现在还会每晚睡前吃两块苏打饼干吗？\n"
                "用户：不会了，我刚才说的是以前，现在不这样做。"
            ),
            timestamp=started_at + timedelta(hours=2),
        )
        correction = await recall(
            client,
            bank_id=primary_bank,
            query="用户现在每晚睡前还会吃苏打饼干吗？",
        )
        assert_contains(correction, "不会", "不再", "不吃", "停止", "否认", "现在不")
        for result in correction:
            if (
                "苏打饼干" in result
                and "吃" in result
                and not any(
                    qualifier in result
                    for qualifier in ("不", "否认", "以前", "过去", "曾经", "停止")
                )
            ):
                raise AssertionError(f"召回了没有历史或否定限定的旧习惯：{result}")
        print("PASS  当前纠正和否定能被召回")

        assistant_guess = (
            "助手：我猜用户每天早餐都会喝蜂蜜水。\n用户：不是，我从来不喝蜂蜜水。"
        )
        await retain(
            client,
            bank_id=primary_bank,
            document_id="assistant-guess",
            content=assistant_guess,
            timestamp=started_at + timedelta(hours=3),
        )
        # replace 重试同一个编号，Hindsight 中仍应只有一份 document。
        await retain(
            client,
            bank_id=primary_bank,
            document_id="assistant-guess",
            content=assistant_guess,
            timestamp=started_at + timedelta(hours=3),
        )
        documents = await client.documents.list_documents(
            primary_bank,
            q="assistant-guess",
            limit=100,
        )
        if documents.total != 1:
            raise AssertionError(
                f"相同 document replace 两次后数量应为 1，实际为 {documents.total}"
            )
        assistant_fact = await recall(
            client,
            bank_id=primary_bank,
            query="用户早餐是否会喝蜂蜜水？",
        )
        assert_contains(assistant_fact, "不喝", "从不", "不会", "否认", "不是")
        for result in assistant_fact:
            if (
                "蜂蜜水" in result
                and "喝" in result
                and not any(
                    qualifier in result
                    for qualifier in ("不", "否认", "猜", "错误", "并非")
                )
            ):
                raise AssertionError(f"助手猜测被召回成了用户事实：{result}")
        print("PASS  助手猜测没有覆盖用户否认；replace 没有重复 document")

        await retain(
            client,
            bank_id=first_user_bank,
            document_id="relaxing-sound",
            content="用户：我最喜欢的放松声音叫“青竹雨声”。",
            timestamp=started_at,
        )
        await retain(
            client,
            bank_id=second_user_bank,
            document_id="relaxing-sound",
            content="用户：我最喜欢的放松声音叫“海盐风铃”。",
            timestamp=started_at,
        )
        first_user_memory = await recall(
            client,
            bank_id=first_user_bank,
            query="用户最喜欢哪一种放松声音？",
        )
        second_user_memory = await recall(
            client,
            bank_id=second_user_bank,
            query="用户最喜欢哪一种放松声音？",
        )
        assert_contains(first_user_memory, "青竹雨声")
        assert_contains(second_user_memory, "海盐风铃")
        if any("海盐风铃" in result for result in first_user_memory) or any(
            "青竹雨声" in result for result in second_user_memory
        ):
            raise AssertionError("两个用户的 bank 出现了交叉召回")
        print("PASS  不同用户的记忆相互隔离")

        proactive_memory = await recall(
            client,
            bank_id=primary_bank,
            query=(
                "准备一次日常关怀：用户睡前担心低血糖时，"
                "以前确认过哪一种声音能陪伴自己？"
            ),
        )
        assert_contains(proactive_memory, "小海豚", "白噪音")
        print("PASS  主动关怀可以从同一用户 bank 召回相关记忆")
    finally:
        cleanup_failures: list[str] = []
        for bank_id in banks:
            try:
                await client.adelete_bank(bank_id)
            except Exception as error:
                cleanup_failures.append(f"{bank_id} ({type(error).__name__})")
        await client.aclose()
        if cleanup_failures:
            raise AssertionError("临时 bank 清理失败：" + "、".join(cleanup_failures))


if __name__ == "__main__":
    asyncio.run(run())
