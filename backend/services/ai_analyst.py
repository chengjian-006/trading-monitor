"""AI Market Analyst — gathers market data and calls LLM to generate reports."""

import asyncio
import logging
import json
import subprocess
import requests
import re
import time
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)


# max_retries=1 (v1.7.x): 主源失败立即切备用源, 不再为已挂的主源(如 EastMoney 被限流时)
# 白等 2 次重试×delay; 主源恢复后下个周期自动用回, 无需手动翻转主从。
def _retry_with_fallback(primary_fn, fallback_fn, is_valid, label: str, max_retries: int = 1, delay: float = 1.5):
    for attempt in range(1, max_retries + 1):
        try:
            result = primary_fn()
        except Exception as e:
            logger.warning(f"[{label}] 主源第{attempt}次抛异常: {e}")
            result = None
        if is_valid(result):
            return result
        if attempt < max_retries:
            logger.warning(f"[{label}] 主源第{attempt}次无效，{delay}s后重试")
            time.sleep(delay)
    logger.warning(f"[{label}] 主源{max_retries}次均失败，切换备用数据源")
    try:
        result = fallback_fn()
        if is_valid(result):
            logger.info(f"[{label}] 备用数据源成功")
            return result
    except Exception as e:
        logger.error(f"[{label}] 备用数据源也失败: {e}")
    try:
        return primary_fn()  # last-ditch
    except Exception as e:
        logger.error(f"[{label}] last-ditch 也抛异常: {e}, 返回空dict兜底")
        return {}

INDEX_CODES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创指数"),
    ("sz399317", "全A指数"),   # 国证A指(全部A股); 同花顺自有全A/中证全指新浪源取不到, 国证A指新浪现价+日K可达
]

# v1.7.84: 全球主要股指 (新浪 hq.sinajs.cn 已实测)
# 字段格式因地区而异 — _parse_global 内做归一化
# 日韩暂缺(新浪 int_nikkei225/int_kospi 返空, 等找到稳定源再补)
# v1.7.x: 美股/港股改走腾讯实时源 (qt.gtimg) —— 新浪 int_ 外盘源已冻结失效
# (盘中不跳、点位停在旧值, 实测道指 int_dji 90s 无变化且点位严重过时), 腾讯 r_us/r_hk 实时跳动。
# 腾讯指数字段(~分隔): [1]=名 [3]=现价 [30]=时间 [31]=涨跌额 [32]=涨跌幅
TENCENT_INDEX_CODES = [
    ("r_usDJI", "道琼斯", "美股"),
    ("r_usIXIC", "纳斯达克", "美股"),
    ("r_usINX", "标普500", "美股"),
    ("r_hkHSI", "恒生指数", "港股"),
    ("r_hkHSCEI", "国企指数", "港股"),
    ("r_hkHSTECH", "恒生科技", "港股"),
]
# 欧洲 + 日本: 腾讯无对应 r_ 代码, 保留新浪 (b_ 欧洲 / hf_ 期货, 与 int_ 不同源)
GLOBAL_INDEX_CODES = [
    # (sina_code, name, region, parser_type)
    # europe: 13 字段 [名,现价,涨跌额,涨跌幅,...]
    ("b_DAX", "德国DAX", "欧洲", "europe"),
    ("b_FTSE", "英富时100", "欧洲", "europe"),
    # 日本: 新浪 int_nikkei225 全空, 改用 hf_NK (日经225 期货) 作为代理
    # 字段 [现价,_,买,卖,最高,最低,时间,昨结,今开,...,名称,...]
    ("hf_NK", "日经225(期货)", "日本", "futures"),
]
# 地区展示顺序: 美股 → 欧洲 → 港股 → 日本
_REGION_ORDER = {"美股": 0, "欧洲": 1, "港股": 2, "日本": 3}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}

TIME_SLOT_NAMES = {
    "0926": "早盘概览",
    "1000": "早盘跟踪",
    "1130": "上午收盘",
    "1400": "午后分析",
    "1500": "收盘总结",
}


def _get_indices_sina() -> list[dict]:
    sina_codes = [code for code, _ in INDEX_CODES]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
    except Exception as e:
        logger.error(f"Index fetch failed (Sina): {e}")
        return []

    results = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        match = re.match(r'var hq_str_(\w+)="(.*)";?', line)
        if not match:
            continue
        sina_code = match.group(1)
        fields = match.group(2).split(",")
        name = next((n for c, n in INDEX_CODES if c == sina_code), sina_code)
        if len(fields) < 10:
            continue
        try:
            price = float(fields[3])
            pre_close = float(fields[2])
            amount = float(fields[9]) / 1e8
            pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
            results.append({"name": name, "price": price, "pct_change": pct, "amount": round(amount, 1)})
        except (ValueError, IndexError):
            continue
    return results


INDEX_SECIDS = {
    "1.000001": "上证指数",
    "0.399001": "深证成指",
    "0.399006": "创业板指",
    "1.000688": "科创指数",
}


def _get_indices_eastmoney() -> list[dict]:
    # v1.7.74: 显式打点(走 subprocess 不经 TrackedAsyncClient)
    import subprocess
    from backend.services import api_metrics
    import time as _t
    secids = ",".join(INDEX_SECIDS.keys())
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&secids={secids}&fields=f2,f3,f4,f6,f12,f14"
    )
    t0 = _t.time()
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url,
             "-H", f"User-Agent: {HEADERS['User-Agent']}",
             "-H", "Referer: https://quote.eastmoney.com"],
            capture_output=True, timeout=10,
        )
        data = json.loads(proc.stdout.decode("utf-8"))
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
        results = []
        for item in diff:
            code = str(item.get("f12", ""))
            name = str(item.get("f14", ""))
            price = item.get("f2", 0) or 0
            pct = item.get("f3", 0) or 0
            amount = (item.get("f6", 0) or 0) / 1e8
            if price > 0:
                results.append({"name": name, "price": price, "pct_change": pct, "amount": round(amount, 1)})
        api_metrics.record("eastmoney", "market_indices", bool(results),
                           int((_t.time() - t0) * 1000), "" if results else "empty result")
        return results
    except Exception as e:
        api_metrics.record("eastmoney", "market_indices", False,
                           int((_t.time() - t0) * 1000), str(e))
        logger.error(f"Index fetch failed (EastMoney): {e}")
        return []


def get_market_indices() -> list[dict]:
    # v1.7.74: 主备倒置 — 新浪为主, 东财为备
    # 东财对 prod IP 频繁断连超时, 新浪稳定且数据足够(name/price/pct/amount)
    # 同时给 api_metrics 打点反映真实业务可用性
    from backend.services import api_metrics
    import time as _t
    t0 = _t.time()
    try:
        res = _get_indices_sina()
        if res:
            api_metrics.record("sina", "market_indices", True, int((_t.time() - t0) * 1000))
            return res
        api_metrics.record("sina", "market_indices", False, int((_t.time() - t0) * 1000), "empty result")
    except Exception as e:
        api_metrics.record("sina", "market_indices", False, int((_t.time() - t0) * 1000), str(e))
    logger.warning("[indices] 新浪返回空, 切换东财备源")
    return _retry_with_fallback(
        _get_indices_eastmoney, _get_indices_sina,
        lambda r: len(r) > 0, "indices"
    )


def _fmt_global_time(date_str: str, time_str: str) -> str:
    """归一指数报价时间为 'MM-DD HH:MM'。date 支持 2026/05/29 或 2026-05-30; time 支持 HH:MM[:SS]。
    海外/港股与 A 股不同时段交易, 标出各自报价时间避免误读新鲜度。"""
    try:
        mmdd = ""
        parts = (date_str or "").replace("/", "-").split("-")
        if len(parts) == 3 and parts[1] and parts[2]:
            mmdd = f"{int(parts[1]):02d}-{int(parts[2]):02d}"
        hhmm = ""
        tp = (time_str or "").split(":")
        if len(tp) >= 2:
            hhmm = f"{int(tp[0]):02d}:{int(tp[1]):02d}"
        return (mmdd + " " + hhmm).strip()
    except (ValueError, IndexError):
        return ""


