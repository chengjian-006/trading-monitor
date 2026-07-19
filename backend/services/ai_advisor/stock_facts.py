"""个股研判事实清单构造器(纯函数, 不连库不碰LLM): 把已gather的各源数据组装成结构化真数字。
摆事实不预测: 同形态胜率是历史客观分布, 非涨跌预测。"""
import json
import re

_SUFFIX = re.compile(r"（[左右]侧）$")


def _norm_model(name: str) -> str:
    """Normalize signal_name by removing (左侧)/(右侧) suffix."""
    return _SUFFIX.sub("", str(name or "")).strip()


def build_stock_facts(code, name, *, signals, winrate, fin_risk, sector, holding, near_buy) -> dict:
    """Build individual stock fact sheet from gathered data.

    Args:
        code: Stock code
        name: Stock name
        signals: List of signal events (from signal history)
        winrate: Model winrate dict from get_model_winrate()
        fin_risk: Financial risk dict from get_fin_risk(code) or None
        sector: Sector context (board_strength, sector_rank, theme_heat list)
        holding: Current position dict or None
        near_buy: Near-buy trigger dict or None

    Returns:
        Structured fact sheet with normalized data and flags for missing data.
    """
    sigs = signals or []

    # 1) Signal history: keep recent 10, normalize names
    recent = [{"signal_name": s.get("signal_name"), "date": str(s.get("trigger_date") or "")[:10],
               "direction": s.get("direction")} for s in sigs[:10]]

    # 2) Model winrate: backfill from signal history (buy signals only)
    #该票历史出现过的买点模型 → 反查全市场同形态胜率
    mkt = {v.get("model_name"): v for v in (winrate or {}).values()}
    seen, models = set(), []
    for s in sigs:
        if s.get("direction") != "buy":
            continue
        mn = _norm_model(s.get("signal_name"))
        if mn and mn in mkt and mn not in seen:
            seen.add(mn)
            m = mkt[mn]
            models.append({"model_name": mn, "win_rate_3m": m.get("win_rate_3m"), "n_3m": m.get("n_3m")})

    # 3) Financial risk flags
    if fin_risk:
        try:
            _flags = json.loads(fin_risk.get("flags_json") or "[]")
        except (ValueError, TypeError):
            _flags = []
        risk = {"has_data": True, "score": fin_risk.get("score"), "flags": _flags}
    else:
        risk = {"has_data": False}

    # 4) Sector context
    sec = sector or {}
    sector_out = {"board_strength": sec.get("board_strength"), "sector_rank": sec.get("sector_rank"),
                  "hot_themes": [t.get("theme") if isinstance(t, dict) else t for t in (sec.get("theme_heat") or [])][:5]}

    # 5) Holding position
    hold = {"is_holding": True, "cost": holding.get("cost"), "float_pct": holding.get("float_pct"),
            "entry_model": holding.get("entry_model")} if holding else {"is_holding": False}

    # 6) Near-buy status
    nb = {"approaching": True, "model": near_buy.get("model"), "gap_pct": near_buy.get("gap_pct")} \
        if near_buy else {"approaching": False}

    return {"code": code, "name": name,
            "signal_history": {"recent": recent, "n": len(sigs)},
            "model_winrate": models, "risk_flags": risk, "sector": sector_out,
            "holding": hold, "near_buy": nb}
