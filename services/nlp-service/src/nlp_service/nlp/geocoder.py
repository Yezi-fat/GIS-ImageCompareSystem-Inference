"""地理编码适配（设计 §3.4.2，FR-9.2，P-022 实现）。

provider 由配置切换（amap / baidu / nominatim）；配置完整 URL（约束 #10），
仅按各 provider 协议拼接 query 参数，不假设路径结构；
公网可达走高德/百度，内网离线自部署 Nominatim + 行政区划数据；
支持到街道级；多候选返回交由用户选择。

验收口径（评审 T-02）：以 fixture/respx 打桩验证三适配器协议正确性，
真实服务联调为可选验收项。
"""
from __future__ import annotations

import httpx

from nlp_service.config import settings


class GeocodeError(Exception):
    """地理编码失败（服务不可达/无结果/响应非法）——由 parser 按部分成功降级处理（FR-9.8）。"""


def geocode(location_text: str) -> dict:
    """地名 → {bbox, confidence, candidates}；多候选 → candidates 列表（FR-9.2）。

    失败抛 GeocodeError，由 parser 按部分成功降级（location=null），
    本函数不直接决定整体成败。
    """
    if not settings.nlp.geocoder_url:
        raise GeocodeError("地理编码服务未配置（geocoder_url 为空）")
    provider = settings.nlp.geocoder_provider
    if provider == "amap":
        return _geocode_amap(location_text)
    if provider == "baidu":
        return _geocode_baidu(location_text)
    return _geocode_nominatim(location_text)


def _get(params: dict) -> dict:
    """按 provider 协议拼接 query 参数发起 GET（完整 URL 不拼路径，约束 #10）。"""
    try:
        resp = httpx.get(settings.nlp.geocoder_url, params=params, timeout=10)
    except httpx.HTTPError as e:
        raise GeocodeError(f"地理编码请求失败：{type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise GeocodeError(f"地理编码服务返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise GeocodeError(f"地理编码响应非 JSON：{e}") from e


def _geocode_nominatim(location_text: str) -> dict:
    """Nominatim：?q=&format=json&limit=5；boundingbox=[south,north,west,east]。"""
    data = _get({"q": location_text, "format": "json", "limit": 5})
    if not data:
        raise GeocodeError(f"地名 {location_text!r} 无编码结果")
    candidates = [_nominatim_candidate(item) for item in data]
    top = candidates[0]
    return {
        "bbox": top["bbox"],
        "confidence": top["confidence"],
        "candidates": candidates[1:],  # 多候选交由用户选择（FR-9.2）
    }


def _nominatim_candidate(item: dict) -> dict:
    south, north, west, east = (float(v) for v in item["boundingbox"])
    return {
        "name": item.get("display_name", ""),
        "bbox": [west, south, east, north],
        "confidence": float(item.get("importance", 0.5)),
    }


def _geocode_amap(location_text: str) -> dict:
    """高德：?key=&address=；geocodes[].rectangle="minx,miny;maxx,maxy"。"""
    data = _get({"key": settings.nlp.geocoder_key, "address": location_text})
    if data.get("status") != "1" or not data.get("geocodes"):
        raise GeocodeError(f"高德编码失败/无结果：{data.get('info', 'unknown')}")
    candidates = []
    for g in data["geocodes"]:
        rect = g.get("rectangle")  # 行政区接口返回矩形范围
        if not rect:
            continue
        (minx, miny), (maxx, maxy) = (p.split(",") for p in rect.split(";"))
        candidates.append({
            "name": g.get("formatted_address", location_text),
            "bbox": [float(minx), float(miny), float(maxx), float(maxy)],
            "confidence": 0.8,
        })
    if not candidates:
        raise GeocodeError(f"高德返回结果无范围信息：{location_text!r}")
    return {"bbox": candidates[0]["bbox"], "confidence": 0.8, "candidates": candidates[1:]}


def _geocode_baidu(location_text: str) -> dict:
    """百度：?ak=&address=&output=json；result 为点坐标，bbox 由点外扩（精度受限）。"""
    data = _get({"ak": settings.nlp.geocoder_key, "address": location_text, "output": "json"})
    if data.get("status") != 0 or not data.get("result"):
        raise GeocodeError(f"百度编码失败/无结果：status={data.get('status')}")
    loc = data["result"]["location"]  # {"lng","lat"} 点坐标
    confidence = float(data["result"].get("confidence", 50)) / 100.0
    delta = 0.05  # 点外扩约 5km 量级（百度地理编码无范围信息，精度受限，置信度相应下调）
    bbox = [loc["lng"] - delta, loc["lat"] - delta, loc["lng"] + delta, loc["lat"] + delta]
    return {"bbox": bbox, "confidence": min(confidence, 0.6), "candidates": []}