# v1.7.84: 全球主要股指获取
def _parse_global_one(sina_code: str, name: str, region: str, parser_type: str, payload: str) -> dict | None:
    """解析单条新浪外盘指数字段, 归一化为 {name,region,price,pct_change,update_time}"""
    fields = payload.split(",")
    if not fields or not fields[0]:
        return None
    try:
        if parser_type == "us":
            # [名, 现价, 涨跌额, 涨跌幅] — 新浪 int_ 无报价时间字段
            return {"name": name, "region": region,
                    "price": float(fields[1]), "pct_change": float(fields[3]), "update_time": ""}
        if parser_type == "europe":
            # [名, 现价, 涨跌额, 涨跌幅, ?, ?, 日期, 时间, ...]
            t = _fmt_global_time(fields[6] if len(fields) > 6 else "",
                                 fields[7] if len(fields) > 7 else "")
            return {"name": name, "region": region,
                    "price": float(fields[1]), "pct_change": float(fields[3]), "update_time": t}
        if parser_type == "hk":
            # [简称, 中文名, 现价, 昨收, ..., 日期(17), 时间(18)] — 涨跌幅自算
            price = float(fields[6]) if len(fields) > 6 else float(fields[2])
            pre = float(fields[3]) if len(fields) > 3 else 0
            pct = round((price - pre) / pre * 100, 2) if pre > 0 else 0
            t = _fmt_global_time(fields[17] if len(fields) > 17 else "",
                                 fields[18] if len(fields) > 18 else "")
            return {"name": name, "region": region, "price": price, "pct_change": pct, "update_time": t}
        if parser_type == "futures":
            # 新浪期货 hf_*: [现价,_,买,卖,最高,最低,时间(6),昨结(7),今开,...,日期(12),...]
            price = float(fields[0])
            pre = float(fields[7]) if len(fields) > 7 else 0
            pct = round((price - pre) / pre * 100, 2) if pre > 0 else 0
            t = _fmt_global_time(fields[12] if len(fields) > 12 else "",
                                 fields[6] if len(fields) > 6 else "")
            return {"name": name, "region": region, "price": price, "pct_change": pct, "update_time": t}
    except (ValueError, IndexError):
        return None
    return None


def _fmt_tencent_time(s: str) -> str:
    """腾讯时间 '2026-06-01 12:09:20' / '2026/06/01 18:31:25' → 'MM-DD HH:MM'。"""
    s = (s or "").replace("/", "-").strip()
    parts = s.split(" ")
    if len(parts) != 2:
        return ""
    d = parts[0].split("-")
    return f"{d[1]}-{d[2]} {parts[1][:5]}" if len(d) == 3 else parts[1][:5]


def _parse_tencent_index(payload: str, name: str, region: str) -> dict | None:
    """腾讯指数字段(~分隔): [3]=现价 [30]=时间 [32]=涨跌幅。"""
    f = payload.split("~")
    if len(f) < 33:
        return None
    try:
        price = float(f[3])
        pct = float(f[32])
    except (ValueError, IndexError):
        return None
    return {"name": name, "region": region, "price": price, "pct_change": pct,
            "update_time": _fmt_tencent_time(f[30] if len(f) > 30 else "")}


def _fetch_tencent_indices() -> dict:
    """腾讯实时美股+港股指数, 返回 {code: {...}}。"""
    from backend.services import api_metrics
    import time as _t
    codes = ",".join(c for c, _, _ in TENCENT_INDEX_CODES)
    url = f"https://qt.gtimg.cn/q={codes}"
    t0 = _t.time()
    out: dict[str, dict] = {}
    try:
        resp = requests.get(url, timeout=8)
        resp.encoding = "gbk"
        text = resp.text
    except Exception as e:
        api_metrics.record("tencent", "global_indices", False, int((_t.time() - t0) * 1000), str(e))
        logger.warning(f"[global_indices] 腾讯取数失败: {e}")
        return out
    for line in text.strip().split("\n"):
        m = re.match(r'v_(\w+)="(.*)";?', line.strip())
        if not m:
            continue
        code, payload = m.group(1), m.group(2)
        cfg = next(((n, r) for c, n, r in TENCENT_INDEX_CODES if c == code), None)
        if cfg:
            d = _parse_tencent_index(payload, cfg[0], cfg[1])
            if d:
                out[code] = d
    api_metrics.record("tencent", "global_indices", bool(out), int((_t.time() - t0) * 1000), "" if out else "empty")
    return out


def get_global_indices() -> list[dict]:
    """全球主要股指(美/欧/港/日): 美股+港股走腾讯实时, 欧洲+日本走新浪。任一源失败不阻塞其余。

    返回: [{name, region, price, pct_change, update_time}, ...] 按地区(美→欧→港→日)排序。
    (新浪 int_ 美股源已冻结失效改腾讯; 韩国 KOSPI 唯一源东财对 prod 封禁, 已放弃)
    """
    from backend.services import api_metrics
    import time as _t

    results: list[dict] = []
    # 腾讯: 美股 + 港股 (实时)
    tx = _fetch_tencent_indices()
    for code, _, _ in TENCENT_INDEX_CODES:
        if code in tx:
            results.append(tx[code])

    # 新浪: 欧洲 + 日本
    if GLOBAL_INDEX_CODES:
        codes = ",".join(c for c, _, _, _ in GLOBAL_INDEX_CODES)
        t0 = _t.time()
        try:
            resp = requests.get(f"https://hq.sinajs.cn/list={codes}", headers=HEADERS, timeout=8)
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                m = re.match(r'var hq_str_(\w+)="(.*)";?', line.strip())
                if not m:
                    continue
                sina_code, payload = m.group(1), m.group(2)
                cfg = next(((n, r, t) for c, n, r, t in GLOBAL_INDEX_CODES if c == sina_code), None)
                if cfg:
                    d = _parse_global_one(sina_code, cfg[0], cfg[1], cfg[2], payload)
                    if d:
                        results.append(d)
            api_metrics.record("sina", "global_indices_eu_jp", True, int((_t.time() - t0) * 1000), "")
        except Exception as e:
            api_metrics.record("sina", "global_indices_eu_jp", False, int((_t.time() - t0) * 1000), str(e))
            logger.warning(f"[global_indices] 新浪欧/日取数失败: {e}")

    results.sort(key=lambda x: _REGION_ORDER.get(x.get("region"), 9))
    return results


def _get_stats_eastmoney() -> dict:
    """f104=上涨家数, f105=下跌家数, f106=涨停, f108=跌停(often broken)"""
    url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get"
        "?fltt=2&fields=f104,f105,f106,f108&secids=1.000001,0.399001"
    )
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url,
             "-H", f"User-Agent: {HEADERS['User-Agent']}",
             "-H", "Referer: https://quote.eastmoney.com"],
            capture_output=True, timeout=10,
        )
        data = json.loads(proc.stdout.decode("utf-8"))
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            return {}
        up_count = sum(item.get("f104", 0) for item in diff)
        down_count = sum(item.get("f105", 0) for item in diff)
        limit_up = sum(item.get("f106", 0) for item in diff)
        limit_down = sum(item.get("f108", 0) for item in diff)
        return {
            "limit_up": limit_up,
            "limit_down": limit_down,
            "up_count": up_count,
            "down_count": down_count,
        }
    except Exception as e:
        logger.warning(f"Market stats fetch failed (EastMoney): {e}")
        return {}


