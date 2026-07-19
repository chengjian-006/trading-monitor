"""交易复盘事实清单构造器(纯函数, 不连库不碰LLM): 把交易回合算成结构化真数字, 交给 ai_client 写人话。
四类: 听模型vs自作主张 / 按买点模型归因成绩 / 盈亏持仓周期 / 买卖习惯坏毛病。"""
from collections import defaultdict


def _rate(wins: int, n: int) -> float:
    return round(wins / n * 100, 1) if n else 0.0


def _avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def _closed(rounds: list[dict]) -> list[dict]:
    return [r for r in rounds if str(r.get("status")) == "closed" and r.get("realized_pnl_pct") is not None]


def build_coach_facts(rounds: list[dict], winrate: dict, start: str, end: str) -> dict:
    cl = _closed(rounds)
    # 名称→全市场近3月胜率(model_winrate 值以 signal_id 为键, 取 model_name 反查)
    mkt = {v.get("model_name"): v for v in (winrate or {}).values()}

    # 1) 听模型 vs 自作主张
    def group_stats(rs):
        pcts = [float(r["realized_pnl_pct"]) for r in rs]
        return {"n": len(rs), "win_rate": _rate(sum(1 for p in pcts if p > 0), len(rs)),
                "avg_pnl_pct": _avg(pcts)}
    listen = [r for r in cl if r.get("entry_model_name")]
    myself = [r for r in cl if not r.get("entry_model_name")]

    # 2) 按买点模型归因
    by_model = []
    grp = defaultdict(list)
    for r in listen:
        grp[r["entry_model_name"]].append(float(r["realized_pnl_pct"]))
    for name, pcts in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        m = mkt.get(name) or {}
        wr = _rate(sum(1 for p in pcts if p > 0), len(pcts))
        mwr = m.get("win_rate_3m")
        by_model.append({
            "model_name": name, "n": len(pcts), "win_rate": wr, "avg_pnl_pct": _avg(pcts),
            "market_win_rate_3m": mwr,
            "exec_gap": round(wr - mwr, 1) if mwr is not None else None,
        })

    # 3) 盈亏/持仓周期
    winners = [r for r in cl if float(r["realized_pnl_pct"]) > 0]
    losers = [r for r in cl if float(r["realized_pnl_pct"]) <= 0]
    hold = [int(r["holding_days"]) for r in cl if r.get("holding_days") is not None]
    cycle = {
        "hold_days_avg": _avg([float(h) for h in hold]),
        "winner_hold_avg": _avg([float(r["holding_days"]) for r in winners if r.get("holding_days") is not None]),
        "loser_hold_avg": _avg([float(r["holding_days"]) for r in losers if r.get("holding_days") is not None]),
        "pnl_dist": {
            "best_pct": round(max((float(r["realized_pnl_pct"]) for r in cl), default=0.0), 2),
            "worst_pct": round(min((float(r["realized_pnl_pct"]) for r in cl), default=0.0), 2),
            "avg_pct": _avg([float(r["realized_pnl_pct"]) for r in cl]),
        },
    }

    # 4) 习惯坏毛病(能从回合列直接算的先做; 追高/卖飞需 leg/K线, Phase1.5 补, 见 spec 开放点)
    habits = {
        "winner_hold_avg": cycle["winner_hold_avg"],
        "loser_hold_avg": cycle["loser_hold_avg"],
        "loser_holds_longer": cycle["loser_hold_avg"] > cycle["winner_hold_avg"],  # 输家扛更久=手输家倾向
        "scaled_out_ratio": _rate(sum(1 for r in cl if r.get("is_scaled_out")), len(cl)),
        "stop_loss_rounds": _rate(
            sum(1 for r in cl if str(r.get("exit_reason") or "").startswith("SELL_") and float(r["realized_pnl_pct"]) < 0),
            len(cl)),
    }

    return {
        "window": {"start": start, "end": end},
        "n_closed": len(cl),
        "listen_vs_self": {"listen": group_stats(listen), "self": group_stats(myself)},
        "by_model": by_model,
        "cycle": cycle,
        "habits": habits,
    }
