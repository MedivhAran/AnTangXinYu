"""生成 RAG 评估的 golden 标注集。

判定方式：不靠"挑独特文字"，而是在生成时直接锁定答案所在的 chunk，记录其 chunk_id 当 gold。
评估时 hit@k = gold chunk_id 是否落在 top-k。gen 与 eval 须在同一索引快照上。

四个来源（按可信度/难度搭配）：
  ① textbook   教材问答（主力，真实患者问句，零 LLM、无循环论证）：解析教材 `## N. 问题？`，
               在向量库里按 header_path 锁定答案 chunk 当 gold（答案可能跨多个 chunk）。
  ② colloquial 口语化改写（硬样本）：把部分教材问句改成病人日常口吻，gold 不变 → 词面不重合，
               考验混合检索 / rerank。
  ③ guideline  指南 LLM 生成题：从中文指南 chunk 生成自然问句，gold = 该 chunk（补内容覆盖）。
  ④ crosslingual 跨语言：从英文 standards chunk 生成中文问句，gold = 该英文 chunk，考验多语改写。

走 Milvus 读取向量库，本地即可运行（②③④需要对话模型）。
输出: AnTang/eval/golden.jsonl，每行 {query, gold_chunk_ids, gold_contents, source_file, bucket}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from pathlib import Path

# 路径按 cwd 相对解析，与 app 约定一致（容器内 cwd=/app，本地从 src/backend 起跑）。
TEXTBOOK_MD = Path(__file__).resolve().parents[4] / "data" / "antang_knowledge_md" / "糖尿病结构化教育实用教程.md"
OUT = Path(__file__).resolve().parents[1] / "eval" / "golden.jsonl"

TEXTBOOK_KEY = "结构化教育"
BOOK_KEYS = ["结构化教育", "认知疗法", "当事人中心治疗"]  # OCR 散文书
STANDARDS_KEY = "standards-of-care"  # 英文

_QUESTION_RE = re.compile(r"^#{2,3}\s*\d+\.\s*(.+)$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _write(golden: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for g in golden:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")


def _header_path(content: str) -> str:
    return content.split("\n\n", 1)[0]


def _body(content: str) -> str:
    parts = content.split("\n\n", 1)
    return parts[1] if len(parts) > 1 else content


def _is_chinese(text: str) -> bool:
    """按正文里中文字符 vs 英文字母判断语言（不靠文件名，因为有些中文名文件正文是英文）。"""
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk >= latin


def load_chunks_from_milvus(knowledge_id: str) -> list[dict]:
    """从 Milvus 向量库读取所有 chunk（本地需能连 Milvus standalone）。"""
    from AnTang.services.rag.vector_stores import milvus_client

    collection = milvus_client._get_collection_safe(knowledge_id)
    if collection is None:
        print(f"Milvus 集合不存在: {knowledge_id}，请确认向量库已建好。")
        return []

    # Milvus 单次 query 上限 16384，用 query_iterator 分批读全量
    rows = []
    it = collection.query_iterator(
        batch_size=2000,
        expr="chunk_id != ''",
        output_fields=["chunk_id", "content", "file_name"],
    )
    try:
        while True:
            batch = it.next()
            if not batch:
                break
            rows.extend(batch)
    finally:
        it.close()
    return [
        {"chunk_id": r.get("chunk_id", ""), "content": r.get("content", ""), "file_name": r.get("file_name", "")}
        for r in rows
        if r.get("chunk_id")
    ]


# ---------- ① 教材问答（主力，无 LLM）----------
def build_textbook_golden(chunks: list[dict]) -> tuple[list[dict], list[str]]:
    tb = [c for c in chunks if TEXTBOOK_KEY in c["file_name"]]
    # 只在 header_path 里匹配：问题作为小节标题 → 该节答案 chunk 才是 gold；目录页(问题在正文)不会误中。
    index = [(_norm(_header_path(c["content"])), c) for c in tb]
    questions = []
    for line in TEXTBOOK_MD.read_text(encoding="utf-8").splitlines():
        m = _QUESTION_RE.match(line.strip())
        if m and (m.group(1).strip().endswith("？") or m.group(1).strip().endswith("?")):
            questions.append(m.group(1).strip())

    golden, unmatched = [], []
    for q in questions:
        qn = _norm(q)
        gold = [c for cn, c in index if qn in cn]
        if gold:
            golden.append(_entry(q, gold, "textbook"))
        else:
            unmatched.append(q)
    return golden, unmatched


def _entry(query: str, gold: list[dict], bucket: str) -> dict:
    return {
        "query": query,
        "gold_chunk_ids": [c["chunk_id"] for c in gold],
        "gold_contents": [c["content"] for c in gold],
        "source_file": gold[0]["file_name"],
        "bucket": bucket,
    }


# ---------- ②③④ LLM 生成 ----------
def _sample(chunks: list[dict], *, exclude=(), chinese=None, n=0, min_body=250, seed=42) -> list[dict]:
    pool = [
        c
        for c in chunks
        if not any(e in c["file_name"] for e in exclude)
        and "目录" not in _header_path(c["content"])
        and len(_body(c["content"])) >= min_body
        and (chinese is None or _is_chinese(_body(c["content"])) == chinese)
    ]
    random.Random(seed).shuffle(pool)
    return pool[:n]


_BAD = ("资料", "文中", "上述", "本段", "该段", "根据上", "作者", "公司", "赛诺菲", "诺和", "礼来", "经费", "基金", "参考文献", "本文")


def _bad_question(q: str) -> bool:
    return (not q) or len(q) < 6 or any(b in q for b in _BAD)


async def _gen_one(model, sem, prompt: str) -> str:
    async with sem:
        resp = await model.ainvoke(prompt)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return text.strip().strip("\"'“”").splitlines()[0].strip() if text.strip() else ""


_COLLOQUIAL_P = "把下面这个糖尿病相关的问题改写成病人日常口语化的说法：意思不变，但不要堆砌书面术语，像真人随口问。只输出改写后的问题。\n原问题：{q}"
_GUIDELINE_P = (
    "下面是一段糖尿病诊疗资料。请提一个该段内容能回答的、关于糖尿病医学知识的自然中文问题。\n"
    "要求：像真实患者或医生提问；问题能独立成立；不要出现'资料/文中/上述/根据'等字眼；"
    "不要问公司、作者、经费、参考文献等无关信息。只输出问题本身。\n资料：{c}"
)
_CROSSLINGUAL_P = (
    "Below is an excerpt from English diabetes clinical standards. Ask ONE natural, standalone question "
    "IN CHINESE that this excerpt answers (patient or clinician voice). Do NOT mention 'the excerpt/text', "
    "and do NOT ask about companies, authors, or funding. Output only the Chinese question.\nExcerpt: {c}"
)


async def gen_colloquial(model, sem, textbook_golden, n) -> list[dict]:
    picks = random.Random(7).sample(textbook_golden, min(n, len(textbook_golden)))
    qs = await asyncio.gather(*[_gen_one(model, sem, _COLLOQUIAL_P.format(q=g["query"])) for g in picks])
    out = []
    for g, q in zip(picks, qs):
        if q:
            out.append({**g, "query": q, "bucket": "colloquial", "origin": g["query"]})
    return out


async def gen_from_chunks(model, sem, picks, bucket, template) -> list[dict]:
    qs = await asyncio.gather(*[_gen_one(model, sem, template.format(c=_body(c["content"])[:1200])) for c in picks])
    return [_entry(q, [c], bucket) for c, q in zip(picks, qs) if not _bad_question(q)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--colloquial", type=int, default=40)
    parser.add_argument("--guideline", type=int, default=40)
    parser.add_argument("--crosslingual", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--show", type=int, default=3)
    args = parser.parse_args()

    from AnTang.settings import init_app_settings

    await init_app_settings()
    from AnTang.services.antang.knowledge import get_default_knowledge_id

    knowledge_id = await get_default_knowledge_id()
    chunks = load_chunks_from_milvus(knowledge_id)

    textbook, unmatched = build_textbook_golden(chunks)
    print(f"向量库 chunk={len(chunks)} | 教材问句匹配 gold={len(textbook)}（未匹配 {len(unmatched)}）")
    _write(textbook)  # 先把教材落盘兜底，LLM 生成慢/被杀也不至于一无所获

    llm_buckets: list[dict] = []
    if args.colloquial or args.guideline or args.crosslingual:
        from AnTang.core.models.manager import ModelManager

        model = ModelManager.get_conversation_model()
        sem = asyncio.Semaphore(args.concurrency)

        # 按正文语言路由：指南桶取中文正文（排除 3 本书），跨语言桶取英文正文
        guide_picks = _sample(chunks, exclude=BOOK_KEYS, chinese=True, n=int(args.guideline * 1.3))
        cross_picks = _sample(chunks, exclude=BOOK_KEYS, chinese=False, n=int(args.crosslingual * 1.3))
        coll, guide, cross = await asyncio.gather(
            gen_colloquial(model, sem, textbook, args.colloquial),
            gen_from_chunks(model, sem, guide_picks, "guideline", _GUIDELINE_P),
            gen_from_chunks(model, sem, cross_picks, "crosslingual", _CROSSLINGUAL_P),
        )
        llm_buckets = coll + guide + cross
        print(f"LLM 生成: colloquial={len(coll)} guideline={len(guide)} crosslingual={len(cross)}")

    golden = textbook + llm_buckets
    for bucket in ("colloquial", "guideline", "crosslingual"):
        samples = [g for g in golden if g["bucket"] == bucket][: args.show]
        if samples:
            print(f"\n===== {bucket} 抽样 =====")
            for g in samples:
                origin = f"（原:{g.get('origin','')}）" if g.get("origin") else ""
                print(f"  Q: {g['query']}{origin}")
                print(f"     gold: {g['gold_contents'][0][:90].strip()}...")

    _write(golden)
    by_bucket = {b: sum(1 for g in golden if g["bucket"] == b) for b in {x["bucket"] for x in golden}}
    print(f"\n已写入 {len(golden)} 条 → {OUT}  分桶: {by_bucket}")


if __name__ == "__main__":
    asyncio.run(main())