def _get_limit_counts_akshare() -> tuple[int, int]:
    """Get limit_up/limit_down counts from akshare pool APIs."""
    # v1.7.71: 分别 try, 每个调用单独打点到 api_metrics
    from backend.services import api_metrics
    import time as _t
    import akshare as ak

    limit_up = 0
    t0 = _t.time()
    try:
        zt_df = ak.stock_zt_pool_em()
        limit_up = len(zt_df) if zt_df is not None else 0
        api_metrics.record("akshare", "limit_up_pool", True, int((_t.time() - t0) * 1000))
    except Exception as e:
        api_metrics.record("akshare", "limit_up_pool", False, int((_t.time() - t0) * 1000), str(e))
        logger.warning(f"akshare 涨停池失败: {e}")

    limit_down = 0
    t0 = _t.time()
    try:
        dt_df = ak.stock_zt_pool_dtgc_em()
        limit_down = len(dt_df) if dt_df is not None else 0
        api_metrics.record("akshare", "limit_down_pool", True, int((_t.time() - t0) * 1000))
    except Exception as e:
        # 跌停池的"30个交易日"是 akshare 数据限制, 视为可达(与探活容错一致)
        ok = "30" in str(e) and "交易日" in str(e)
        api_metrics.record("akshare", "limit_down_pool", ok, int((_t.time() - t0) * 1000), "" if ok else str(e))
        if not ok:
            logger.warning(f"akshare 跌停池失败: {e}")

    return limit_up, limit_down


def _get_stats_akshare() -> dict:
    try:
        import akshare as ak
        limit_up, limit_down = _get_limit_counts_akshare()
        spot_df = ak.stock_zh_a_spot_em()
        if spot_df is not None and "涨跌幅" in spot_df.columns:
            up_count = int((spot_df["涨跌幅"] > 0).sum())
            down_count = int((spot_df["涨跌幅"] < 0).sum())
        else:
            up_count = 0
            down_count = 0
        return {
            "limit_up": limit_up,
            "limit_down": limit_down,
            "up_count": up_count,
            "down_count": down_count,
        }
    except Exception as e:
        logger.error(f"Market stats fetch failed (akshare): {e}")
        return {}


def _is_valid_stats(d: dict) -> bool:
    if not d:
        return False
    return d.get("limit_up", 0) > 0 and d.get("up_count", 0) > 0


# 市场温度缓存: 新浪全市场扫描约 22s, 太重不能每 30s 跑; 宽度/涨跌停变化不快, 缓存 5 分钟。
_market_stats_cache: dict = {"data": {}, "ts": 0.0, "attempt": 0.0}
_MARKET_STATS_TTL = 600       # 成功后 10 分钟内复用 (宽度变化慢)
_MARKET_STATS_RETRY = 60      # 失败后最少隔 60s 再扫 (防止把新浪越打越限流)


from backend.utils.limit_calc import get_limit_pct as _limit_pct_for  # 统一涨跌停幅


def _compute_market_stats(df) -> dict | None:
    """全市场快照 DataFrame → 市场温度 dict; 数据无效(竞价/源未就绪)返回 None.

    v1.7.387 源头校验(0612误报普查整改): 竞价时段新浪快照最新价全0、涨跌幅算不出 →
    上涨0/下跌0, 且"最新价0 ≤ 跌停价"会把全场数成跌停(0609-0611连续误报涨跌家数恶化的根源)。
    全场无涨跌幅=无数据返回 None; 最新价≤0 的个股不参与涨跌停判定。"""
    import pandas as pd
    if df is None or "涨跌幅" not in df.columns:
        return None
    pct = pd.to_numeric(df["涨跌幅"], errors="coerce")
    up_count = int((pct > 0).sum())
    down_count = int((pct < 0).sum())
    if up_count <= 0 and down_count <= 0:
        return None
    # 涨停/跌停: 用昨收按板块阈值算出精确涨停/跌停价, 与最新价比 (比"涨跌幅阈值"准, 不误收强势非涨停股)
    pre = pd.to_numeric(df["昨收"], errors="coerce")
    last = pd.to_numeric(df["最新价"], errors="coerce")
    thr = df.apply(lambda r: _limit_pct_for(r.get("代码", ""), r.get("名称", "")), axis=1) / 100.0
    up_limit_price = (pre * (1 + thr)).round(2)
    down_limit_price = (pre * (1 - thr)).round(2)
    valid = (pre > 0) & (last > 0)
    limit_up = int(((last >= up_limit_price - 0.001) & valid).sum())
    limit_down = int(((last <= down_limit_price + 0.001) & valid).sum())
    return {"limit_up": limit_up, "limit_down": limit_down,
            "up_count": up_count, "down_count": down_count}


def get_market_stats() -> dict:
    """市场温度: 上涨/下跌家数(精确) + 涨停/跌停(按板块涨跌幅阈值近似)。

    全部走 akshare 新浪源全市场一次扫描 (零东财 — 东财行情接口对生产 IP 封禁)。
    前端市场温度只显示涨跌家数; 涨停/跌停近似值供 plunge/regime 等内部用(用户面精确涨停/跌停看情绪面板)。
    新浪全市场约 22s, 缓存 5 分钟。
    """
    import time as _t
    import os
    import contextlib
    from backend.services import api_metrics
    now = _t.time()
    # 成功值在 TTL 内直接复用
    if now - _market_stats_cache["ts"] < _MARKET_STATS_TTL and _market_stats_cache["data"]:
        return dict(_market_stats_cache["data"])
    # 退避: 距上次尝试不足 RETRY 秒, 不再打新浪, 返回上次值(可能为空)
    if now - _market_stats_cache["attempt"] < _MARKET_STATS_RETRY:
        return dict(_market_stats_cache["data"])
    _market_stats_cache["attempt"] = now
    t0 = _t.time()
    try:
        import akshare as ak
        with open(os.devnull, "w") as _dn, contextlib.redirect_stderr(_dn):
            df = ak.stock_zh_a_spot()  # 新浪源全市场
        data = _compute_market_stats(df)
        if data is None:
            # 无数据(竞价时段最新价全0等) ≠ 失败, 但绝不能把垃圾写进缓存/返回。
            # 竞价时段(9:25-9:30)这是降级源的日常, 连续竞价中出现才算源健康事件。
            from backend.core.trading_calendar import is_continuous_auction
            if is_continuous_auction():
                from backend.services import data_health
                data_health.report("market_stats_empty", detail="全场无涨跌幅(最新价全0)")
            api_metrics.record("sina", "market_stats", False, int((_t.time() - t0) * 1000), "no data")
            return dict(_market_stats_cache["data"])
        _market_stats_cache.update(data=data, ts=now)
        api_metrics.record("sina", "market_stats", True, int((_t.time() - t0) * 1000), "")
        return dict(data)
    except Exception as e:
        api_metrics.record("sina", "market_stats", False, int((_t.time() - t0) * 1000), str(e))
        logger.warning(f"[market_stats] 新浪全市场取数失败: {e}")
        return dict(_market_stats_cache["data"])


