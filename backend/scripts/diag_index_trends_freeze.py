"""指数分时冻结回放实时核查 — 一次性诊断脚本(只读, 不写库)。

实拉 get_index_trends 用的真源(东财→同花顺兜底), 打印每个A股指数分时末点时间
与当前时刻的滞后(交易分钟), 判定是否仍在冻结回放(末点停在15:00=隔日残留)。
跑法(项目根目录): py -3 -m backend.scripts.diag_index_trends_freeze
"""
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.services import ai_analyst
from backend.core.trading_calendar import trading_minute, trends_stale, is_continuous_auction

now_hhmm = datetime.now().strftime("%H:%M")
print(f"当前时刻: {now_hhmm}  连续竞价时段: {is_continuous_auction()}")
print("-" * 60)

# 绕过清洗, 直接看原始源吐什么(清洗会把陈旧的置空, 看不到末点)
secid_map = {
    "1.000001": ("sh000001", "上证指数", "hs_1A0001"),
    "0.399001": ("sz399001", "深证成指", "hs_399001"),
    "0.399006": ("sz399006", "创业板指", "hs_399006"),
    "1.000688": ("sh000688", "科创指数", "hs_1B0688"),
    "0.399317": ("sz399317", "全A指数", "hs_399317"),
}
for secid, (code, name, ths_code) in secid_map.items():
    fetched = ai_analyst._retry_with_fallback(
        lambda s=secid: ai_analyst._fetch_single_trend(s),
        lambda tc=ths_code: ai_analyst._fetch_single_trend_ths(tc),
        lambda r: bool(r) and len(r.get("trends", [])) > 0,
        f"trend_{code}",
    )
    trends = (fetched or {}).get("trends", [])
    if not trends:
        print(f"{name:6s} {code}: 无数据(源全失败)")
        continue
    last_t = trends[-1].get("time")
    n = len(trends)
    try:
        lag = abs(trading_minute(now_hhmm) - trading_minute(last_t))
    except Exception:
        lag = "?"
    stale = trends_stale(trends, now_hhmm)
    flag = "❌冻结回放" if stale else "✅新鲜"
    print(f"{name:6s} {code}: 点数={n:3d} 末点={last_t} 滞后={lag}分钟 {flag}")
