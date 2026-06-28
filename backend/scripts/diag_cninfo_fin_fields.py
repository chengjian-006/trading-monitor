# -*- coding: utf-8 -*-
"""巨潮 data20 财务表: 定位资产负债表接口名 + dump 三表字段, 确认红旗指标够用。
服务器跑: venv/bin/python -m backend.scripts.diag_cninfo_fin_fields
"""
import json
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
BASE = "http://www.cninfo.com.cn/data20/financialData"

BS_CANDS = ["getBalanceStatement", "getBalanceSheetData", "getBalanceSheets",
            "getAssetLiabilityStatement", "getBalance", "getBalanceSheetStatement",
            "getDebtStatement", "getZcfzStatement"]


def fetch(client, method, params):
    r = client.get(f"{BASE}/{method}", params=params, headers={"User-Agent": UA}, timeout=15)
    return r


def main():
    with httpx.Client(follow_redirects=True) as client:
        print("=== 定位资产负债表接口(sign=1) ===")
        bs_method = None
        for m in BS_CANDS:
            r = fetch(client, m, {"scode": CODE, "sign": "1"})
            ok = r.status_code == 200 and "records" in r.text
            print(f"[{r.status_code}] {m}  {'<=== 成功' if ok else r.text[:70]}")
            if ok and not bs_method:
                bs_method = m

        print("\n=== 三表字段 + 最近报告期(确认红旗指标) ===")
        targets = {
            "利润表 getIncomeStatement": "getIncomeStatement",
            "现金流 getCashFlowStatement": "getCashFlowStatement",
        }
        if bs_method:
            targets[f"资产负债 {bs_method}"] = bs_method
        for label, m in targets.items():
            r = fetch(client, m, {"scode": CODE, "sign": "1"})
            try:
                recs = r.json().get("data", {}).get("records", [])
            except Exception as e:
                print(f"\n[{label}] 解析失败 {e}"); continue
            print(f"\n[{label}] 报告期数={len(recs)}")
            if recs:
                r0 = recs[0]
                # 找日期字段
                date_keys = [k for k in r0 if any(s in k.lower() for s in ("date", "year", "rdate", "enddate"))]
                print(f"  日期字段: {[(k, r0[k]) for k in date_keys]}")
                print(f"  字段总数: {len(r0)}; 全部key:")
                print("   ", list(r0.keys()))
    print("\n(把上面的中文/字段名贴回, 据此映射红旗指标)")


if __name__ == "__main__":
    main()
