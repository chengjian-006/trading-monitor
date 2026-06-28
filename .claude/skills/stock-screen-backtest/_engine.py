# -*- coding: utf-8 -*-
"""选股+回测 skill 引擎 —— 薄壳, 真身在 backend/services/backtester_5m.py(网页API与skill共用一份)。

历史上 5 分钟可成交口径引擎写在这里; v1.7.x 抽成正式后端服务后, 本文件只做 re-export +
构造 MODELS/MODEL_BY_ID(已解析 cfg/s0 的 model 对象), 保持 screen.py/backtest.py 等脚本接口不变。
"""
import os
import sys

# 项目根: .claude/skills/stock-screen-backtest/_engine.py → 上 3 级
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.backtester_5m import (  # noqa: E402
    MIN_BARS, DEDUP_DAYS, FEE, MODEL_IDS, MODEL_NAMES, build_model,
    daily_could_fire, load_daily_one, load_daily_many, load_5m_one, universe_codes,
    fire_5m, entry_price, simulate_exit, stat, fmt_stat,
    run_model_backtest, run_backtest_5m,
)

# 已解析(cfg/s0 填好)的 model 对象列表, 供脚本直接 iterate/查 id
MODELS = [build_model(mid) for mid in MODEL_IDS]
MODEL_BY_ID = {m["id"]: m for m in MODELS}
