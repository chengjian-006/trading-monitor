"""个股买卖点卡片"背景标签"融合 (v1.7.x).

在买卖点推送卡里融合两类背景信息(只标注不拦截, 用户自己判断):
  🚩/⚠️ 黑天鹅 — 财务红旗评分(≥中危50) / 近14天风险公告(商誉/问询/减持…)
  📈 业绩预增 — 近期正向业绩预告(预增/略增/扭亏等) + 变动幅度

设计: 纯函数 build_background_tags 构标签(可单测); fetch_background 查三张表(集成层)。
风险优先看 → 黑天鹅在预增前。财务红旗<50(关注级)不上标签, 防噪。
"""
from __future__ import annotations

FIN_RISK_MIN_SHOW = 50       # 财务红旗达此分才上标签(中危+; 关注级<50不显, 防噪)
RISK_ANN_RECENT_DAYS = 14    # 风险公告近 N 天才上标签(旧消息不扰)


def _fin_level(score: int) -> str:
    """复用 financial_risk_scanner 的告警级别口径: 高危≥70 / 中危50-69 / 关注<50。"""
    from backend.services.financial_risk_scanner import risk_level
    label, _emoji = risk_level(score)
    return label


def build_background_tags(*, forecast: dict | None, fin_risk: dict | None,
                          risk_anns: list[dict], bold: bool) -> list[str]:
    """构建背景标签行(lark_md, 微信也能渲染纯文本)。风险类在前, 预增在后; 无则空 list。

    forecast: {predict_type, amp_lower, amp_upper} 或 None(应已是正向"利好"组)
    fin_risk: {score, ...} 或 None(<FIN_RISK_MIN_SHOW 不显)
    risk_anns: [{tags, title, ann_date}] 近14天风险公告(调用方已按时效过滤)
    """
    b = (lambda s: f"**{s}**") if bold else (lambda s: str(s))
    tags: list[str] = []

    # ── 黑天鹅族(风险优先看, 排前) ──
    # 风险公告(取最近一条, 优先 tags 简述, 无则截 title)
    if risk_anns:
        a = risk_anns[0]
        desc = (a.get("tags") or "").strip() or (a.get("title") or "").strip()[:16]
        d = str(a.get("ann_date") or "")[:10]
        mmdd = d[5:] if len(d) >= 10 else d
        suffix = f"（{mmdd}）" if mmdd else ""
        tags.append(f"⚠️ {b('黑天鹅·风险公告')}：{desc}{suffix}")
    # 财务红旗(≥中危才显)
    if fin_risk:
        score = int(fin_risk.get("score") or 0)
        if score >= FIN_RISK_MIN_SHOW:
            level = _fin_level(score)
            tags.append(f"🚩 {b(f'黑天鹅·{level}')}：财务红旗评分{b(score)}")

    # ── 业绩预增(顺风, 排后) ──
    if forecast:
        ptype = (forecast.get("predict_type") or "预告").strip()
        lo, hi = forecast.get("amp_lower"), forecast.get("amp_upper")
        amp = ""
        if lo is not None and hi is not None:
            amp = f" {b(f'+{float(lo):.0f}%~{float(hi):.0f}%')}"
        elif hi is not None:
            amp = f" {b(f'+{float(hi):.0f}%')}"
        tags.append(f"📈 {b('业绩预增')}：{ptype}{amp}")

    return tags


def render_tags_text(tags: list[str]) -> str:
    """标签行合并为一段(每行一个); 空则空串。"""
    return "\n".join(tags) if tags else ""


async def fetch_background(code: str) -> dict:
    """查三张表拿背景数据: 正向业绩预告 + 财务红旗 + 近14天风险公告。失败项静默略过。"""
    import re
    out: dict = {"forecast": None, "fin_risk": None, "risk_anns": []}
    if not (code and re.match(r"^\d{6}$", code)):
        return out
    try:
        from backend.models.repo import earnings as _earn
        out["forecast"] = await _earn.get_positive_forecast_by_code(code)
    except Exception:
        pass
    try:
        from backend.models.repo import fin_risk as _fr
        out["fin_risk"] = await _fr.get_fin_risk(code)
    except Exception:
        pass
    try:
        from backend.models.repo import risk_ann as _ra
        out["risk_anns"] = await _ra.get_recent_risk_anns_by_code(code, RISK_ANN_RECENT_DAYS)
    except Exception:
        pass
    return out
