# -*- coding: utf-8 -*-
"""验证新浪明细解析 + 二期2.1三红旗(商誉占比/其他应收款膨胀/存贷双高)。
服务器跑: venv/bin/python -m backend.scripts.diag_sina_flags_test
"""
import asyncio

from backend.fetcher.sina_finance import get_balance_sheet_latest


def _pick(bs: dict, *names):
    for n in names:
        if n in bs:
            return bs[n]
    return None


def detail_flags(bs: dict) -> list[str]:
    flags = []
    equity = _pick(bs, "所有者权益(或股东权益)合计", "归属于母公司股东权益合计")
    assets = _pick(bs, "资产总计")
    goodwill = _pick(bs, "商誉")
    other_recv = _pick(bs, "其他应收款(合计)", "其他应收款（合计）", "其他应收款")
    cash = _pick(bs, "货币资金")
    st_loan = _pick(bs, "短期借款") or 0
    lt_loan = _pick(bs, "长期借款") or 0
    bond = _pick(bs, "应付债券") or 0

    if goodwill and equity and equity > 0 and goodwill / equity > 0.30:
        flags.append(f"商誉占比{goodwill/equity*100:.0f}%>30%")
    if other_recv and assets and other_recv / assets > 0.10:
        flags.append(f"其他应收款占总资产{other_recv/assets*100:.0f}%>10%")
    if cash and assets:
        debt = st_loan + lt_loan + bond
        if cash / assets > 0.15 and debt / assets > 0.30:
            flags.append(f"存贷双高(货币{cash/assets*100:.0f}%+有息负债{debt/assets*100:.0f}%)")
    return flags


async def run(code, name):
    bs, rdate = await get_balance_sheet_latest(code)
    print(f"\n=== {name}({code}) 报告期={rdate} 科目数={len(bs)} ===")
    print("  含'权益'科目名:", [k for k in bs if "权益" in k])
    for k in ("资产总计", "所有者权益（或股东权益）合计", "商誉", "其他应收款(合计)",
              "货币资金", "短期借款", "长期借款", "应付债券"):
        print(f"  {k}: {bs.get(k)}")
    eq = _pick(bs, "所有者权益(或股东权益)合计", "归属于母公司股东权益合计")
    ta = bs.get("资产总计")
    gw = bs.get("商誉") or 0
    orr = _pick(bs, "其他应收款(合计)", "其他应收款") or 0
    ca = bs.get("货币资金") or 0
    debt = (bs.get("短期借款") or 0) + (bs.get("长期借款") or 0) + (bs.get("应付债券") or 0)
    print(f"  净资产={eq}")
    if eq and ta:
        print(f"  比率: 商誉/净资产={gw/eq*100:.1f}% 其他应收/总资产={orr/ta*100:.1f}% "
              f"货币/总资产={ca/ta*100:.1f}% 有息负债/总资产={debt/ta*100:.1f}%")
    print(f"  → 明细红旗: {detail_flags(bs) or '无'}")


async def main():
    await run("002217", "合力泰")
    await run("600519", "贵州茅台")
    await run("300058", "蓝色光标(高商誉)")
    await run("600518", "ST康美(存贷双高典型)")


if __name__ == "__main__":
    asyncio.run(main())
