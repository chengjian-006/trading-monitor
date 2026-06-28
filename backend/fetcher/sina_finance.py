# -*- coding: utf-8 -*-
"""新浪财务报表(下载格式)抓取 — 二期2.1财务明细红旗数据源(v1.7.x).

为什么用新浪: 巨潮免费接口只给资产负债表"摘要"(无商誉/其他应收款/短期借款), 而二期2.1的
商誉占比/其他应收款膨胀/存贷双高三红旗需要明细科目。新浪 vDOWN 下载格式(tab分隔文本)字段齐全、
生产IP实证可达(东财被封, 新浪可用, 见 [[avoid-eastmoney-api]])。

  get_balance_sheet_latest — 取最新年报(12-31)资产负债表明细 {科目名: 值(元)}

格式: 第一行=报告日期(tab分隔多期, 最新在前), 其后每行=科目名\t各期值; GBK编码, 值单位元。
"""
import logging

from backend.fetcher.http_client import _get_client

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BS_URL = ("http://money.finance.sina.com.cn/corp/go.php/vDOWN_BalanceSheet"
           "/displaytype/4/stockid/{code}/ctrl/all.phtml")


def _to_float(s: str):
    s = (s or "").strip()
    if s in ("", "--", "-", "0.00"):
        return 0.0 if s == "0.00" else None
    try:
        return float(s)
    except ValueError:
        return None


def _parse(text: str) -> tuple[dict[str, float], str]:
    """解析下载文本 → (最新12-31期 {科目:值}, 报告日期)。无年报期则取最新一期。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}, ""
    dates = [d.strip() for d in lines[0].split("\t")[1:]]
    if not dates:
        return {}, ""
    # 选最新的年报期(报告日 YYYYMMDD 或 YYYY-MM-DD, 取月日=1231); 没有则用第0期(最新)
    idx, rdate = 0, dates[0]
    for i, d in enumerate(dates):
        if d.replace("-", "").endswith("1231"):
            idx, rdate = i, d
            break

    out: dict[str, float] = {}
    for ln in lines[1:]:
        cells = ln.split("\t")
        name = cells[0].strip()
        if not name or len(cells) <= idx + 1:
            continue
        v = _to_float(cells[idx + 1])
        if v is not None:
            out[name] = v
    return out, rdate


async def get_balance_sheet_latest(code: str) -> tuple[dict[str, float], str]:
    """取某票最新年报资产负债表明细。失败返回 ({}, '')。"""
    client = _get_client()
    try:
        resp = await client.get(_BS_URL.format(code=code), headers={"User-Agent": _UA})
        text = resp.content.decode("gb2312", errors="replace")
        return _parse(text)
    except Exception as e:
        logger.warning(f"[sina] {code} 资产负债表明细拉取失败: {e}")
        return {}, ""