def _fetch_single_trend(secid: str) -> dict:
    """单只指数分时数据 (EastMoney trends2 接口).

    返回: {pre_close, trends: [{time, price, avg_price, volume}], amount}
      - volume 是该分钟新增成交量 (手), 用于绘制成交量柱图
      - amount 是当日累计成交额 (亿)
    """
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&iscr=0&ndays=1"
    )
    empty = {"pre_close": 0, "trends": [], "amount": 0}
    # v1.7.x: 主源空响应/非JSON保护 — curl 拿到空 body 或被限流返 HTML 时,
    # 不再让 json.loads 抛 JSONDecodeError 把整个刷新任务带崩(对齐 _fetch_single_trend_ths)。
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url,
             "-H", f"User-Agent: {HEADERS['User-Agent']}",
             "-H", "Referer: https://quote.eastmoney.com"],
            capture_output=True, timeout=10,
        )
        raw = proc.stdout.decode("utf-8").strip()
        if not raw:
            logger.warning(f"EastMoney trend 空响应 secid={secid} (rc={proc.returncode}) — 交备用源")
            return empty
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"EastMoney trend fetch failed secid={secid}: {e} — 交备用源")
        return empty
    pre_close = data.get("data", {}).get("preClose", 0)
    trends_raw = data.get("data", {}).get("trends", [])
    # parts 顺序: 0=time 1=open 2=close 3=high 4=low 5=volume(手) 6=amount(每分钟成交额, 元) 7=avg_price
    # 注意: parts[6] 是该分钟成交额(非累计), 当日累计=各分钟求和(实测 13:07 值<13:06, 累计不可能递减)。
    trends = []
    amount = 0
    for t in trends_raw:
        parts = t.split(",")
        if len(parts) < 3:
            continue
        try:
            point = {
                "time": parts[0][11:16],
                "price": float(parts[2]),
                "volume": float(parts[5]) if len(parts) > 5 and parts[5] else 0.0,
                "avg_price": float(parts[7]) if len(parts) > 7 and parts[7] else float(parts[2]),
            }
            trends.append(point)
        except (ValueError, IndexError):
            continue
        if len(parts) >= 7 and parts[6]:
            try:
                amount += float(parts[6]) / 1e8        # 各分钟求和 = 当日累计成交额
            except ValueError:
                pass
    return {"pre_close": pre_close, "trends": trends, "amount": round(amount, 0)}


def _fetch_single_trend_ths(ths_code: str) -> dict:
    url = f"http://d.10jqka.com.cn/v4/time/{ths_code}/last.js"
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url,
             "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
             "-H", "Referer: http://stockpage.10jqka.com.cn/"],
            capture_output=True, timeout=10,
        )
        text = proc.stdout.decode("utf-8")
        start = text.index("(") + 1
        end = text.rindex(")")
        data = json.loads(text[start:end])
        inner = data.get(ths_code, {})
        pre_close = float(inner.get("pre", 0))
        raw = inner.get("data", "")
        if not raw:
            return {"pre_close": pre_close, "trends": [], "amount": 0}
        trends = []
        amount = 0
        # THS 字段顺序: 0=time 1=price 2=cum_amount 3=avg_price 4=volume(可能)
        for item in raw.split(";"):
            parts = item.split(",")
            if len(parts) >= 2:
                t = parts[0]
                point = {"time": f"{t[:2]}:{t[2:]}", "price": float(parts[1])}
                if len(parts) >= 4:
                    try:
                        point["avg_price"] = float(parts[3])
                    except ValueError:
                        point["avg_price"] = point["price"]
                if len(parts) >= 5:
                    try:
                        point["volume"] = float(parts[4])
                    except ValueError:
                        point["volume"] = 0.0
                else:
                    point["volume"] = 0.0
                trends.append(point)
            if len(parts) >= 3:
                try:
                    amount = float(parts[2]) / 1e8
                except ValueError:
                    pass
        return {"pre_close": pre_close, "trends": trends, "amount": round(amount, 0)}
    except Exception as e:
        logger.warning(f"THS trend fetch failed for {ths_code}: {e}")
        return {"pre_close": 0, "trends": [], "amount": 0}


# 港股指数(腾讯 ifzq 分时/日K; 现价涨跌幅在全球指数区已有, 前端按名兜底)
HK_INDEX = [("hkHSI", "恒生指数"), ("hkHSTECH", "恒生科技")]


