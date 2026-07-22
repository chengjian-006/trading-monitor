"""大盘情绪分 regime filter (v1.7.x).

把当下市场状态归为 friendly / neutral / hostile 三档,
盘中买点信号根据档位调整优先级:
  friendly  : 原样推送 (priority=3 → 企微)
  neutral   : 降一档    (priority=3 → 2, 只入库+前端, 不推企微)
  hostile   : 直接跳过买点的"推送", 仅入库做记录, 卖点不受影响

四个维度打分 (满分 100):
  1) 上证 close vs MA20:     强势 +35, 上方 +25, 下方 0, 下方 2% +(-15)
  2) 涨停/跌停 比:           >3 +25,  >1 +15, <0.5 -10, <0.2 -25
  3) 涨/跌家数比:            >1.5 +20, >1 +10, <0.5 -15, <0.3 -25
  4) 两市成交额(亿):         >12000 +15, >8000 +10, <6000 -10

Score >= 60: friendly; 30~60: neutral; < 30: hostile.
"""
import json
import logging
import time

from backend.models import repository

logger = logging.getLogger(__name__)

SHANGHAI_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
    "CN_MarketDataService.getKLineData"
    "?symbol=sh000001&scale=240&ma=no&datalen=25"
)
_KLINE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}

_cache: dict = {}
_CACHE_TTL = 60  # 秒


async def _fetch_shanghai_closes(n: int = 25) -> list[float]:
    """拉上证近 N 个交易日收盘价 (ASC), 失败返空。"""
    try:
        # 共享连接池(带keepalive), 不再每次新建client重做TLS握手(每扫描周期缓存失效时都会走到这)
        from backend.fetcher.http_client import _get_client
        resp = await _get_client().get(SHANGHAI_KLINE_URL, headers=_KLINE_HEADERS, timeout=8)
        text = resp.text
        start = text.find("(")
        end = text.rfind(")")
        if start < 0 or end <= start:
            return []
        data = json.loads(text[start + 1:end])
        return [float(d["close"]) for d in data if d.get("close")]
    except Exception as e:
        logger.warning(f"[regime] 上证 K 线拉取失败: {e}")
        return []


def _score_ma20(close: float, ma20: float) -> tuple[int, str]:
    if ma20 <= 0:
        return 0, "上证 MA20 数据不全"
    ratio = close / ma20
    if ratio >= 1.02:
        return 35, f"上证 {close:.0f} 强势(MA20 上方 {(ratio-1)*100:.1f}%)"
    if ratio >= 1.0:
        return 25, f"上证 {close:.0f} 在 MA20({ma20:.0f}) 上方"
    if ratio >= 0.98:
        return 0, f"上证 {close:.0f} 在 MA20({ma20:.0f}) 下方 {(1-ratio)*100:.1f}%"
    return -15, f"上证 {close:.0f} 跌破 MA20({ma20:.0f}) {(1-ratio)*100:.1f}% (空头)"


def _score_limit_ratio(limit_up: int, limit_down: int) -> tuple[int, str]:
    if limit_up + limit_down == 0:
        return 0, "涨跌停数据为零"
    if limit_down == 0:
        return 25, f"涨停 {limit_up} 家, 跌停 0 家 (极强情绪)"
    ratio = limit_up / limit_down
    if ratio >= 3:
        return 25, f"涨停 {limit_up} / 跌停 {limit_down} (比 {ratio:.1f}x 强势)"
    if ratio >= 1:
        return 15, f"涨停 {limit_up} / 跌停 {limit_down} (比 {ratio:.1f}x 偏强)"
    if ratio >= 0.5:
        return 0, f"涨停 {limit_up} / 跌停 {limit_down} (情绪平衡)"
    if ratio >= 0.2:
        return -10, f"涨停 {limit_up} / 跌停 {limit_down} (比 {ratio:.2f}x 偏弱)"
    return -25, f"涨停 {limit_up} / 跌停 {limit_down} (恐慌, 比 {ratio:.2f}x)"


