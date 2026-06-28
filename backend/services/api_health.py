"""外部数据源接口健康监控 (v1.7.71 改造)

从"5 分钟一次模拟探活"改为"读真实业务调用的 5 分钟滚动窗口成功率":
  - 业务能用 → 监控显示绿
  - 业务真挂 → 监控显示红
  - 不再因东财对"探活流量"风控而出现"红一片但业务正常"的误报

数据来源:
  - data_fetcher.TrackedAsyncClient 每次 httpx 请求自动 record
  - ai_analyst._get_limit_counts_akshare 每次 akshare 调用显式 record

汇总规则(每个 (source, usage) 桶):
  - 窗口内 success_rate >= 0.8 → ok
  - 0 < success_rate < 0.8     → degraded (黄, 部分失败)
  - success_rate == 0 且有调用 → fail
  - 窗口内无任何调用            → unknown
"""
import asyncio
import logging
import time
from datetime import datetime

from backend.services import api_metrics

logger = logging.getLogger(__name__)


# (source, usage, 中文标签) — 决定前端显示的固定布局
USAGE_LAYOUT: list[tuple[str, str, str]] = [
    # v1.7.75: 新浪挪到前面(已成主源), 东财降到后面(备源)
    ("sina", "realtime_quote", "实时报价"),
    ("sina", "kline", "K线"),
    ("sina", "market_indices", "大盘指数"),
    # v1.7.78: 同花顺 realhead 为"个股弹性数据"主源
    ("ths", "stock_extra", "弹性数据(量比/换手/流通市值)"),
    ("eastmoney", "realtime_quote", "实时报价"),
    ("eastmoney", "kline", "K线"),
    ("eastmoney", "sector_ranking", "板块榜"),
    ("eastmoney", "market_indices", "大盘指数"),
    ("eastmoney", "stock_extra", "弹性数据(含行业)"),
    ("akshare", "limit_up_pool", "涨停池"),
    ("akshare", "limit_down_pool", "跌停池"),
]

SOURCE_LABELS = {
    "eastmoney": "东方财富",
    "sina": "新浪财经",
    "ths": "同花顺",
    "akshare": "akshare",
}

USAGE_LABELS = {u: label for _, u, label in USAGE_LAYOUT}

# v1.7.72: "业务功能可用性"维度
# 每个功能映射到 [(主源,usage), (备源,usage), ...]
# 任一源 ok 即功能可用 (体现 fail-over 的真实业务体验)
FUNCTIONS: list[dict] = [
    # v1.7.75: 主备倒置 — 新浪升为主源, 东财降为备源
    # 原因: 东财对 prod IP 频繁风控/断连超时, 新浪稳定性更高
    {
        "id": "realtime_quote",
        "label": "实时报价",
        "sources": [("sina", "realtime_quote"), ("eastmoney", "realtime_quote")],
    },
    {
        "id": "kline",
        "label": "日K线",
        "sources": [("sina", "kline"), ("eastmoney", "kline")],
    },
    {
        "id": "market_indices",
        "label": "大盘指数",
        "sources": [("sina", "market_indices"), ("eastmoney", "market_indices")],
    },
    {
        "id": "sector_ranking",
        "label": "板块榜",
        # v1.7.x: 东财 prod IP 被封, 加腾讯备源接管榜单展示 (备源不带 bk_code, 不支持下钻)
        "sources": [("eastmoney", "sector_ranking"), ("tencent", "sector_ranking")],
    },
    {
        # v1.7.78: 个股弹性数据 (量比/换手/流通市值/涨速/行业) — 股票池表格右侧那几列
        "id": "stock_extra",
        "label": "弹性数据",
        "sources": [("ths", "stock_extra"), ("eastmoney", "stock_extra")],
    },
    {
        "id": "limit_up_pool",
        "label": "涨停池",
        "sources": [("akshare", "limit_up_pool")],
    },
    {
        "id": "limit_down_pool",
        "label": "跌停池",
        "sources": [("akshare", "limit_down_pool")],
    },
]


