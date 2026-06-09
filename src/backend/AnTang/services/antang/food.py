"""《中国食物成分表》营养成分 + 升糖指数(GI) 本地查询。

数据来源：https://github.com/Sanotsu/china-food-composition-data
（源自《中国食物成分表 标准版·第6版》，营养值为每 100g 可食部）。

纯内存查询：食物名做归一化后按"完全/前缀/包含"打分匹配，营养表与 GI 表各查一路再合并。
营养表里很多是生料（如"稻米"），GI 表里多是熟食/制品（如"大米饭"），两路互补。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "food_data"
_MISSING = {"", "—", "-", "Tr", "tr", "TR", None}


def _norm(s: str) -> str:
    """归一化食物名：去空白/括号/标点/星号，便于宽松匹配。"""
    return re.sub(r"[\s（）()【】\[\]*·,，。、/]+", "", str(s)).lower()


# 名字里跟在查询词后的"限定符"分隔符：如"西瓜（代表值）"。用来把"西瓜（…）"判得比"西瓜子"更贴近。
_SEP = set("（(【[ \t，,、/）)")


def _score(query_strip: str, query_norm: str, name_orig: str, name_norm: str) -> int:
    if not query_norm or not name_norm:
        return 0
    if query_norm == name_norm:
        return 100
    # 原名以查询词开头、且后面紧跟括号/分隔符（"西瓜（代表值）"），比"西瓜子"这种新合成词更贴近
    if name_orig.startswith(query_strip) and (
        len(name_orig) == len(query_strip) or name_orig[len(query_strip)] in _SEP
    ):
        return 95
    if name_norm.startswith(query_norm):
        return 85
    if query_norm in name_norm:
        return 70
    if name_norm in query_norm:
        return 60
    return 0


@lru_cache(maxsize=1)
def _load():
    comp = json.loads((_DATA_DIR / "composition.json").read_text(encoding="utf-8"))
    gi = json.loads((_DATA_DIR / "gi.json").read_text(encoding="utf-8"))
    for f in comp:
        f["_norm"] = _norm(f.get("foodName", ""))
    for g in gi:
        g["_norm"] = _norm(g.get("foodName", ""))
    return comp, gi


def _top(items, query, limit):
    qs = query.strip()
    qn = _norm(query)
    scored = [
        (s, len(it["foodName"]), it)
        for it in items
        if (s := _score(qs, qn, it["foodName"], it["_norm"])) > 0
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [it for _, _, it in scored[:limit]]


def _val(v, unit=""):
    return "—" if v in _MISSING else f"{v}{unit}"


def _gi_level(gi: int) -> str:
    return "高" if gi >= 70 else ("中" if gi >= 55 else "低")


def lookup_food(query: str, food_limit: int = 3, gi_limit: int = 5) -> str:
    """查询食物营养成分与 GI，返回给 Agent 使用的结构化文本。"""
    comp, gi = _load()
    foods = _top(comp, query, food_limit)
    gis = _top(gi, query, gi_limit)
    if not foods and not gis:
        return f"没有在《中国食物成分表》里找到与“{query}”匹配的食物，可以换个更常见的说法再试。"

    lines: list[str] = []
    shown_gi: set[str] = set()
    if foods:
        lines.append("【营养成分（每 100g 可食部）】")
        for f in foods:
            gi_hit = _top(gi, f["foodName"], 1)
            gi_txt = ""
            if gi_hit:
                g = gi_hit[0]
                shown_gi.add(g["foodName"])
                gi_txt = f"，GI {g['GI']}（{_gi_level(g['GI'])}升糖）"
            lines.append(
                f"- {f['foodName']}：能量 {_val(f.get('energyKCal'), ' kcal')}，"
                f"碳水 {_val(f.get('CHO'), ' g')}，蛋白质 {_val(f.get('protein'), ' g')}，"
                f"脂肪 {_val(f.get('fat'), ' g')}，膳食纤维 {_val(f.get('dietaryFiber'), ' g')}{gi_txt}"
            )

    extra = [g for g in gis if g["foodName"] not in shown_gi]
    if extra:
        lines.append("【升糖指数 GI 参考】")
        for g in extra:
            lines.append(f"- {g['foodName']}：GI {g['GI']}（{_gi_level(g['GI'])}升糖）")

    return "\n".join(lines)