def _fetch_hk_trend(qcode: str) -> dict:
    """腾讯 ifzq 港股指数分时 → {pre_close, trends:[{time,price,avg_price,volume}], amount(亿)}。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={qcode}"
    empty = {"pre_close": 0, "trends": [], "amount": 0}
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url, "-H", f"User-Agent: {HEADERS['User-Agent']}"],
            capture_output=True, timeout=10,
        )
        d = json.loads(proc.stdout.decode("utf-8"))
        node = (d.get("data") or {}).get(qcode) or {}
        md = node.get("data") or {}
        rows = md.get("data") or []
        prec = float(md.get("prec") or 0)
        if not prec:
            qt = (node.get("qt") or {}).get(qcode) or []
            prec = float(qt[4]) if len(qt) > 4 else 0
        # 腾讯 ifzq 港股指数每行 [时间, 价, 累计量, 累计额], 两个坑要绕开:
        #   1) 指数无真实成交量, "累计量"=累计额/10000(合成, 实测每行 额/量 恒=10000), 拿
        #      累计额/累计量 当均价会得到恒定 10000 的废线, 把价格刻度撑爆 → 自己用 价×分钟量
        #      累计算真实 VWAP 均价线(按量加权=按额加权, 即真实均价)。
        #   2) 收盘集合竞价末行偶发把累计额截断回落成≈累计量(脏行)→ 成交额取历史最大累计额,
        #      并对累计量回落的异常行跳过计量, 防脏末行污染成交额/均价。
        trends = []
        prev_vol = 0.0
        max_amt = 0.0
        cum_pv = 0.0   # Σ 价×分钟量
        cum_v = 0.0    # Σ 分钟量
        for r in rows:
            p = r.split()
            if len(p) < 2:
                continue
            t = p[0]
            time_s = f"{t[:2]}:{t[2:4]}" if len(t) >= 4 else t
            price = float(p[1])
            cumvol = float(p[2]) if len(p) > 2 else 0.0
            cumamt = float(p[3]) if len(p) > 3 else 0.0
            vol = cumvol - prev_vol
            if vol < 0:            # 累计量回落=脏行(末行截断), 不计该分钟量/额
                vol = 0.0
            else:
                prev_vol = cumvol
                cum_pv += price * vol
                cum_v += vol
                if cumamt > max_amt:
                    max_amt = cumamt
            avg = (cum_pv / cum_v) if cum_v > 0 else price
            trends.append({"time": time_s, "price": price, "avg_price": round(avg, 3), "volume": vol})
        return {"pre_close": prec, "trends": trends, "amount": round(max_amt / 1e8, 1) if max_amt else 0}
    except Exception as e:
        logger.warning(f"HK trend fetch failed ({qcode}): {e}")
        return empty


# 指数分时 20s TTL: plunge_detector(30s) 与 market_data_refresher(60s) 各自全量拉同一份数据,
# 每分钟约3轮×7指数纯属浪费; 急跌判定窗口10分钟, 20s滞后无影响。
_index_trends_cache: dict = {"ts": 0.0, "data": {}}
_INDEX_TRENDS_TTL = 20


# A股指数代码(适用交易分钟陈旧度判断; 港股时段不同, 不适用)
_A_SHARE_INDEX_CODES = ("sh000001", "sz399001", "sz399006", "sh000688", "sz399317")


def _sanitize_stale_index_trends(result: dict, now_hhmm: str) -> dict:
    """v1.7.387 源头校验(0612误报普查整改): A股指数分时末点距 now 超阈值(交易分钟) →
    源冻结回放(挂掉后反复吐同一份序列, 科创0610/0611假急跌根源), 清空该指数 trends,
    下游(急跌检测/前端分时图/AI报告)统一表现为"无数据"而非陈旧假数据。港股不动。"""
    from backend.core.trading_calendar import trends_stale
    for code in _A_SHARE_INDEX_CODES:
        d = result.get(code)
        if d and trends_stale(d.get("trends", []), now_hhmm):
            last_t = d["trends"][-1].get("time")
            logger.warning(
                f"[index_trends] {d.get('name', code)} 分时末点{last_t}"
                f"距now({now_hhmm})过远, 疑似源冻结回放, 该指数分时置空")
            from backend.services import data_health
            data_health.report("index_trends_frozen",
                               detail=f"{d.get('name', code)}末点停在{last_t}")
            d["trends"] = []
    return result


def get_index_trends() -> dict:
    import time as _t
    now = _t.time()
    if now - _index_trends_cache["ts"] < _INDEX_TRENDS_TTL and _index_trends_cache["data"]:
        return dict(_index_trends_cache["data"])
    result = {}
    secid_map = {
        "1.000001": ("sh000001", "上证指数", "hs_1A0001"),
        "0.399001": ("sz399001", "深证成指", "hs_399001"),
        "0.399006": ("sz399006", "创业板指", "hs_399006"),
        "1.000688": ("sh000688", "科创指数", "hs_1B0688"),
        "0.399317": ("sz399317", "全A指数", "hs_399317"),   # 国证A指(全部A股); 东财prod失败时同花顺 hs_399317 兜底
    }
    for secid, (code, name, ths_code) in secid_map.items():
        fetched = _retry_with_fallback(
            lambda s=secid: _fetch_single_trend(s),
            lambda tc=ths_code: _fetch_single_trend_ths(tc),
            lambda r: bool(r) and len(r.get("trends", [])) > 0,  # 防 None: 主源抛异常时 result=None
            f"trend_{code}"
        )
        fetched["name"] = name
        result[code] = fetched
    # 港股指数分时(腾讯 ifzq)
    for qcode, name in HK_INDEX:
        ht = _fetch_hk_trend(qcode)
        ht["name"] = name
        result[qcode] = ht
    # 连续竞价时段才清洗陈旧分时(盘前9:25-9:30/收盘后/周末, 源返回上一时段全天序列是正常态)
    from backend.core.trading_calendar import is_continuous_auction
    if is_continuous_auction():
        from datetime import datetime as _dt
        result = _sanitize_stale_index_trends(result, _dt.now().strftime("%H:%M"))
    if any(v.get("trends") for v in result.values()):
        _index_trends_cache["ts"] = now
        _index_trends_cache["data"] = dict(result)
    return result


def _fetch_zt_pool(prev_date: str) -> list:
    url = (
        f"https://push2ex.eastmoney.com/getTopicZTPool"
        f"?ut=7eea3edcaed734bea9telerik&dession=128.16.60"
        f"&date={prev_date}"
    )
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", url,
             "-H", f"User-Agent: {HEADERS['User-Agent']}",
             "-H", "Referer: https://quote.eastmoney.com"],
            capture_output=True, timeout=10,
        )
        data = json.loads(proc.stdout.decode("utf-8"))
        return data.get("data", {}).get("pool", []) if data.get("data") else []
    except Exception as e:
        logger.warning(f"ZT pool fetch failed (EastMoney): {e}")
        return []


def _fetch_zt_pool_akshare(prev_date: str) -> list:
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=prev_date)
        if df is None or df.empty:
            return []
        pool = []
        for _, row in df.iterrows():
            fbt = str(row.get("首次封板时间", "")).replace(":", "")
            zbc = int(row.get("炸板次数", 1))
            fund = float(row.get("封板资金", 0))
            pool.append({
                "c": str(row.get("代码", "")).zfill(6),
                "n": str(row.get("名称", "")),
                "fbt": fbt,
                "zbc": zbc,
                "fund": fund,
            })
        return pool
    except Exception as e:
        logger.warning(f"ZT pool fetch failed (akshare): {e}")
        return []


async def get_strong_zt_performance() -> dict:
    """昨日强势涨停股（10点前封板、全天未开板、封单>2亿）今日表现"""
    from datetime import timedelta

    today = datetime.now()
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    prev_date = d.strftime("%Y%m%d")

    pool = _retry_with_fallback(
        lambda: _fetch_zt_pool(prev_date),
        lambda: _fetch_zt_pool_akshare(prev_date),
        lambda r: len(r) > 0, "zt_pool"
    )

    if not pool:
        return []

    strong = []
    for item in pool:
        fbt = str(item.get("fbt", ""))
        zbc = item.get("zbc", 1)
        fund = item.get("fund", 0) or 0
        if fbt >= "100000":
            continue
        if zbc > 0:
            continue
        if fund < 200000000:
            continue
        code = str(item.get("c", "")).zfill(6)
        name = item.get("n", "")
        strong.append({"code": code, "name": name, "fund": fund})

    if not strong:
        return {"date": prev_date, "stocks": [], "count": 0}

    codes = [s["code"] for s in strong]
    quotes = await data_fetcher.get_realtime_quotes(codes)

    result_stocks = []
    for s in strong:
        q = quotes.get(s["code"], {})
        pct = q.get("pct_change", 0)
        price = q.get("price", 0)
        result_stocks.append({
            "code": s["code"],
            "name": s["name"],
            "fund_yi": round(s["fund"] / 1e8, 1),
            "today_pct": pct,
            "today_price": price,
        })

    result_stocks.sort(key=lambda x: x["today_pct"], reverse=True)
    avg_pct = round(sum(s["today_pct"] for s in result_stocks) / len(result_stocks), 2) if result_stocks else 0
    up_count = sum(1 for s in result_stocks if s["today_pct"] > 0)

    return {
        "date": prev_date,
        "count": len(result_stocks),
        "avg_pct": avg_pct,
        "up_count": up_count,
        "down_count": len(result_stocks) - up_count,
        "stocks": result_stocks,
    }


TIME_WEIGHT = {
    "09:30": 0.0, "10:00": 0.25, "10:30": 0.35,
    "11:00": 0.45, "11:30": 0.52,
    "13:00": 0.52, "13:30": 0.62, "14:00": 0.72,
    "14:30": 0.83, "15:00": 1.0,
}


def _estimate_full_day_amount(current_amount: float, current_time: str) -> float:
    """根据当前时段成交额推算全天成交额"""
    weight = 0
    for ts, w in TIME_WEIGHT.items():
        if current_time >= ts:
            weight = w
    if weight <= 0:
        return 0
    return round(current_amount / weight, 0)


def _fetch_amount_compare() -> dict:
    """两市(上证综指+深证成指)分时累计成交额 today vs 昨日同期。

    修复(2026-06-04): 原来只取上证综指、且 today_val 取的是 checkpoint 那一分钟的
    单分钟成交额(非累计)→ 喂给 AI 的"两市合计成交额"小了上百倍(如 25亿)。
    现改为: 两市分时成交额(元→亿)按时间累计求和, 口径与 /turnover 面板一致。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    cum_today: dict[str, float] = {}   # hhmm -> 两市当日累计(亿)
    cum_yest: dict[str, float] = {}
    got = False
    for secid in ("1.000001", "0.399001"):   # 上证综指 + 深证成指
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid={secid}"
            "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=2"
        )
        try:
            proc = subprocess.run(
                ["curl", "-s", "--compressed", url,
                 "-H", f"User-Agent: {HEADERS['User-Agent']}",
                 "-H", "Referer: https://quote.eastmoney.com"],
                capture_output=True, timeout=10,
            )
            trends = json.loads(proc.stdout.decode("utf-8")).get("data", {}).get("trends", [])
        except Exception as e:
            logger.warning(f"Amount compare fetch failed (secid={secid}): {e}")
            continue
        if not trends:
            continue
        got = True
        run_t = 0.0   # 今日累计
        run_y = 0.0   # 昨日累计
        for t in trends:
            parts = t.split(",")
            if len(parts) < 7:
                continue
            ts = parts[0]
            hhmm = ts[11:16]
            amt = float(parts[6]) / 1e8 if parts[6] else 0.0   # 该分钟成交额(元)→亿
            if ts.startswith(today_str):
                run_t += amt
                cum_today[hhmm] = cum_today.get(hhmm, 0.0) + run_t   # 叠加另一指数的同时点累计
            else:
                run_y += amt
                cum_yest[hhmm] = cum_yest.get(hhmm, 0.0) + run_y

    if not got or not cum_today:
        return {}

    def _at(cum: dict[str, float], cp: str) -> float:
        ks = [k for k in cum if k <= cp]
        return cum[max(ks)] if ks else 0.0

    hourly: dict = {}
    for cp in ["10:00", "11:00", "11:30", "14:00", "15:00"]:
        today_val = _at(cum_today, cp)
        yest_val = _at(cum_yest, cp)
        if today_val > 0:
            pct = round((today_val - yest_val) / yest_val * 100, 1) if yest_val > 0 else 0
            hourly[cp] = {"today": round(today_val), "yesterday": round(yest_val), "pct": pct}

    latest_time = max(cum_today)
    latest_amount = cum_today[latest_time]
    estimated = _estimate_full_day_amount(latest_amount, latest_time)
    if estimated > 0:
        hourly["_estimated_full_day"] = estimated
        hourly["_latest_time"] = latest_time
    return hourly


