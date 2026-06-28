# -*- coding: utf-8 -*-
"""回踩MA10/MA20「放量确认双闸」盘后实盘观察诊断 — 只读, 不写库不部署。

验证 v1.7.462/463 双闸上线后两件回测看不到的事(回测用全天真实量, 绕开了盘中U型外推):
  ① 突破即报: 信号是否在突破点附近就报(不追高)。看 触发价 vs 突破线(昨高×1.025) 的追高度。
  ② 放量确认会不会误挡真突破: 盘中放量倍数靠U型外推, 外推偏小可能把真突破挡掉。
     用"全天真实量过检测器=该不该报"与"实盘实际报了没"对比, 捞 疑似误挡/误报。

【A 已触发复核】当天 BUY_RALLY_MA10/MA20 信号: 触发时刻/触发价/追高度 + 触发时外推放量倍数 vs 收盘实际放量倍数(外推准不准)。
【B 全池误挡/误报扫描】对自选池每只票用收盘后全天真实日线过生产检测器→"该报集"; 与实盘"已报集"对差:
     该报却没报=疑似误挡(外推挡掉真突破 / 或晚盘突破 / 或不在扫描池 / 或去重已持仓, 需逐一甄别);
     报了却EOD不该报=疑似误报(盘中外推过头)。

跑法(项目根目录, 周一收盘后): py -3 -m backend.scripts.diag_rally_gate_live [YYYY-MM-DD]
默认= 本机今天。务必收盘(15:00后)再跑, 否则当天日线是半截。
"""
import asyncio
import json
import re
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.models.database import init_db
from backend.models.repo import _db
from backend.models.repo.stocks import list_quotable_codes
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.signal_engine_detectors import _detect_rally_ma20_pullback
from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG

NOPROXY = {"http": None, "https": None}
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
MA10_CFG = dict(DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA10"])
MA20_CFG = dict(DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA20"])
AVG_WIN = 10
MODELS = [("BUY_RALLY_MA10", "回踩MA10", MA10_CFG), ("BUY_RALLY_MA20", "回踩MA20", MA20_CFG)]
MODEL_NAME = {sid: nm for sid, nm, _ in MODELS}


def sina_sym(code):
    return ("sh" if code[0] in ("6", "9", "5") else "sz") + code


def fetch_daily(code):
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData"
           f"?symbol={sina_sym(code)}&scale=240&ma=no&datalen=120")
    for attempt in range(2):
        try:
            r = requests.get(url, headers=H, proxies=NOPROXY, timeout=12)
            t = r.text; s, e = t.find("("), t.rfind(")")
            if s < 0 or e <= s:
                return code, None
            data = json.loads(t[s + 1:e])
            if not data:
                return code, None
            df = pd.DataFrame(data).rename(columns={"day": "date"})
            for c in ("open", "high", "low", "close", "volume"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df[["date", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
            return code, df
        except Exception:
            if attempt == 0:
                continue
            return code, None
    return code, None


def fetch_pool(codes):
    out = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(fetch_daily, c) for c in codes]):
            code, df = fut.result()
            if df is not None and len(df) >= 30:
                out[code] = df
    return out


def eod_eval(df, the_date):
    """对一只票收盘后全天真实日线, 返回 (该报哪些model, 当日实际放量倍数, 昨高, 今日high/close)。
    要求 df 最后一行就是 the_date(收盘后已含当天)。"""
    if df.empty or str(df["date"].iloc[-1])[:10] != the_date:
        return None
    ind = compute_indicators(df)
    ind["amount_est"] = ind["volume"] * ind["close"]
    if len(ind) < 26:
        return None
    latest = ind.iloc[-1]
    vol = ind["volume"].values
    avg10 = vol[-(AVG_WIN + 1):-1].mean() if len(vol) > AVG_WIN else 0
    actual_mult = (vol[-1] / avg10) if avg10 > 0 else 0.0
    prev_high = float(ind["high"].iloc[-2])
    should = []
    for sid, _, cfg in MODELS:
        if _detect_rally_ma20_pullback(ind, latest, cfg) is not None:
            should.append(sid)
    return dict(should=should, actual_mult=actual_mult, prev_high=prev_high,
                today_high=float(latest["high"]), today_close=float(latest["close"]))


async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    the_date = arg or datetime.now().strftime("%Y-%m-%d")
    await init_db()

    # ---- 拉池子 + 全池收盘日线 ----
    codes = await list_quotable_codes()
    name_rows = await _db._fetchall("SELECT code, name FROM cfzy_biz_stock_pool WHERE deleted_at IS NULL")
    name_map = {r["code"]: (r["name"] or "") for r in name_rows}
    print(f"自选池可报价票 {len(codes)} 只, 拉收盘日线中 ...", flush=True)
    kl = fetch_pool(codes)
    print(f"  拿到 {len(kl)} 只日线 (当天={the_date})")

    # ---- A 已触发复核 ----
    rows = await _db._fetchall(
        "SELECT code, name, signal_id, signal_name, price, detail, triggered_at "
        "FROM cfzy_biz_signals WHERE DATE(triggered_at)=%s AND direction='buy' "
        "AND signal_id IN ('BUY_RALLY_MA10','BUY_RALLY_MA20') "
        "ORDER BY triggered_at ASC", (the_date,))
    print("\n" + "=" * 104)
    print(f"【A 已触发复核】{the_date}  回踩MA10/MA20 共 {len(rows)} 条")
    print("=" * 104)
    fired = set()
    if not rows:
        print("(当天无回踩MA10/MA20 触发)")
    else:
        print(f"{'时刻':<9}{'代码':<8}{'名称':<8}{'模型':<9}{'触发价':>8}{'突破线':>8}{'追高度':>7}"
              f"{'外推倍':>7}{'实际倍':>7}{'外推误差':>8}{'触发距日高':>9}")
        print("-" * 104)
        for r in rows:
            code = r["code"]; fired.add((code, r["signal_id"]))
            t = str(r["triggered_at"])[11:19]
            det = r["detail"] or ""
            m_trig = re.search(r"触发价([\d.]+)", det)
            m_mult = re.search(r"放量([\d.]+)倍", det)
            trig_px = float(m_trig.group(1)) if m_trig else (r["price"] or 0)
            proj_mult = float(m_mult.group(1)) if m_mult else None
            ev = eod_eval(kl.get(code, pd.DataFrame()), the_date)
            if ev:
                brk = ev["prev_high"] * 1.025
                chase = (trig_px / brk - 1) * 100 if brk > 0 else 0      # 追高度: 触发价高于突破线%
                from_hi = (trig_px / ev["today_high"] - 1) * 100         # 触发价距当日最高%(负=在高点下方报)
                amult = ev["actual_mult"]
                err = ((proj_mult - amult) / amult * 100) if (proj_mult and amult > 0) else None
                errs = f"{err:+.0f}%" if err is not None else "--"
                pm = f"{proj_mult:.2f}" if proj_mult else "--"
                print(f"{t:<9}{code:<8}{(r['name'] or '')[:6]:<8}{r['signal_name'][:7]:<9}"
                      f"{trig_px:>8.2f}{brk:>8.2f}{chase:>+6.1f}%{pm:>7}{amult:>7.2f}{errs:>8}{from_hi:>+8.1f}%")
            else:
                print(f"{t:<9}{code:<8}{(r['name'] or '')[:6]:<8}{r['signal_name'][:7]:<9}"
                      f"{trig_px:>8.2f}{'(无当日EOD日线, 跳过对比)':>40}")
        print("\n看点: 追高度越接近0越好(突破即报不追高); 外推误差越接近0说明U型外推越准; 触发距日高为负=在当日高点下方报(早).")

    # ---- B 全池误挡/误报扫描 ----
    should_map = {}   # (code, sid) -> actual_mult
    detail_map = {}
    for code, df in kl.items():
        ev = eod_eval(df, the_date)
        if not ev or not ev["should"]:
            continue
        for sid in ev["should"]:
            should_map[(code, sid)] = ev["actual_mult"]
        detail_map[code] = ev
    should_set = set(should_map.keys())

    miss = should_set - fired       # 该报却没报 = 疑似误挡
    false_fire = fired - should_set # 报了却EOD不该报 = 疑似误报

    print("\n" + "=" * 104)
    print(f"【B 误挡/误报扫描】全池按收盘全天真实量过检测器 → 该报集 {len(should_set)} 条")
    print("=" * 104)
    print(f"\n▶ 疑似误挡(全天真实量该报、实盘没报) {len(miss)} 条 —— 重点逐一甄别是否被外推挡掉:")
    if not miss:
        print("  (无 — 双闸没有漏掉任何全天该报的突破, 理想结果)")
    for (code, sid) in sorted(miss):
        ev = detail_map[code]
        brk = ev["prev_high"] * 1.025
        print(f"  {code} {name_map.get(code,''):<6} {MODEL_NAME[sid]:<8} "
              f"全天实际放量{should_map[(code,sid)]:.2f}倍 突破线{brk:.2f} 今收{ev['today_close']:.2f} 今高{ev['today_high']:.2f}")
    print("    甄别清单: ①是否晚盘(14:30后)才突破→外推已准但时间晚 ②是否不在实盘扫描池/停牌 "
          "③是否当天已持仓/10日内已报被去重 ④真·早盘外推偏小被挡(=需要关注的误挡)")

    print(f"\n▶ 疑似误报(实盘报了、EOD全天真实量不该报) {len(false_fire)} 条 —— 看是否盘中外推过头:")
    if not false_fire:
        print("  (无 — 没有外推过头的误报, 理想结果)")
    for (code, sid) in sorted(false_fire):
        ev = detail_map.get(code)
        amult = f"{ev['actual_mult']:.2f}倍" if ev else "EOD不达标"
        print(f"  {code} {name_map.get(code,''):<6} {MODEL_NAME[sid]:<8} 全天实际放量{amult}(<1.5×即外推高估误报)")

    print("\n完成. 这是只读诊断, 未写库未部署.")


if __name__ == "__main__":
    asyncio.run(main())
