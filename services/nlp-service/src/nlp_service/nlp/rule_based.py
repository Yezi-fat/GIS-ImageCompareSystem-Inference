"""规则模板降级 NLU（设计 §3.4，FR-9.6/9.7，P-023 实现）。

jieba 分词 + 固定句式模板（"识别/对比 + 地名 + 要素[+年份]"）+ 地名词典；
仅支持固定句式并明确提示能力受限；响应标记 nlu_provider=rule-based。
要素别名/修饰语归一（"建筑群"→building）词表内置（评审 D-02）。
"""
from __future__ import annotations

import re

# 要素别名/修饰语词表（内置维护，评审 D-02）：别名/修饰语 → 标准要素表述。
# 键为自然语言表述，值为要素目录中的标准 name 或 id（由 parser 对照 catalog 归一）。
ELEMENT_ALIASES: dict[str, str] = {
    "建筑群": "建筑", "楼房": "建筑", "房屋": "建筑", "建筑物": "建筑",
    "树林": "森林", "林地": "森林", "林木": "森林",
    "草原": "草地", "草甸": "草地",
    "雪山": "雪山覆盖", "积雪": "雪山覆盖", "冰川": "雪山覆盖",
}

# 意图句式模板（FR-9.6 固定句式；顺序即优先级——多期先于双期判定）
_INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"时序|多期|演变|逐年|多年|历年来|变化过程", "temporal_analysis"),
    (r"对比|比对|差异|两期|前后", "feature_comparison"),
    (r"识别|提取|圈出|标出|找出|检测", "feature_extraction"),
]

_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")


def extract_by_rules(text: str, gazetteer: dict) -> dict:
    """规则解析 → {intent, location_text, elements, periods, confidence}（nlu_provider=rule-based）。

    - 意图：句式模板匹配，未命中 → None（交由 parser 判 LOCATION_UNRESOLVED）；
    - 地名：地名词典最长匹配；未命中退回正则提取行政区后缀词（"xx区/xx街道"）；
    - 要素：词表与别名的原文命中（标准名归一在 parser 对照 catalog 完成）；
    - 期次：文本中的年份序列（如“2015 到 2019”→ ["2015","2019"]）；
    - confidence：规则解析能力受限（FR-9.6），恒 0.5（低于 0.7 → parser 标注不确定字段）。
    """
    intent = None
    for pattern, intent_type in _INTENT_PATTERNS:
        if re.search(pattern, text):
            intent = intent_type
            break

    location_text = _match_gazetteer(text, gazetteer)

    elements = _extract_elements(text, location_text, gazetteer)
    periods = _YEAR_PATTERN.findall(text)  # 如“2015 到 2019”→ ["2015", "2019"]

    return {
        "intent": intent,
        "location_text": location_text,
        "elements": elements,
        "periods": periods,
        "confidence": 0.5,
    }


def _match_gazetteer(text: str, gazetteer: dict) -> str | None:
    """地名词典最长匹配；未命中时以行政区后缀词兜底（词典未覆盖的地名仍交地理编码）。"""
    best = None
    for name in gazetteer:
        if name in text and (best is None or len(name) > len(best)):
            best = name
    if best:
        return best
    m = re.search(r"[一-龥]{2,}(?:省|市|区|县|街道|镇|乡)", text)
    return m.group(0) if m else None


def _extract_elements(
    text: str,
    location_text: str | None = None,
    gazetteer: dict | None = None,
) -> list[str]:
    """要素表述抽取：别名表与标准名直命中（归一在 parser）+ 名词候选兜底。

    名词候选（jieba 词性标注 n 系）兜底未收录表述（如“水稻田”），
    由 parser 对照 catalog 判 unsupported（FR-9.3 不静默忽略）；
    行政区词（含地名词典成员与“省/市/区/街道”类后缀词）不是要素，须剔除。
    """
    import jieba.posseg

    hits: list[str] = []
    # 别名表优先（长词先匹配，避免“建筑”先于“建筑群”命中）
    for alias in sorted(ELEMENT_ALIASES, key=len, reverse=True):
        if alias in text:
            standard = ELEMENT_ALIASES[alias]
            if standard not in hits:
                hits.append(standard)
    # 常见要素标准名直命中
    for word in ("森林", "草地", "建筑", "水体", "雪山覆盖"):
        if word in text and word not in hits:
            hits.append(word)

    # 名词候选兜底：过滤意图词/期次/地名后的名词视为要素表述候选
    stop_words = {
        "识别", "提取", "对比", "比对", "分析", "检测", "圈出", "标出", "找出",
        "影像", "地图", "区域", "范围", "变化", "差异", "演变", "过程", "时序",
    }
    admin_suffixes = ("省", "市", "区", "县", "街道", "镇", "乡", "道")
    gazetteer = gazetteer or {}
    for word, flag in jieba.posseg.cut(text):
        if not flag.startswith("n") or len(word) < 2 or word in stop_words:
            continue
        if _YEAR_PATTERN.fullmatch(word) or word.endswith(admin_suffixes):
            continue
        if location_text and (word in location_text or location_text in word):
            continue
        if any(word in name or name in word for name in gazetteer):
            continue  # 地名词典成员（如“四川”⊂“四川省”）非要素
        if word not in hits and word not in ELEMENT_ALIASES.values():
            hits.append(word)
    return hits