def get_market_amount_compare() -> dict:
    return _retry_with_fallback(
        _fetch_amount_compare, _fetch_amount_compare,
        lambda r: bool(r), "amount_compare"
    )


async def _describe_kline_from_cache(code: str) -> str:
    """从DB缓存生成K线形态描述"""
    try:
        rows = await repository.get_cached_klines(code, limit=20)
        if not rows or len(rows) < 5:
            return ""

        latest = rows[-1]
        close = float(latest["close"])
        high5 = max(float(r["high"]) for r in rows[-5:])
        low5 = min(float(r["low"]) for r in rows[-5:])
        ma5 = sum(float(r["close"]) for r in rows[-5:]) / 5
        ma10 = sum(float(r["close"]) for r in rows[-10:]) / min(len(rows), 10)
        ma20 = sum(float(r["close"]) for r in rows[-20:]) / min(len(rows), 20)

        features = []
        if close > ma5 > ma10 > ma20:
            features.append("多头排列")
        elif close < ma5 < ma10 < ma20:
            features.append("空头排列")

        if close >= high5 * 0.98:
            features.append("近5日新高")
        if close <= low5 * 1.02:
            features.append("近5日新低")

        vol_avg = sum(float(r["volume"]) for r in rows[-5:]) / 5
        vol_today = float(latest["volume"])
        if vol_avg > 0:
            if vol_today > vol_avg * 1.5:
                features.append("放量")
            elif vol_today < vol_avg * 0.6:
                features.append("缩量")

        open_p = float(latest["open"])
        body = abs(close - open_p)
        upper_shadow = float(latest["high"]) - max(close, open_p)
        lower_shadow = min(close, open_p) - float(latest["low"])
        if body > 0:
            if upper_shadow > body * 2:
                features.append("长上影")
            if lower_shadow > body * 2:
                features.append("长下影")

        close_5ago = float(rows[-5]["close"]) if len(rows) >= 5 else close
        if close_5ago > 0:
            pct_5d = round((close - close_5ago) / close_5ago * 100, 1)
            features.append(f"5日涨幅{pct_5d:+.1f}%")

        return "、".join(features) if features else ""
    except Exception as e:
        logger.warning(f"K-line describe failed for {code}: {e}")
        return ""


async def _build_buy_tracking(today_signals: list) -> dict:
    """买点盈利跟踪: 今日/昨日 buy 信号 触发价→现价 的差额%。板块(BK)无报价不计入。

    今日买点 = 当天触发, 触发价→报告时刻现价; 昨日买点 = 上一交易日触发, 触发价→现在。
    """
    from datetime import datetime as _dt, timedelta as _td
    from backend.core.trading_calendar import is_workday as _is_workday

    def _clean(nm: str) -> str:
        for t in ("（右侧）", "（左侧）", "(右侧)", "(左侧)"):
            nm = nm.replace(t, "")
        return nm.strip() or nm

    d = _dt.now().date() - _td(days=1)
    for _ in range(10):
        if _is_workday(_dt(d.year, d.month, d.day)):
            break
        d -= _td(days=1)
    prev = d.strftime("%Y-%m-%d")

    today_buys = [s for s in (today_signals or [])
                  if s.get("direction") == "buy" and not str(s["code"]).startswith("BK")]
    try:
        yest_rows = await repository.get_buy_signals_on_date(prev)
    except Exception:
        yest_rows = []
    yest_buys = [s for s in yest_rows if not str(s["code"]).startswith("BK")]

    codes = list({s["code"] for s in today_buys} | {s["code"] for s in yest_buys})
    if not codes:
        return {}
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception:
        quotes = {}

    def _rows(sigs):
        out = []
        for s in sigs:
            try:
                entry = float(s.get("price") or 0)
            except (ValueError, TypeError):
                entry = 0.0
            cur = (quotes.get(s["code"]) or {}).get("price")
            if not entry or not cur:
                continue
            out.append({"name": s["name"], "signal": _clean(s.get("signal_name") or ""),
                        "pct": round((float(cur) - entry) / entry * 100, 2)})
        out.sort(key=lambda x: x["pct"], reverse=True)
        return out

    def _summ(items):
        n = len(items)
        if not n:
            return {"n": 0, "avg": 0.0, "red": 0, "green": 0}
        return {"n": n, "avg": round(sum(i["pct"] for i in items) / n, 1),
                "red": sum(1 for i in items if i["pct"] > 0),
                "green": sum(1 for i in items if i["pct"] < 0)}

    today_r, yest_r = _rows(today_buys), _rows(yest_buys)
    if not today_r and not yest_r:
        return {}
    return {
        "as_of": _dt.now().strftime("%H:%M"),
        "today": today_r, "today_sum": _summ(today_r),
        "yest": yest_r, "yest_sum": _summ(yest_r),
    }


