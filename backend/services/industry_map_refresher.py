# -*- coding: utf-8 -*-
"""全市场行业映射刷新 (v1.7.598) — 问财拉「全部A股所属同花顺行业」写 cfzy_sys_industry_map。

供板块共振·禁补仓提示(sector_cocrash_guard)算各行业大跌占比。选问财的原因(0711实测):
问财=全覆盖5533只+同花顺三级行业(粒度正合适); 东财 prod 被封且本机也开始不稳;
新浪行业仅覆盖2965只且分类陈旧(天华新能还挂在改名前的电子信息)。

每周日19:20跑(行业归属极少变动, 低频=少撞同花顺风控); pywencai 同步阻塞走 to_thread,
带登录 cookie(复用博主续签那份)降风控; 失败/结果过少一律保留旧映射(只 upsert 不删)。
config.wencai_screening.enabled=False 时早返回(部署机未装 Node/pywencai 不刷告警)。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_QUERY = "全部A股的所属同花顺行业"
_MIN_ROWS = 3000     # 结果少于这个数视为拉挂了(全A~5000+), 不写库保留旧映射


def rows_from_wencai_df(df) -> list[tuple[str, str]]:
    """问财 DataFrame → [(code, industry)]。code 优先 6 位纯数字 'code' 列, 退回'股票代码'前6位;
    行业为空/NaN 的行跳过。纯函数可单测。"""
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out: list[tuple[str, str]] = []
    for _, raw in df.iterrows():
        row = raw.to_dict()
        code = str(row.get("code") or "").strip()
        if not re.match(r"^\d{6}$", code):
            m = re.match(r"(\d{6})", str(row.get("股票代码") or ""))
            code = m.group(1) if m else ""
        if not code:
            continue
        ind = row.get("所属同花顺行业")
        if ind is None or (isinstance(ind, float) and ind != ind):
            continue
        s = str(ind).strip()
        if not s or s.lower() == "nan":
            continue
        out.append((code, s[:64]))
    return out


async def run_industry_map_refresh():
    """每周日19:20: 问财拉全A行业归属, upsert 进 cfzy_sys_industry_map(失败保留旧数据)。"""
    import asyncio

    from backend.core.config import load_config
    from backend.models import repository

    if not load_config().get("wencai_screening", {}).get("enabled"):
        logger.info("[industry_map] wencai_screening 未启用, 跳过刷新(沿用旧映射)")
        return
    try:
        import pywencai
    except ImportError as e:
        logger.warning(f"[industry_map] pywencai 未安装, 跳过刷新: {e}")
        return

    from backend.fetcher.wencai_screener import _login_cookie
    cookie = _login_cookie()

    def _run():
        if cookie:
            return pywencai.get(question=_QUERY, loop=True, cookie=cookie)
        return pywencai.get(question=_QUERY, loop=True)

    try:
        df = await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning(f"[industry_map] 问财拉取失败(保留旧映射): {type(e).__name__}: {e}")
        return
    rows = rows_from_wencai_df(df)
    if len(rows) < _MIN_ROWS:
        logger.warning(f"[industry_map] 结果过少({len(rows)}<{_MIN_ROWS}), 疑似拉挂, 保留旧映射")
        return
    n = await repository.upsert_industry_map(rows)
    logger.info(f"[industry_map] 已刷新 {len(rows)} 只(受影响{n}), "
                f"{len({i for _, i in rows})} 个行业")