def _bucket_status(stats: dict | None) -> tuple[str, int, str]:
    """根据 (total, ok) 决定单个桶的 status / 中位耗时 / 最近错误。"""
    if not stats or stats["total"] == 0:
        return "unknown", 0, ""
    rate = stats["success_rate"]
    if rate >= 0.8:
        return "ok", stats["p50_latency_ms"], ""
    if rate > 0:
        # 部分失败也算 fail 在单桶维度, 但留 last_error
        return "fail", stats["p50_latency_ms"], stats["last_error"]
    return "fail", stats["p50_latency_ms"], stats["last_error"]


def _summarize_source(checks: dict) -> str:
    """根据该数据源下所有桶的 status 汇总 ok/degraded/fail/unknown。"""
    if not checks:
        return "unknown"
    statuses = [c["status"] for c in checks.values()]
    if all(s == "unknown" for s in statuses):
        return "unknown"
    valid = [s for s in statuses if s != "unknown"]
    if not valid:
        return "unknown"
    if all(s == "ok" for s in valid):
        return "ok"
    if all(s == "fail" for s in valid):
        return "fail"
    return "degraded"


def get_health_state() -> dict:
    """从 api_metrics 滚动窗口合成监控状态。"""
    raw = api_metrics.get_stats()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sources: dict[str, dict] = {
        sid: {"label": label, "summary": "unknown", "checks": {}}
        for sid, label in SOURCE_LABELS.items()
    }
    latest_ts = 0.0
    for src, usage, _label in USAGE_LAYOUT:
        s = raw.get((src, usage))
        status, latency_ms, last_error = _bucket_status(s)
        if s and s["last_ts"] > latest_ts:
            latest_ts = s["last_ts"]
        sources[src]["checks"][usage] = {
            "status": status,
            "latency_ms": latency_ms,
            "error": last_error,
            "checked_at": datetime.fromtimestamp(s["last_ts"]).strftime("%Y-%m-%d %H:%M:%S") if s else "",
            "total": s["total"] if s else 0,
            "ok": s["ok"] if s else 0,
        }
    for src in sources:
        sources[src]["summary"] = _summarize_source(sources[src]["checks"])

    # v1.7.72: 业务功能可用性 (任一备源 ok 即功能 ok, fallback 透明)
    functions: list[dict] = []
    for fn in FUNCTIONS:
        primary_label = SOURCE_LABELS.get(fn["sources"][0][0], fn["sources"][0][0])
        active_src_label = ""
        statuses = []
        for src, usage in fn["sources"]:
            st = sources.get(src, {}).get("checks", {}).get(usage, {}).get("status", "unknown")
            statuses.append((src, st))
            if st == "ok" and not active_src_label:
                active_src_label = SOURCE_LABELS.get(src, src)

        valid = [s for _, s in statuses if s != "unknown"]
        if not valid:
            fn_status = "unknown"
            reason = "5分钟内无业务调用"
        elif any(s == "ok" for s in valid):
            fn_status = "ok"
            primary_status = statuses[0][1]
            if primary_status == "ok":
                reason = f"{primary_label}主源正常"
            else:
                reason = f"{primary_label}挂, 已切到{active_src_label}备源"
        else:
            fn_status = "fail"
            if len(fn["sources"]) == 1:
                reason = f"{primary_label}挂且无备源"
            else:
                reason = "所有源都挂"

        functions.append({
            "id": fn["id"],
            "label": fn["label"],
            "status": fn_status,
            "reason": reason,
        })

    summary = {
        "total": len(functions),
        "ok": sum(1 for f in functions if f["status"] == "ok"),
        "fail": sum(1 for f in functions if f["status"] == "fail"),
        "unknown": sum(1 for f in functions if f["status"] == "unknown"),
    }

    checked_at = (
        datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d %H:%M:%S")
        if latest_ts > 0 else now_str
    )
    # v1.7.x: 同时聚合"调度任务连续失败"健康, 顶栏 popover 一站式看
    failing_tasks: list[dict] = []
    try:
        from backend.models import repository
        import asyncio as _asyncio
        # repository.list_scheduled_tasks 是 async, 调用方是 sync 函数, 跑一次 sync 等
        # 这里走简单方案: 调用方应该把这块改 async; 不过 fastapi handler 已经是 async, 这里可以
    except Exception:
        repository = None  # type: ignore

    return {
        "checked_at": checked_at,
        "sources": sources,
        "functions": functions,
        "summary": summary,
        "failing_tasks": failing_tasks,  # 由 /api/health/external 异步注入
    }