async def gather_market_context(time_slot: str) -> dict:
    context = {
        "time_slot": time_slot,
        "time_slot_name": TIME_SLOT_NAMES.get(time_slot, ""),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 这四个外部行情拉取是同步阻塞(requests + 内部 time.sleep), 直接在 async 里调会冻结
    # event loop(报告生成期间饿死 3s 行情/30s 扫描)。改 to_thread 卸到线程池并发拉。
    context["indices"], context["global_indices"], context["market_stats"], context["amount_compare"] = (
        await asyncio.gather(
            asyncio.to_thread(get_market_indices),
            asyncio.to_thread(get_global_indices),  # v1.7.84: 全球主要股指(美/欧/港)
            asyncio.to_thread(get_market_stats),
            asyncio.to_thread(get_market_amount_compare),
        )
    )
    context["strong_zt"] = await get_strong_zt_performance()

    all_stocks = await repository.list_all_stocks()
    if all_stocks:
        codes = list({s["code"] for s in all_stocks})
        quotes = await data_fetcher.get_realtime_quotes(codes)
        # 无实时价的票(盘前/停牌)要回退查K线缓存 — 并发查一轮, 不在循环里逐只串行(N+1)
        no_price_codes = [s["code"] for s in all_stocks if not quotes.get(s["code"], {}).get("price", 0)]
        kline_fallback: dict[str, list] = {}
        if no_price_codes:
            kl_results = await asyncio.gather(
                *[repository.get_cached_klines(c, limit=2) for c in no_price_codes],
                return_exceptions=True)
            kline_fallback = {c: r for c, r in zip(no_price_codes, kl_results)
                              if isinstance(r, list) and r}
        pool_data = []
        for s in all_stocks:
            q = quotes.get(s["code"], {})
            price = q.get("price", 0)
            pct = q.get("pct_change", 0)
            name = q.get("name", s.get("name", ""))
            if not price:
                klines = kline_fallback.get(s["code"])
                if klines:
                    price = float(klines[-1]["close"])
                    if len(klines) >= 2:
                        prev_close = float(klines[-2]["close"])
                        pct = round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
                    name = name or s.get("name", "")
            pool_data.append({
                "code": s["code"],
                "name": name,
                "pct_change": pct,
                "price": price,
                "trade_type": s.get("trade_type", ""),
                "kline": "",
            })
        pool_data.sort(key=lambda x: x["pct_change"], reverse=True)
        # 前30只的K线形态描述并发生成(每只一次DB查询, 串行要30个RTT)
        top30 = pool_data[:30]
        descs = await asyncio.gather(
            *[_describe_kline_from_cache(s["code"]) for s in top30],
            return_exceptions=True)
        for s, d in zip(top30, descs):
            s["kline"] = d if isinstance(d, str) else ""
        context["pool_stocks"] = pool_data
        context["pool_count"] = len(codes)

    today_signals = await repository.get_today_signals_all()
    if today_signals:
        buy_list = [s for s in today_signals if s["direction"] == "buy"]
        sell_list = [s for s in today_signals if s["direction"] in ("sell", "reduce")]

        # 买入: 拆"板块预警"(code 以 BK 开头) 与"个股信号"两组
        buy_sectors = [s for s in buy_list if str(s["code"]).startswith("BK")]
        buy_stocks = [s for s in buy_list if not str(s["code"]).startswith("BK")]

        # 卖出: 按个股聚合, 同股多信号合并(保持触发顺序)
        from collections import OrderedDict
        sell_by_code: "OrderedDict[str, dict]" = OrderedDict()
        for s in sell_list:
            key = s["code"]
            if key not in sell_by_code:
                sell_by_code[key] = {"code": s["code"], "name": s["name"], "signals": []}
            sell_by_code[key]["signals"].append(s["signal_name"])

        # 午盘(1400)/收盘(1500)报告: 给每个买点附历史胜率
        # (近90天真实 outcome, 成功=买入后5个交易日收盘 ≥+5%; 样本不足则不显示)
        outcome_stats: dict = {}
        if time_slot in ("1400", "1500"):
            try:
                outcome_stats = await repository.get_signal_outcome_stats(user_id=1, days_back=90)
            except Exception as e:
                logger.warning(f"[report] 取买点历史胜率失败: {e}")
                outcome_stats = {}

        def _winrate(signal_id: str | None) -> dict | None:
            st = outcome_stats.get(signal_id) if signal_id else None
            if not st or not st.get("evaluated"):
                return None
            return {"rate": st["success_rate"], "success": st["success"], "evaluated": st["evaluated"]}

        context["signals_summary"] = {
            "total": len(today_signals),
            "buy": len(buy_list),
            "sell": len(sell_list),
            "buy_sectors": [
                {"name": s["name"], "code": s["code"], "signal_name": s["signal_name"],
                 "win": _winrate(s.get("signal_id"))}
                for s in buy_sectors
            ],
            "buy_stocks": [
                {"name": s["name"], "code": s["code"], "signal_name": s["signal_name"],
                 "win": _winrate(s.get("signal_id"))}
                for s in buy_stocks
            ],
            "sell_groups": list(sell_by_code.values()),
            # 兼容旧消费方(AI prompt 拼接), 保留扁平明细
            "buy_details": [
                f"{s['name']}({s['code']}) {s['signal_name']}"
                for s in buy_list
            ],
            "sell_details": [
                f"{s['name']}({s['code']}) {s['signal_name']}"
                for s in sell_list
            ],
        }

    # 买点盈利跟踪(午盘/收盘报告): 今日/昨日买点 触发价→现价 差额%
    if time_slot in ("1400", "1500"):
        try:
            bt = await _build_buy_tracking(today_signals)
            if bt:
                context["buy_tracking"] = bt
        except Exception as e:
            logger.warning(f"[report] 买点盈利跟踪构建失败: {e}")

    try:
        popularity = await data_fetcher.get_popularity_full(10)
        if popularity.get("stocks"):
            context["popularity_top10"] = [
                {"rank": s["rank"], "name": s["name"], "code": s["code"],
                 "pct_change": s["pct_change"]}
                for s in popularity["stocks"][:10]
            ]
        if popularity.get("hot_concepts"):
            context["hot_concepts"] = [
                {"name": c["name"], "count": c["count"], "stocks": c["stocks"][:3]}
                for c in popularity["hot_concepts"][:5]
            ]
    except Exception as e:
        logger.warning(f"Popularity fetch for report failed: {e}")

    return context


def _build_prompt(context: dict) -> tuple[str, str]:
    time_slot = context.get("time_slot", "")
    slot_name = context.get("time_slot_name", "")
    dt = context.get("datetime", "")

    # === Build data block ===
    parts = [f"当前时间：{dt}，时段：{slot_name}"]

    # 大盘指数
    indices = context.get("indices", [])
    if indices:
        idx_lines = []
        for i in indices:
            idx_lines.append(f"{i['name']} {i['price']:.2f} ({i['pct_change']:+.2f}%) 成交额{i.get('amount', 0):.0f}亿")
        parts.append("A股四指数：" + " | ".join(idx_lines))

    # v1.7.84: 全球主要股指(美/欧/港)
    global_indices = context.get("global_indices", [])
    if global_indices:
        # 按 region 分组
        by_region: dict[str, list[str]] = {}
        for g in global_indices:
            line = f"{g['name']} {g['price']:.2f} ({g['pct_change']:+.2f}%)"
            by_region.setdefault(g["region"], []).append(line)
        for region in ("美股", "欧洲", "日本", "港股"):
            if region in by_region:
                parts.append(f"{region}：" + " | ".join(by_region[region]))

    # 成交额对比（使用trends2 API的准确数据）
    amount_cmp = context.get("amount_compare", {})
    total_amount = 0
    if amount_cmp:
        latest_cp = None
        for cp in ["15:00", "14:00", "11:30", "11:00", "10:00"]:
            if cp in amount_cmp:
                latest_cp = cp
                break
        if latest_cp:
            v = amount_cmp[latest_cp]
            total_amount = v["today"]
            parts.append(f"两市合计成交额：{total_amount:.0f}亿")
            parts.append(f"较昨日同期（{latest_cp}）：{v['pct']:+.1f}%（今{v['today']:.0f}亿 vs 昨{v['yesterday']:.0f}亿）")

    # 预计全天成交额
    if total_amount > 0:
        now_time = datetime.now().strftime("%H:%M")
        estimated_full = _estimate_full_day_amount(total_amount, now_time)
        if estimated_full > 0:
            parts.append(f"预计全天成交额：{estimated_full:.0f}亿")

    # 涨跌停统计
    stats = context.get("market_stats", {})
    if stats:
        parts.append(
            f"市场统计：涨停{stats.get('limit_up', '?')}家，跌停{stats.get('limit_down', '?')}家，"
            f"上涨{stats.get('up_count', '?')}家，下跌{stats.get('down_count', '?')}家"
        )

    # 昨日强势涨停今日表现
    strong_zt = context.get("strong_zt", {})
    if strong_zt and strong_zt.get("stocks"):
        stocks = strong_zt["stocks"]
        parts.append(
            f"昨日强势涨停（10点前封板、未开板、封单>2亿）今日表现：共{strong_zt['count']}只，"
            f"平均涨幅{strong_zt['avg_pct']:+.2f}%，上涨{strong_zt['up_count']}只，下跌{strong_zt['down_count']}只"
        )
        for s in stocks:
            parts.append(f"  {s['name']}({s['code']}) 昨日封单{s['fund_yi']}亿 → 今日{s['today_pct']:+.2f}%")

    # 热门概念
    hot_concepts = context.get("hot_concepts", [])
    if hot_concepts:
        concept_str = ", ".join(f"{c['name']}({c['count']}只)" for c in hot_concepts)
        parts.append(f"热门概念：{concept_str}")

    # 人气排行
    pop_top = context.get("popularity_top10", [])
    if pop_top:
        pop_str = ", ".join(f"{s['name']}({s['pct_change']:+.2f}%)" for s in pop_top)
        parts.append(f"人气排行Top10：{pop_str}")

    # 自选股详情
    pool = context.get("pool_stocks", [])
    if pool:
        parts.append(f"\n=== 自选股池（共{len(pool)}只） ===")
        for s in pool:
            line = f"{s['name']}({s['code']}) 现价{s['price']:.2f} 涨跌{s['pct_change']:+.2f}% [{s['trade_type']}]"
            if s.get("kline"):
                line += f" K线形态：{s['kline']}"
            parts.append(line)

    # 今日信号
    signals = context.get("signals_summary")
    if signals:
        parts.append(f"\n今日信号：共{signals['total']}个（买入{signals['buy']}，卖出/减仓{signals['sell']}）")
        details = (signals.get("buy_details") or []) + (signals.get("sell_details") or [])
        if details:
            parts.append(f"信号明细：{'; '.join(details)}")

    data_block = "\n".join(parts)

    # === Build system prompt ===
    time_guidance = ""
    if time_slot == "0926":
        time_guidance = "这是早盘9:26的集合竞价时段，重点关注今日开盘情况、昨日复盘要点、今日关注方向。"
    elif time_slot == "1000":
        time_guidance = "这是10:00早盘跟踪，重点分析开盘后走势演变、资金流向、板块轮动情况。"
    elif time_slot == "1130":
        time_guidance = "这是上午收盘总结，重点总结上半场行情特征、下午可能的走势研判。"
    elif time_slot == "1430":
        time_guidance = "这是14:30尾盘分析，重点关注尾盘异动、资金动向、明日预判。"
    elif time_slot == "1600":
        time_guidance = "这是16:00收盘总结，重点做全天复盘、涨跌原因分析、明日操作计划。"

    system_prompt = f"""你是一位专业的A股短线交易分析师，服务于个人投资者的智能监控系统。
你需要结合用户的交易策略体系来给出操作建议。

【用户交易策略体系】
短线买入策略：
- BUY_WEAK_EXTREME（弱势极限·左侧）：主升浪后地量+缩量+贴 MA10/MA20 ±2%, 缩量枯竭等启动
- BUY_STRONG_START（强势起点·右侧）：左侧弱势极限后, 今日预估量≥前期×3 且全天预估额≥20亿, 涨≥2%, 站上 MA10/MA20

短线卖出策略 (仅对持仓股, 盘中任何时刻可触发)：
- SELL_BREAK_MA5（短线卖 跌破MA5）：close 跌破 MA5 ≥2%
- SELL_BREAK_MA10（短线卖 跌破MA10）：close 跌破 MA10 ≥2%
- SELL_BREAK_MA20（短线卖 跌破MA20）：close 跌破 MA20 ≥2%
- SELL_TAKE_PROFIT：浮盈≥7%减仓锁利
- SELL_LOSS_5 / SELL_LOSS_8 / SELL_LOSS_10：浮亏 -5%/-8%/-10% 持仓警戒

风控策略：
- R1_REDUCE（高位巨量）：20日量比>2.5时提示减仓
- SL_SELL（单笔止损）：买入后跌幅达5%强制止损

请基于以上策略体系和以下市场数据生成盘面分析报告，用纯 HTML 格式输出（禁止用 markdown），必须包含【第一部分】和【第二部分】两个完整部分。

【输出格式规范】
- 报告顶部第一行必须是标题栏，格式：<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><b style="font-size:14px">盘面分析 · {slot_name}</b><span style="font-size:11px;color:#888">{dt}</span></div>
- 标题用 <h3 style="margin:12px 0 6px;font-size:13px">
- 用 flexbox 卡片和 CSS 条形图展示数据，紧凑排列
- 涨/正面用红色：color:#e53e3e
- 跌/负面用绿色：color:#16a34a
- 重点数据用 <b> 加粗
- 下跌/亏损/需减仓的行：background:#fff5f5
- 表格：style="width:100%;border-collapse:collapse;font-size:12px"
- 单元格：style="padding:4px 6px;border-bottom:1px solid #f0f0f0"
- 表头：style="background:#f0f7ff"
- 直接输出 HTML，不要包裹在代码块中
- 整体风格：紧凑、信息密度高、多用图形化展示

注意：本报告**不要**再输出全球股市、A股四指数卡片、市场温度（涨停/跌停/涨跌比）这些纯数据展示——它们由前端 MarketOverviewBar 实时刷新, 已独立挂在页面顶部, 此处重复输出会冗余。
本报告专注于"AI 才能产出的价值": 市场环境定性 + 板块逻辑 + 持仓研判 + 操作要点。

【第一部分：市场环境与板块】

<h3 style="margin:8px 0 6px;font-size:13px">操作建议
<span style="font-size:12px;padding:3px 10px;border-radius:4px;font-weight:bold;margin-left:10px">[根据综合判断填入: 适合买入 / 谨慎观望 / 不适合买入]</span>
</h3>
标签颜色规则:
- 适合买入: background:#e6ffec;color:#0d7a2e;border:1px solid #a3e6b5
- 谨慎观望: background:#fff8dc;color:#b8860b;border:1px solid #f0d060
- 不适合买入: background:#ffe5e5;color:#cc0000;border:1px solid #f5a0a0

紧跟一句话评估理由(15-30 字), 引用关键数据点(如"两市成交2.9万亿低于昨日3.5万亿, 涨跌比1:2, 谨慎为主"):
<div style="font-size:12px;color:#555;margin:4px 0 8px">理由文本...</div>

<h3>热点板块</h3>
紧凑表格（font-size:12px）：板块 | 涨停数 | 代表股 | 逻辑
只列今日真正活跃的 3-5 个板块, AI 总结炒作逻辑。

【第二部分：自选股分析】（必须输出，这是最重要的部分）

<h3>持仓动态</h3>
只展示有交易策略相关性的个股（已触发信号 或 根据K线形态判断即将可能触发策略的），无关股票不要列出。
紧凑表格：股票 | 现价 | 涨跌 | K线形态 | 策略研判 | 操作建议
- 下跌股票整行浅红背景
- "策略研判"列：说明处于哪个策略的触发条件附近
- "操作建议"列用加粗+颜色
- 如果无任何股票接近触发条件，输出一行：<div style="font-size:12px;color:#888;margin:8px 0">当前无接近策略触发条件的个股</div>

<h3>操作要点</h3>
2-3句话总结：
- 哪些接近买入信号触发条件
- 哪些必须执行减仓/止损
- 整体仓位建议

{time_guidance}"""

    return system_prompt, data_block


async def generate_report(time_slot: str) -> tuple[str, dict] | None:
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        logger.warning("AI API key not configured, skipping report")
        return None

    if not cfg.get("ai_report_enabled", True):
        logger.info("AI report disabled, skipping")
        return None

    context = await gather_market_context(time_slot)
    system_prompt, user_content = _build_prompt(context)

    from openai import OpenAI
    base_url = cfg.get("ai_base_url", "https://api.deepseek.com/v1")
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    for attempt in range(1, 4):
        try:
            # 同步 SDK 调用(可耗时 10-60s)卸到线程池, 不阻塞 event loop
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=cfg.get("ai_model", "deepseek-chat"),
                    max_tokens=16384,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                )
            )
            content = response.choices[0].message.content
            if content and len(content) > 100:
                return content, context
            logger.warning(f"[ai_report] 第{attempt}次返回内容过短({len(content or '')}字)")
        except Exception as e:
            logger.error(f"[ai_report] 第{attempt}次API调用失败: {e}")
        if attempt < 3:
            await asyncio.sleep(3)
    return None