def _score_breadth(up_count: int, down_count: int) -> tuple[int, str]:
    if up_count + down_count == 0:
        return 0, "涨跌家数为零"
    if down_count == 0:
        return 20, f"普涨 {up_count} / 普跌 0"
    ratio = up_count / down_count
    if ratio >= 1.5:
        return 20, f"涨 {up_count} / 跌 {down_count} (普涨, 比 {ratio:.1f}x)"
    if ratio >= 1:
        return 10, f"涨 {up_count} / 跌 {down_count} (偏多, 比 {ratio:.1f}x)"
    if ratio >= 0.5:
        return 0, f"涨 {up_count} / 跌 {down_count} (持平)"
    if ratio >= 0.3:
        return -15, f"涨 {up_count} / 跌 {down_count} (普跌, 比 {ratio:.2f}x)"
    return -25, f"涨 {up_count} / 跌 {down_count} (单边踩踏, 比 {ratio:.2f}x)"


def _score_amount(total_yi: float) -> tuple[int, str]:
    if total_yi <= 0:
        return 0, "两市成交额数据为零"
    if total_yi >= 12000:
        return 15, f"两市成交 {total_yi:.0f}亿 (放量 ≥1.2万亿)"
    if total_yi >= 8000:
        return 10, f"两市成交 {total_yi:.0f}亿 (健康)"
    if total_yi >= 6000:
        return 0, f"两市成交 {total_yi:.0f}亿 (一般)"
    return -10, f"两市成交 {total_yi:.0f}亿 (清淡 <6000亿)"


def _judge_regime(score: int) -> str:
    if score >= 60:
        return "friendly"
    if score >= 30:
        return "neutral"
    return "hostile"


def _plain_language(ma20_s: int, limit_s: int, breadth_s: int, amount_s: int) -> tuple[str, str]:
    """把四个因子的组合翻成大白话「结论 + 操作」。纯规则, 随局面变化。

    返回 (结论, 操作)。识别恐慌/普跌/分化/普涨/震荡几种典型局面, 量能作前缀修饰。
    """
    hot = limit_s >= 15          # 涨停明显多于跌停
    cold = limit_s <= -10        # 跌停偏多
    bup = breadth_s >= 10        # 个股偏多/普涨
    bdown = breadth_s <= -15     # 个股普跌
    istrong = ma20_s >= 25       # 指数站上 MA20
    iweak = ma20_s <= 0          # 指数没站稳(持平偏下或跌破)
    ibroken = ma20_s == -15      # 指数跌破 MA20(空头)
    panic = limit_s <= -25 or breadth_s <= -25
    vp = "放量" if amount_s >= 15 else ("缩量" if amount_s <= -10 else "")

    # 1) 恐慌杀跌
    if panic:
        return (f"{vp}恐慌杀跌,多数个股单边下挫",
                "仓位降到2成以下或空仓,别抄底接飞刀;等跌停明显减少、出现放量止跌反包再进,手里票破位坚决止损")
    # 2) 普跌 + 跌停偏多(资金离场)
    if bdown and cold:
        return (f"{vp}普跌,资金离场、情绪偏弱",
                "仓位压到3~5成,先砍破位补不起来的弱票;不开新仓,等指数重新站上MA20、涨跌家数转正再说")
    # 3) 分化: 强势股活跃但多数个股在跌(赚指数不赚钱)
    if hot and bdown:
        return (f"{vp}分化,赚指数不赚钱:强势股活跃但多数个股在跌",
                "只做最强主线龙头、半仓内快进快出;跟跌的弱票反弹就减、别死扛;不碰低位补涨票,这种行情它们多半继续阴跌")
    # 4) 普涨 + 情绪热
    if bup and hot:
        tail = ",但指数没站上MA20" if iweak else (",指数同步走强" if istrong else "")
        act = ("可做多但别一把满仓(6成左右),优先领涨股,留点仓位应对指数回踩" if iweak
               else "可加到6~8成、持股为主,优先强势板块领涨股,回踩不慌")
        return (f"{vp}普涨,做多氛围浓、强势股活跃{tail}", act)
    # 5) 普涨但封板不强
    if bup:
        act = ("跟随放量翻红的强势股顺势持有,设好止盈,别追高位" if amount_s >= 15
               else "小仓参与即可,量能不足、持续性存疑,冲高见好就收、不追高")
        return (f"{vp}个股普遍翻红,但封板不强、人气一般", act)
    # 6) 个股普跌但无明显跌停
    if bdown:
        return ("个股普跌、人气偏弱",
                "仓位收到5成内、以防守为主;不抄底,等放量企稳;弱票逢反弹减、强票可留")
    # 7) 多空平衡 / 震荡
    tail = "、指数偏强" if istrong else ("、指数偏弱" if ibroken else "")
    return (f"{vp}多空平衡,震荡格局{tail}",
            "区间思维、轻仓灵活;强势股回踩低吸、冲高减,别追涨杀跌;等量价突破方向明确再加仓")


