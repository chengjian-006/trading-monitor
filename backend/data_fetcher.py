"""v1.7.x: data_fetcher.py 拆分完成 — 本文件仅作 facade re-export.

所有数据抓取实现按数据类型 / 数据源拆到 backend/fetcher/ 下:

  fetcher.codes        : code/secid 转换 (sina/em/ths/通用 6 位)
  fetcher.http_client  : TrackedAsyncClient + 共用 headers + api_metrics 打点
  fetcher.quotes       : 实时行情 (sina 主 / eastmoney 备)
  fetcher.klines       : 日 K 三源 fallback + DB 缓存
  fetcher.intraday     : 个股分时 (单只详细 + 批量 sparkline)
  fetcher.stock_extra  : 弹性数据 speed/turnover/volume_ratio/free_cap/industry
  fetcher.search       : 代码/名称搜索 (sina suggest + akshare 兜底)
  fetcher.popularity   : 人气榜
  fetcher.sectors      : 板块/概念榜 / 板块综合 / 行业 BK 映射
  fetcher.big_money    : 笔级大单分析
  fetcher.news         : 公告 + 资讯 + 人气榜聚合报告

调用方继续 `from backend import data_fetcher` 一切如旧, 不受影响.
"""
import os

# 股票数据源都在国内, 走系统 HTTP_PROXY 反而会失败 — 显式排除
_STOCK_DATA_HOSTS = (
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,emappdata.eastmoney.com,"
    "np-anotice-stock.eastmoney.com,np-listapi.eastmoney.com,"
    "sina.com.cn,sinajs.cn,hq.sinajs.cn,quotes.sina.cn,suggest3.sinajs.cn,"
    "10jqka.com.cn,d.10jqka.com.cn,stockpage.10jqka.com.cn"
)
_existing_no_proxy = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
os.environ["NO_PROXY"] = f"{_existing_no_proxy},{_STOCK_DATA_HOSTS}" if _existing_no_proxy else _STOCK_DATA_HOSTS
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import logging  # noqa: E402

logger = logging.getLogger(__name__)

# ── code/secid 转换 ──
from backend.fetcher.codes import (  # noqa: E402,F401
    _code_to_sina, _normalize_code, _code_to_em, _code_to_ths,
)

# ── HTTP client + 共用 headers + api_metrics 打点 ──
from backend.fetcher.http_client import (  # noqa: E402,F401
    HEADERS, EM_HEADERS, THS_HEADERS,
    _classify_source, _classify_usage, TrackedAsyncClient, _get_client,
)

# ── 实时行情 (sina 主 / eastmoney 备) ──
from backend.fetcher.quotes import (  # noqa: E402,F401
    _get_quotes_sina, _get_quotes_eastmoney, get_realtime_quotes,
    REALTIME_CACHE_TTL, _sf, _safe_num,
)

# ── 日 K 三源 fallback + DB 缓存 ──
from backend.fetcher.klines import (  # noqa: E402,F401
    _kline_sina, _kline_eastmoney, _kline_ths,
    _save_kline_cache, _load_kline_cache,
    get_daily_kline, get_index_kline,
)

# ── 弹性数据 ──
from backend.fetcher.stock_extra import (  # noqa: E402,F401
    _fetch_stock_extra_eastmoney, _fetch_stock_extra_ths, _fetch_stock_extra,
    _fetch_one_ths_realhead, _merge_extra,
    get_stock_extra,
    EXTRA_CACHE_TTL,
)

# ── 分时数据 ──
from backend.fetcher.intraday import (  # noqa: E402,F401
    _intraday_ths, get_intraday_data, get_batch_intraday_sparkline,
    get_cached_sparkline_speed,
)

# ── 搜索 ──
from backend.fetcher.search import (  # noqa: E402,F401
    search_stock, _search_fallback,
)

# ── 人气榜(同花顺热榜) ──
from backend.fetcher.popularity import (  # noqa: E402,F401
    get_popularity_rank, get_popularity_rank_for_codes,
    fmt_pop_rank, RANK_OUT_OF_TOP100,
)

# ── 板块/概念 ──
from backend.fetcher.sectors import (  # noqa: E402,F401
    get_stock_concepts, get_concept_板块_quotes, get_industry_bk_map,
    get_sector_ranking, get_sector_overview, get_sector_top_stocks,
)

# ── 大单 ──
from backend.fetcher.big_money import get_big_orders_today  # noqa: E402,F401

# ── 公告/资讯/人气聚合报告 ──
from backend.fetcher.news import (  # noqa: E402,F401
    get_stock_announcements, get_stock_news, get_popularity_full,
)
