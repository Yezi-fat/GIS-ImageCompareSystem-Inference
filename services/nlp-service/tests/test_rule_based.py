"""P-023 验收：规则模板降级——固定句式可解析、要素别名归一、期次抽取。"""
from nlp_service.nlp.gazetteer import load_gazetteer
from nlp_service.nlp.rule_based import extract_by_rules

GAZ = load_gazetteer()


def test_standard_sentence():
    """验收例句："识别四川成都金牛区xx街道的建筑群"。"""
    result = extract_by_rules("识别四川成都金牛区xx街道的建筑群", GAZ)
    assert result["intent"] == "feature_extraction"
    assert result["location_text"] == "成都市金牛区" or result["location_text"] in GAZ
    assert "建筑" in result["elements"]      # 别名归一：建筑群→建筑
    # 行政区词不进入要素候选（名词候选兜底需剔除，防 unsupported 噪声）
    assert "四川" not in result["elements"] and "街道" not in result["elements"]
    assert result["periods"] == []
    assert result["confidence"] == 0.5       # 规则版能力受限，低置信度强制确认


def test_comparison_with_periods():
    result = extract_by_rules("对比2015年和2019年成都金牛区的森林变化", GAZ)
    assert result["intent"] == "feature_comparison"
    assert result["periods"] == ["2015", "2019"]
    assert "森林" in result["elements"]


def test_temporal_intent():
    result = extract_by_rules("分析成都2015到2019年草地演变过程", GAZ)
    assert result["intent"] == "temporal_analysis"
    assert result["periods"] == ["2015", "2019"]


def test_no_intent_no_location():
    """意图与位置均缺失 → 由 parser 判 LOCATION_UNRESOLVED。"""
    result = extract_by_rules("随便看看", GAZ)
    assert result["intent"] is None and result["location_text"] is None


def test_alias_normalization():
    """别名/修饰语归一（评审 D-02）：雪山→雪山覆盖、树林→森林。"""
    result = extract_by_rules("识别金牛区的雪山和树林", GAZ)
    assert "雪山覆盖" in result["elements"] and "森林" in result["elements"]
