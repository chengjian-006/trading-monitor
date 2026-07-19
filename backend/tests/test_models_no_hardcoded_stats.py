# -*- coding: utf-8 -*-
"""模型图鉴文案不得写死战绩数字 (v1.7.708) — 静态扫描, 不连库不起前端.

背景: models.ts 的 traits/caveats 里曾写死各模型胜率与盈利因子(平台突破写「实测胜率74%/
盈利因子4.03」), 而同一页的胜率榜是每晚 21:00 重算的实时值(当时 54.0%/近6月48.8%) ——
**同屏两个数字差 20 多个百分点**。战绩一律走实时表, 静态文案只留定性判断。

本测试是守门闸: 以后再往文案里写死"胜率XX%""盈利因子X.XX"会直接挂测试。
术语解释(METRIC_TIPS)里的举例数字是讲概念用的, 不是战绩断言, 故豁免。
"""
import re
from pathlib import Path

import pytest

_MODELS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "models.ts"

# 战绩型断言: 胜率/盈利因子/PF 后面直接跟数字
_PATTERNS = [
    (re.compile(r"胜率\s*[:：]?\s*\d"), "胜率+数字"),
    (re.compile(r"盈利因子\s*[:：]?\s*\d"), "盈利因子+数字"),
    (re.compile(r"\bPF\s*\d"), "PF+数字"),
]


def _stat_fields() -> list[tuple[int, str]]:
    """取 traits / caveats / oneLine / scope 这些展示给用户的文案行(带行号)。"""
    if not _MODELS_TS.exists():
        pytest.skip(f"未找到 {_MODELS_TS}")
    out = []
    in_tips = False
    for i, line in enumerate(_MODELS_TS.read_text(encoding="utf-8").splitlines(), 1):
        # METRIC_TIPS 是术语解释, 里面的数字是举例(如"盈利因子2.09 = 赚的是亏的2.09倍"), 豁免
        if "METRIC_TIPS" in line:
            in_tips = True
        if in_tips:
            if line.strip().startswith("}"):
                in_tips = False
            continue
        if re.search(r"\b(traits|caveats|oneLine|scope)\s*:", line):
            out.append((i, line))
    return out


def test_no_hardcoded_winrate_or_pf_in_model_copy():
    """模型文案里不得出现写死的胜率/盈利因子数字。"""
    offenders = []
    for lineno, line in _stat_fields():
        for pat, label in _PATTERNS:
            if pat.search(line):
                offenders.append(f"models.ts:{lineno} [{label}] {line.strip()[:110]}")
    assert not offenders, (
        "模型文案里写死了战绩数字, 会与每晚重算的实时胜率表打架。\n"
        "改法: 定量走卡片顶部「实时战绩」带(wrOf 读 cfzy_biz_model_winrate), 文案只留定性。\n"
        + "\n".join(offenders)
    )


def test_scanner_actually_matches_known_bad_pattern():
    """反向自检: 扫描器本身要能抓到典型违例, 否则这条守门闸是空的。"""
    bad = "traits: ['全市场半年回测盈利因子3.0-3.3,自选池2026实测胜率74%/盈利因子4.03']"
    assert any(p.search(bad) for p, _ in _PATTERNS), "扫描器抓不到已知违例, 规则写坏了"


def test_metric_tips_examples_are_exempt():
    """术语解释里的举例数字(讲概念用)不该被误伤。"""
    tips_line = "'例:盈利因子2.09 = 赚的钱是亏的钱的2.09倍(每亏1块、赚回2.09块)。\\n' +"
    assert any(p.search(tips_line) for p, _ in _PATTERNS), "该行确实含数字"
    # 但它在 METRIC_TIPS 段内, _stat_fields 不会把它收进来
    assert all("METRIC_TIPS" not in line for _n, line in _stat_fields())
