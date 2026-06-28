# -*- coding: utf-8 -*-
"""二期2.1部署后验证: 生产模块跑 财务9项红旗 + 质押/减持关键词。
服务器跑: venv/bin/python -m backend.scripts.diag_21_verify
"""
import asyncio

from backend.fetcher.cninfo import get_financial_statements
from backend.fetcher.sina_finance import get_balance_sheet_latest
from backend.services.financial_risk_scanner import (
    compute_flags, sina_detail_flags, risk_score, push_key)
from backend.services.risk_announcement_scanner import match_risk


async def _fin(code, name):
    fin = await get_financial_statements(code)
    bs, _ = await get_balance_sheet_latest(code)
    flags, year = compute_flags(fin["income"], fin["cashflow"], fin["balance"])
    flags += sina_detail_flags(bs)
    fl = "; ".join(f"{f['label']}({f['strength'][0]})" for f in flags) or "无"
    print(f"  {name}({code}) {year} 分{risk_score(flags)} {'推' if push_key(flags) else '不推'}  [{fl}]")


async def fin_test():
    print("=== 财务9项红旗(巨潮6+新浪3) ===")
    await _fin("002217", "合力泰")
    await _fin("600519", "茅台")
    await _fin("300058", "蓝色光标")


def ann_test():
    print("\n=== 质押/减持关键词(应中/应放过) ===")
    cases = {
        "关于控股股东部分股份触及平仓线的公告": True,
        "关于控股股东补充质押的公告": True,
        "关于控股股东所持股份被司法冻结的公告": True,
        "关于控股股东减持公司股份的公告": True,
        "关于实际控制人拟清仓式减持的公告": True,
        "关于公司股份质押的公告": False,          # 常规质押放过
        "关于持股5%以上股东减持计划的公告": False,  # 非控股股东放过
        "关于回购公司股份的公告": False,
    }
    for t, exp in cases.items():
        tags = match_risk(t)
        got = bool(tags)
        print(f"  {'OK ' if got == exp else 'FAIL'} 期望{exp} 实得{tags}  {t[:24]}")


async def main():
    await fin_test()
    ann_test()


if __name__ == "__main__":
    asyncio.run(main())
