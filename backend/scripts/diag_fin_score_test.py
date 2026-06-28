# -*- coding: utf-8 -*-
"""二期财务红旗打分回测: 在合力泰(应满堂红)+茅台(健康对照)上验证6项红旗口径。
服务器跑: venv/bin/python -m backend.scripts.diag_fin_score_test
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE = "http://www.cninfo.com.cn/data20/financialData"


def _fetch(client, method, scode):
    r = client.get(f"{BASE}/{method}", params={"scode": scode, "sign": "1"},
                   headers={"User-Agent": UA}, timeout=15)
    recs = r.json().get("data", {}).get("records", [])
    rows = recs[0].get("year", []) if recs else []   # 年报口径
    # 转 {科目: {年: 值}}
    out = {}
    for row in rows:
        idx = row.get("index")
        if not idx:
            continue
        out[idx] = {k: v for k, v in row.items() if k != "index"}
    return out


def _val(table, key, year):
    return (table.get(key) or {}).get(year)


def compute_flags(income, cashflow, balance):
    """返回 (flags:list[(标签,强度,明细)], years_desc)。强度: strong/medium。"""
    # 最新两个有数据的年报年份
    years = sorted({y for d in income.values() for y, v in d.items() if v is not None}, reverse=True)
    if not years:
        return [], []
    y0 = years[0]
    y1 = years[1] if len(years) > 1 else None

    flags = []
    equity = _val(balance, "所有者权益", y0)
    np0 = _val(income, "归属母公司净利润", y0)
    np1 = _val(income, "归属母公司净利润", y1) if y1 else None
    ocf = _val(cashflow, "经营活动产生的现金流量净额", y0)
    retained = _val(balance, "未分配利润", y0)
    tot_liab = _val(balance, "总负债", y0)
    tot_asset = _val(balance, "总资产", y0)
    rev0 = _val(income, "营业总收入", y0)
    rev1 = _val(income, "营业总收入", y1) if y1 else None

    # 强红旗
    if equity is not None and equity < 0:
        flags.append(("资不抵债", "strong", f"所有者权益 {equity:.0f}万<0"))
    if np0 is not None and np1 is not None and np0 < 0 and np1 < 0:
        flags.append(("连续亏损", "strong", f"归母净利 {y1}={np1:.0f} / {y0}={np0:.0f} 均<0"))
    if np0 is not None and ocf is not None and np0 > 0 and ocf < 0:
        flags.append(("利润现金流背离", "strong", f"{y0}归母净利+{np0:.0f}万 但经营现金流{ocf:.0f}万<0"))
    # 中红旗
    if retained is not None and retained < 0:
        flags.append(("累计亏损", "medium", f"未分配利润 {retained:.0f}万<0"))
    if tot_liab and tot_asset and tot_asset != 0:
        dar = tot_liab / tot_asset
        if dar > 0.70:
            flags.append(("高杠杆", "medium", f"资产负债率 {dar*100:.0f}%>70%"))
    if rev0 is not None and rev1 not in (None, 0):
        drop = (rev1 - rev0) / abs(rev1)
        if drop > 0.50:
            flags.append(("营收断崖", "medium", f"营收 {y1}={rev1:.0f}→{y0}={rev0:.0f} 降{drop*100:.0f}%"))

    return flags, years


def should_push(flags):
    strong = [f for f in flags if f[1] == "strong"]
    medium = [f for f in flags if f[1] == "medium"]
    return bool(strong) or len(medium) >= 2


def run(client, code, name):
    income = _fetch(client, "getIncomeStatement", code)
    cashflow = _fetch(client, "getCashFlowStatement", code)
    balance = _fetch(client, "getBalanceSheets", code)
    flags, years = compute_flags(income, cashflow, balance)
    print(f"\n=== {name}({code}) 年报年份: {years} ===")
    if not flags:
        print("  无红旗")
    for label, strength, detail in flags:
        mark = "🔴强" if strength == "strong" else "🟡中"
        print(f"  {mark} {label}: {detail}")
    print(f"  → 推送判定: {'推' if should_push(flags) else '不推'}")


def main():
    with httpx.Client(follow_redirects=True) as client:
        run(client, "002217", "合力泰(应满堂红)")
        run(client, "600519", "贵州茅台(健康对照)")
        run(client, "000001", "平安银行(对照)")


if __name__ == "__main__":
    main()
