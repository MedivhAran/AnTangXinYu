"""Markdown 噪声清洗：进切块前去掉参考文献段、作者/样板信息、页码行。

PDF（pymupdf4llm 转出）和图片书（OCR 转出）都汇流到 markdown_parser，
所以清洗放在这里一处即可覆盖两条来源。

设计偏保守，宁可少删也不误删正文：
- 参考文献段：从"参考文献/References/Bibliography"这一行开始跳过，直到遇到下一个
  Markdown 标题（章节）或文末。这样能处理一篇里多章节各带一段参考文献的情况。
- 样板行：作者/单位/通信作者/收稿日期/基金项目/DOI/邮箱等整行删除。
- 页码行：整行只有数字或"- 12 -"形式删除。
"""

from __future__ import annotations

import re

# 整行就是"参考文献 / References / Bibliography"（允许 markdown #、加粗、空格、冒号）
_REF_HEADING = re.compile(
    r"^\s*#{0,6}\s*\**\s*(参\s*考\s*文\s*献|references?|bibliography)\s*\**\s*[:：]?\s*$",
    re.IGNORECASE,
)
# 下一个 Markdown 标题，用来界定参考文献段的结束
_HEADING = re.compile(r"^\s*#{1,6}\s+\S")
# 期刊编辑部样板段标题（读者·作者·编者 / 利益冲突 / 作者贡献 / 志谢），同样整段跳过
_NOISE_SECTION = re.compile(
    r"^\s*#{0,6}\s*\**\s*[·•\s]*(读\s*者[·•\s]*作\s*者[·•\s]*编\s*者|利益冲突|作者贡献|志\s*谢|致\s*谢)[·•\s]*\**\s*[:：]?\s*$",
)
# 中文学术样板行（整行起始即匹配）
_BOILERPLATE = re.compile(
    r"^\s*("
    r"(通讯|通信|第一)?作者(单位|简介|贡献)?\s*[:：]"
    r"|作者单位\s*[:：]"
    r"|收稿日期|修回日期|接受日期|出版日期"
    r"|基金项目|利益冲突|志\s*谢|致\s*谢"
    r"|doi\s*[:：]"
    r"|https?://doi\.org"
    r")",
    re.IGNORECASE,
)
# GB/T 引文标记（[J]/[M]/[C]/[D]/[S]/[EB/OL] 等）或内联 DOI：这类几乎只出现在参考文献，
# 用来兜住"没有'参考文献'标题、直接用引文格式"的文档（行内任意位置命中即删该行）。
_CITATION = re.compile(r"\[(J|M|C|D|S|R|EB|G)(/OL)?\]|doi\s*[:：]|doi\.org", re.IGNORECASE)
# 纯邮箱行（作者联系方式）
_EMAIL_LINE = re.compile(r"^\s*[\w.\-+]+@[\w.\-]+\.\w+\s*$")
# 纯页码行：只有数字，或形如 "- 12 -" / "第 12 页"
_PAGE_NUM = re.compile(r"^\s*(第\s*)?[-—·.\s]*\d{1,4}[-—·.\s]*(页)?\s*$")


def clean_markdown(text: str) -> str:
    """删除参考文献段、样板行、页码行，返回清洗后的 markdown。"""
    if not text:
        return text

    out: list[str] = []
    skip_refs = False
    for line in text.split("\n"):
        if skip_refs:
            # 参考文献段内：遇到下一节标题则停止跳过并保留该标题，否则继续丢弃
            if _HEADING.match(line):
                skip_refs = False
            else:
                continue
        if _REF_HEADING.match(line) or _NOISE_SECTION.match(line):
            skip_refs = True
            continue
        if _BOILERPLATE.match(line) or _EMAIL_LINE.match(line) or _PAGE_NUM.match(line):
            continue
        if _CITATION.search(line):  # GB/T 引文行 / 内联 DOI
            continue
        out.append(line)

    # 折叠清洗后留下的多余空行
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
