"""RAG 检索消融评估。

四个配置逐级叠加，看每个组件的贡献：
  A 单路向量      纯向量 top-k（无 BM25 / RRF / rerank / 改写）
  B +BM25 RRF     向量 + ES 关键词 双路 RRF 融合（无 rerank）
  C +rerank       B 上加 cross-encoder 重排
  D +多语改写     C 上加 多语 query 改写（= 线上完整管线）

指标：Hit@5、MRR@10，按 bucket(教材/口语化/指南/跨语言) 分别报 + 整体。
判定：gold chunk_id 是否落在检索结果里（gen_golden 已锁定 gold）。

读 AnTang/eval/golden.jsonl，须与 gen_golden 同一索引快照（中间别重建）。
需要 ES → 在后端容器内跑：
    docker compose exec backend uv run python -m AnTang.scripts.eval_rag
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from loguru import logger

from AnTang.settings import init_app_settings

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden.jsonl"
CONFIGS = ["A", "B", "C", "D"]
LABELS = {"A": "A 单路向量", "B": "B +BM25 RRF", "C": "C +rerank", "D": "D +多语改写"}
BUCKETS = ["__all__", "textbook", "colloquial", "guideline", "crosslingual"]
BNAMES = {"__all__": "全部", "textbook": "教材", "colloquial": "口语化", "guideline": "指南", "crosslingual": "跨语言"}


def hit_at_k(ranked: list[str], gold: set, k: int = 5) -> float:
    return 1.0 if any(c in gold for c in ranked[:k]) else 0.0


def mrr(ranked: list[str], gold: set, k: int = 10) -> float:
    for i, c in enumerate(ranked[:k]):
        if c in gold:
            return 1.0 / (i + 1)
    return 0.0


def _mean(pairs, idx):
    return sum(p[idx] for p in pairs) / len(pairs) if pairs else None


def _table(results, metric_idx, title):
    print(f"\n===== {title} =====")
    print("配置".ljust(14) + "".join(BNAMES[b].rjust(9) for b in BUCKETS))
    for c in CONFIGS:
        cells = []
        for b in BUCKETS:
            v = _mean(results[c][b], metric_idx)
            cells.append(f"{v:.3f}" if v is not None else "-")
        print(LABELS[c].ljust(14) + "".join(s.rjust(9) for s in cells))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只评估前 N 条(调试)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    await init_app_settings()
    from AnTang.services.antang.knowledge import ensure_default_knowledge
    from AnTang.services.rag.handler import RagHandler
    from AnTang.services.rag.vector_stores import milvus_client

    kb = await ensure_default_knowledge()
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        golden = golden[: args.limit]
    logger.info(f"评估 {len(golden)} 条 golden，知识库 {kb}")

    async def retrieve(cfg: str, q: str) -> list[str]:
        if cfg == "A":
            return [d.chunk_id for d in await milvus_client.search(q, kb, top_k=10)]
        if cfg == "B":
            docs = await RagHandler.mix_retrival_documents([q], [kb], "content")
            return [d.chunk_id for d in docs[:10]]
        r = await RagHandler._rank_for_query(q, [kb], min_score=0, top_k=10, needs_query_rewrite=(cfg == "D"))
        return [d["chunk_id"] for d in r["final"]]

    results = {c: defaultdict(list) for c in CONFIGS}  # results[cfg][bucket] = [(hit5, mrr), ...]
    wins: list[tuple[str, str]] = []  # A 漏、D 中 的定性案例
    sem = asyncio.Semaphore(args.concurrency)
    done = 0

    async def eval_one(item: dict) -> None:
        nonlocal done
        q, gold, bucket = item["query"], set(item["gold_chunk_ids"]), item["bucket"]
        async with sem:
            ranked = {}
            for c in CONFIGS:
                try:
                    ranked[c] = await retrieve(c, q)
                except Exception as e:
                    logger.warning(f"[{c}] '{q[:20]}' 失败: {e}")
                    ranked[c] = []
        for c in CONFIGS:
            pair = (hit_at_k(ranked[c], gold), mrr(ranked[c], gold))
            results[c][bucket].append(pair)
            results[c]["__all__"].append(pair)
        if hit_at_k(ranked["A"], gold) == 0 and hit_at_k(ranked["D"], gold) == 1:
            wins.append((bucket, q))
        done += 1
        if done % 25 == 0:
            logger.info(f"  已评估 {done}/{len(golden)}")

    await asyncio.gather(*[eval_one(it) for it in golden])

    counts = {b: len(results["A"][b]) for b in BUCKETS}
    print(f"\n样本量: {counts}")
    _table(results, 0, "Hit@5")
    _table(results, 1, "MRR@10")

    a5, d5 = _mean(results["A"]["__all__"], 0), _mean(results["D"]["__all__"], 0)
    am, dm = _mean(results["A"]["__all__"], 1), _mean(results["D"]["__all__"], 1)
    print(f"\n>>> 整体 A→D：Hit@5 {a5:.3f}→{d5:.3f}（+{(d5-a5):.3f}）  MRR {am:.3f}→{dm:.3f}（+{(dm-am):.3f}）")

    print(f"\n===== 定性：单路向量漏、完整管线命中 的案例(前 8/{len(wins)}) =====")
    for bucket, q in wins[:8]:
        print(f"  [{bucket}] {q}")


if __name__ == "__main__":
    asyncio.run(main())
