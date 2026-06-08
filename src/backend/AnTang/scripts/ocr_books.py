"""离线把图片版书籍 OCR 成干净 Markdown，供知识库重建消费。

只处理图片版散文书（CBT / 罗杰斯 / 糖尿病结构化教育教程）；
《中国食物成分表》走结构化数据源，不在此列。

设计要点：
- 逐页渲染成 PNG → qwen3-vl 忠实转写（跳过页眉/页脚/页码）；
- 每页结果落盘缓存（data/ocr_cache/<书>/pXXXX.md），断点续跑、改一页不用重跑整本；
- worker 池并发 + 限流 + 失败重试，渲染串行（fitz 文档非线程安全）；
- 全部页缓存齐后合并成 data/antang_knowledge_md/<书>.md。

用法：
    uv run python -m AnTang.scripts.ocr_books                 # OCR 全部 3 本
    uv run python -m AnTang.scripts.ocr_books --book 认知疗法   # 只跑某本
    uv run python -m AnTang.scripts.ocr_books --max-pages 3    # 冒烟测试（每本只跑前 3 页）
    uv run python -m AnTang.scripts.ocr_books --merge-only     # 不调模型，仅把已缓存页合并成 md
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import re
import time
from pathlib import Path

import fitz
from loguru import logger

from AnTang.settings import init_app_settings
from AnTang.core.models.manager import ModelManager

REPO_ROOT = Path(__file__).resolve().parents[4]
PDF_DIR = REPO_ROOT / "data" / "antang_knowledge_pdfs"
MD_DIR = REPO_ROOT / "data" / "antang_knowledge_md"
CACHE_DIR = REPO_ROOT / "data" / "ocr_cache"

# 仅 OCR 这几本图片版散文书；食物成分表走结构化数据源。
PROSE_BOOK_KEYWORDS = ["认知疗法", "当事人中心治疗", "糖尿病结构化教育实用教程"]

PROSE_PROMPT = """这是一本中文专业书籍的一页扫描图。请把这一页正文逐字转写成 Markdown：
- 忠实原文逐字转写，不要改写、翻译、总结或补充。
- 保留标题层级(用 #)、列表、段落。
- 跳过页眉、页脚、页码、出版信息、参考文献编号列表。
- 公式用 LaTeX。
- 只输出转写后的正文，不要任何解释。"""

_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def find_books(book_filter: str | None) -> list[Path]:
    books: list[Path] = []
    for kw in PROSE_BOOK_KEYWORDS:
        if book_filter and book_filter not in kw:
            continue
        matches = sorted(p for p in PDF_DIR.glob("*.pdf") if kw in p.name)
        if matches:
            books.append(matches[0])
        else:
            logger.warning(f"[ocr] 未找到书：{kw}")
    return books


def render_page_png(doc: fitz.Document, page_no: int, dpi: int) -> bytes:
    pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    return pix.tobytes("png")


def _coerce_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text.strip()


async def ocr_one(model, png: bytes) -> str:
    b64 = base64.b64encode(png).decode()
    resp = await model.ainvoke(
        [
            {"role": "system", "content": [{"type": "text", "text": "你是专业的文档 OCR 转写助手。"}]},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": PROSE_PROMPT},
                ],
            },
        ]
    )
    return strip_fence(_coerce_text(resp.content))


async def ocr_book(pdf_path: Path, *, dpi: int, concurrency: int, max_pages: int | None) -> None:
    stem = pdf_path.stem
    cache = CACHE_DIR / stem
    cache.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    total = len(doc)
    last = total if max_pages is None else min(max_pages, total)

    pending = [i for i in range(last) if not (cache / f"p{i:04d}.md").exists()]
    logger.info(f"[ocr] {stem}: 共 {total} 页，本次待处理 {len(pending)} 页（已缓存 {last - len(pending)}）")

    model = ModelManager.get_qwen_vl_model()
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in pending:
        queue.put_nowait(i)
    render_lock = asyncio.Lock()
    done = 0
    t0 = time.time()

    async def worker(wid: int) -> None:
        nonlocal done
        while True:
            try:
                i = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with render_lock:  # fitz 文档非线程安全，渲染串行
                    png = await asyncio.to_thread(render_page_png, doc, i, dpi)
                text = ""
                for attempt in range(3):
                    try:
                        text = await ocr_one(model, png)
                        break
                    except Exception as err:
                        logger.warning(f"[ocr] {stem} p{i} 第{attempt + 1}次失败：{err}")
                        await asyncio.sleep(2 * (attempt + 1))
                if not text:
                    logger.error(f"[ocr] {stem} p{i} 重试耗尽，留待下次续跑")
                else:
                    (cache / f"p{i:04d}.md").write_text(text, encoding="utf-8")
                done += 1
                if done % 10 == 0 or done == len(pending):
                    rate = done / max(time.time() - t0, 1e-6)
                    eta = (len(pending) - done) / max(rate, 1e-6)
                    logger.info(f"[ocr] {stem}: {done}/{len(pending)} 页，{rate:.2f} 页/s，ETA {eta/60:.1f} 分钟")
            finally:
                queue.task_done()

    if pending:
        await asyncio.gather(*[asyncio.create_task(worker(w)) for w in range(concurrency)])
    doc.close()
    merge_book(pdf_path, last)


def merge_book(pdf_path: Path, last: int) -> None:
    stem = pdf_path.stem
    cache = CACHE_DIR / stem
    parts: list[str] = []
    missing = 0
    for i in range(last):
        f = cache / f"p{i:04d}.md"
        if f.exists():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                parts.append(t)
        else:
            missing += 1
    MD_DIR.mkdir(parents=True, exist_ok=True)
    out = MD_DIR / f"{stem}.md"
    out.write_text("\n\n".join(parts), encoding="utf-8")
    flag = f"（缺 {missing} 页未 OCR）" if missing else ""
    logger.info(f"[ocr] 合并完成 → {out}（{len(parts)} 页有内容{flag}）")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", default=None, help="只跑包含该关键字的书")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-pages", type=int, default=None, help="每本最多处理前 N 页（冒烟测试）")
    parser.add_argument("--merge-only", action="store_true", help="只合并已缓存页，不调模型")
    args = parser.parse_args()

    await init_app_settings()
    books = find_books(args.book)
    if not books:
        logger.error("[ocr] 没有匹配到任何书")
        return
    logger.info(f"[ocr] 待处理 {len(books)} 本：{[b.name for b in books]}")

    for pdf_path in books:
        if args.merge_only:
            doc = fitz.open(pdf_path)
            n = len(doc)
            doc.close()
            last = n if args.max_pages is None else min(args.max_pages, n)
            merge_book(pdf_path, last)
        else:
            await ocr_book(pdf_path, dpi=args.dpi, concurrency=args.concurrency, max_pages=args.max_pages)


if __name__ == "__main__":
    asyncio.run(main())