async def get_failing_tasks_async() -> list[dict]:
    """异步取调度任务里 consecutive_failures > 0 的, 给顶栏健康面板用。"""
    try:
        from backend.models import repository
        rows = await repository.list_scheduled_tasks()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        cf = int(r.get("consecutive_failures") or 0)
        if cf > 0:
            out.append({
                "job_id": r.get("job_id", ""),
                "name": r.get("name", ""),
                "consecutive_failures": cf,
                "last_error_msg": (r.get("last_error_msg") or "")[:200],
                "last_run_at": str(r.get("last_run_at")) if r.get("last_run_at") else "",
            })
    # 失败次数多的排前面
    out.sort(key=lambda x: -x["consecutive_failures"])
    return out


def get_usage_labels() -> dict:
    return USAGE_LABELS


# v1.7.71: "立即重测"按钮触发一组代表性真实调用(走 TrackedAsyncClient → 自动写入 metrics)
# 不再使用模拟探活
async def check_all_api_health():
    """触发一组代表性业务调用以快速刷新 metrics(给"立即重测"按钮用)。"""
    from backend import data_fetcher
    from backend.services import ai_analyst

    async def _safe(coro, name):
        try:
            await coro
        except Exception as e:
            logger.debug(f"[api_health] recheck {name} fail: {e}")

    # 东财 4 类调用
    await _safe(data_fetcher.get_realtime_quotes(["000001"]), "em_realtime")
    await _safe(data_fetcher.get_daily_kline("000001", days=5), "em_kline")
    # 板块榜 + 指数: 用东财行业板块和上证指数分时
    try:
        client = data_fetcher._get_client()
        await _safe(
            client.get(
                "https://push2.eastmoney.com/api/qt/clist/get"
                "?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f3,f12,f14",
                headers=data_fetcher.EM_HEADERS,
            ),
            "em_sector",
        )
        await _safe(
            client.get(
                "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
                "?secid=1.000001&fields1=f1&fields2=f51,f53&iscr=0&ndays=1",
                headers=data_fetcher.EM_HEADERS,
            ),
            "em_index",
        )
    except Exception as e:
        logger.debug(f"[api_health] recheck em aux fail: {e}")

    # 新浪报价 / K线
    try:
        client = data_fetcher._get_client()
        await _safe(
            client.get("https://hq.sinajs.cn/list=sz000001", headers=data_fetcher.HEADERS),
            "sina_realtime",
        )
        await _safe(
            client.get(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData"
                "?symbol=sz000001&scale=240&ma=no&datalen=5",
                headers=data_fetcher.HEADERS,
            ),
            "sina_kline",
        )
    except Exception as e:
        logger.debug(f"[api_health] recheck sina fail: {e}")

    # v1.7.75: 大盘指数 — 触发 get_market_indices(主新浪, 备东财, 内部已打点)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, ai_analyst.get_market_indices)
    except Exception as e:
        logger.debug(f"[api_health] recheck indices fail: {e}")

    # akshare 涨/跌停池(同步阻塞, 用线程池跑)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, ai_analyst._get_limit_counts_akshare)
    except Exception as e:
        logger.debug(f"[api_health] recheck akshare fail: {e}")
