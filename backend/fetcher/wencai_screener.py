# -*- coding: utf-8 -*-
"""同花顺问财(iwencai)自然语言选股采集 — 问财候选榜 (v1.7.540).

问财无官方开放 API, 这里走社区逆向库 pywencai:
  - pywencai.get(question="换手率大于5% 且 创60日新高 ...") → DataFrame(选股) 或 dict(个股)
  - token(hexin-v) 由 JS 动态生成, pywencai 经 PyExecJS 调本机 Node 算出来, 故部署机必须装 Node。
  - 依赖未装(import 失败)或 Node 缺失时抛 WencaiFetchError, 上层据此早返回/告警, 不刷异常。

不同选股语句返回的列不一样(回踩型有「缩量/区间涨跌幅」, 题材型有「市值/行业」),
故 _normalize_rows 只锚定 代码/名称/最新价/涨跌幅 四个核心列(按列名前缀容错匹配),
另挑几个同花顺特有标签列(技术形态/买入信号/所属概念/换手率/成交额)塞进 extra, 整行原样不存(概念资讯含大段新闻 JSON, 太重)。
"""

import logging
import re

logger = logging.getLogger(__name__)

# extra 里收录的同花顺特有标签列(按列名前缀匹配, 去掉 [20260630] 这类日期后缀)
_EXTRA_PREFIXES = {
    "技术形态": "tech_pattern",
    "买入信号inter": "buy_signal",
    "所属概念": "concepts",
    "所属同花顺行业": "industry",
    "换手率": "turnover",
    "成交额": "amount",
    "a股市值(不含限售股)": "free_cap",
}


class WencaiFetchError(Exception):
    """问财拉取失败(依赖缺失 / Node 缺失 / 网络 / 接口异常 / token 失效)。

    供上层 wencai_scanner 做"连续失败→飞书告警"的兜底, 区别于"拉取成功但无结果"(返回 [])。"""


def _strip_date_suffix(col: str) -> str:
    """'换手率[20260630]' → '换手率'; '区间涨跌幅:前复权[20260601-20260630]' → '区间涨跌幅:前复权'。"""
    return re.sub(r"\[[0-9\-]+\]$", "", col).strip()


def _to_float(v):
    try:
        if v is None or v == "":
            return None
        return round(float(v), 4)
    except (ValueError, TypeError):
        return None


def _pick(row: dict, base_to_col: dict, *bases):
    """按"去日期后缀的列名"找原始值, 命中第一个 base 即返回。"""
    for b in bases:
        col = base_to_col.get(b)
        if col is not None:
            return row.get(col)
    return None


def _normalize_rows(df, limit: int) -> list[dict]:
    """问财 DataFrame → [{code, name, price, pct_change, extra}], 截前 limit 只。"""
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    # 列名(去日期后缀) → 原始列名, 供按业务名取值
    base_to_col: dict[str, str] = {}
    for col in df.columns:
        base_to_col.setdefault(_strip_date_suffix(col), col)

    out: list[dict] = []
    for _, raw in df.head(limit).iterrows():
        row = raw.to_dict()
        # 代码: 优先 6 位纯数字的 'code' 列, 退回 '股票代码'(920222.BJ)取前 6 位
        code = row.get("code") or ""
        if not code:
            sc = str(row.get("股票代码") or "")
            m = re.match(r"(\d{6})", sc)
            code = m.group(1) if m else sc
        code = str(code).strip()
        if not re.match(r"^\d{6}$", code):
            continue
        name = str(_pick(row, base_to_col, "股票简称") or "").strip()
        price = _to_float(_pick(row, base_to_col, "最新价", "收盘价:前复权"))
        pct = _to_float(_pick(row, base_to_col, "最新涨跌幅", "涨跌幅:前复权"))

        extra: dict[str, object] = {}
        for base, key in _EXTRA_PREFIXES.items():
            val = base_to_col.get(base)
            if val is None:
                continue
            v = row.get(val)
            if v is None or (isinstance(v, float) and v != v):  # 跳过 None / NaN
                continue
            if key in ("turnover", "amount", "free_cap"):
                fv = _to_float(v)
                if fv is not None:
                    extra[key] = fv
            else:
                s = str(v).strip()
                if s and s.lower() != "nan":
                    extra[key] = s[:120]  # 标签列截断, 防超长

        out.append({"code": code, "name": name, "price": price,
                    "pct_change": pct, "extra": extra})
    return out


async def fetch_wencai(query: str, limit: int = 50) -> list[dict]:
    """跑一条问财选股语句, 返回归一化候选列表(无结果返回 [], 失败抛 WencaiFetchError)。

    pywencai.get 是同步阻塞(内部起 Node 子进程算 token), 故经 asyncio.to_thread 丢线程池跑,
    避免卡住事件循环(同 model_winrate 重算的 to_thread 思路)。
    """
    import asyncio

    try:
        import pywencai  # noqa: F401
    except ImportError as e:
        raise WencaiFetchError(f"pywencai 未安装: {e}")

    def _run():
        return pywencai.get(question=query, loop=False)

    try:
        df = await asyncio.to_thread(_run)
    except Exception as e:
        raise WencaiFetchError(f"问财查询异常: {type(e).__name__}: {e}")

    return _normalize_rows(df, limit)