async def compute_regime(force: bool = False) -> dict:
    """主入口. 60s 缓存. 返回:
      { regime, score, factors: [{name, score, reason}, ...], updated_at }
    """
    now = time.time()
    cached = _cache.get("data")
    if not force and cached and now - cached.get("_ts", 0) < _CACHE_TTL:
        return cached

    # 1) 上证 MA20(已收盘日) — 新浪日K末条盘中仍是昨收, 取最近20条算均
    closes = await _fetch_shanghai_closes(25)
    if len(closes) >= 20:
        ma20 = sum(closes[-20:]) / 20
        daily_close = closes[-1]
    elif closes:
        ma20 = sum(closes) / len(closes)
        daily_close = closes[-1]
    else:
        ma20 = 0.0
        daily_close = 0.0

    # 2-4) 来自 market_overview
    overview = await repository.get_market_overview()
    market_stats = (overview or {}).get("market_stats") or {}
    a_indices = (overview or {}).get("a_indices") or []
    # 上证 vs MA20 的"现价"用盘中实时(a_indices 已是实时), 取不到才回退日K末条(昨收)。
    # 修复: 原来用日K末条(盘中=昨收)判强弱, 普涨日仍被判"跌破MA20·空头", summary 误报"指数偏弱"。
    sh_live = 0.0
    for idx in a_indices:
        if idx.get("name") == "上证指数" and idx.get("price"):
            sh_live = float(idx.get("price") or 0)
            break
    close = sh_live if sh_live > 0 else daily_close
    ma20_score, ma20_reason = _score_ma20(close, ma20)
    limit_up = int(market_stats.get("limit_up") or 0)
    limit_down = int(market_stats.get("limit_down") or 0)
    up_count = int(market_stats.get("up_count") or 0)
    down_count = int(market_stats.get("down_count") or 0)
    # 两市成交额: 上证 + 深证, _get_indices_sina 已把单位换算为"亿"
    total_yi = 0.0
    for idx in a_indices:
        if idx.get("name") in ("上证指数", "深证成指"):
            total_yi += float(idx.get("amount") or 0)

    limit_score, limit_reason = _score_limit_ratio(limit_up, limit_down)
    breadth_score, breadth_reason = _score_breadth(up_count, down_count)
    amount_score, amount_reason = _score_amount(total_yi)

    total = max(0, min(100, 50 + ma20_score + limit_score + breadth_score + amount_score))
    regime = _judge_regime(total)
    summary, action = _plain_language(ma20_score, limit_score, breadth_score, amount_score)

    payload = {
        "regime": regime,
        "score": total,
        "summary": summary,   # 大白话结论(随局面变化)
        "action": action,     # 大白话操作提示
        "factors": [
            {"name": "上证 vs MA20", "score": ma20_score, "reason": ma20_reason},
            {"name": "涨停/跌停", "score": limit_score, "reason": limit_reason},
            {"name": "涨跌家数", "score": breadth_score, "reason": breadth_reason},
            {"name": "两市成交", "score": amount_score, "reason": amount_reason},
        ],
        "raw": {
            "sh_close": round(close, 2),
            "sh_ma20": round(ma20, 2),
            "limit_up": limit_up, "limit_down": limit_down,
            "up_count": up_count, "down_count": down_count,
            "total_amount_yi": round(total_yi, 0),
        },
        "_ts": now,
    }
    _cache["data"] = payload
    logger.info(
        f"[regime] score={total} regime={regime} | "
        f"MA20({ma20_score}) Limit({limit_score}) Breadth({breadth_score}) Amount({amount_score})"
    )
    return payload


def adjusted_priority_for_buy(base_priority: int, regime: str) -> int:
    """给定一个买点信号的基础优先级, 根据 regime 调整:
      friendly: 原样
      neutral : priority 3 → 2 (DB+WS, 不推企微)
      hostile : priority 任意 → 1 (仅 DB, 既不推 WS 也不推企微; 仍留痕供 outcome 回填/学习)
    """
    if regime == "hostile":
        return 1
    if regime == "neutral" and base_priority >= 3:
        return 2
    return base_priority
