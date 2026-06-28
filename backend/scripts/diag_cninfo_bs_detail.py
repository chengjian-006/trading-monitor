# -*- coding: utf-8 -*-
"""探巨潮明细资产负债表: 摘要版(getBalanceSheets)没有商誉/其他应收款/短期借款,
找有这些科目的明细接口, 支撑二期2.1红旗(商誉占比/其他应收款膨胀/存贷双高)。
服务器跑: venv/bin/python -m backend.scripts.diag_cninfo_bs_detail
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
BASE = "http://www.cninfo.com.cn/data20/financialData"
WANT = ("商誉", "其他应收款", "短期借款", "应收账款", "存货", "应付债券", "一年内到期")

# 候选明细接口名 + 参数(sign=1已知有效)
CANDS = [
    ("getBalanceSheets", {"scode": CODE, "sign": "1", "type": "2"}),
    ("getBalanceSheets", {"scode": CODE, "sign": "1", "detail": "1"}),
    ("getBalanceSheetDetail", {"scode": CODE, "sign": "1"}),
    ("getBalanceSheetData", {"scode": CODE, "sign": "1"}),
    ("getBalanceSheetsDetail", {"scode": CODE, "sign": "1"}),
    ("getZcfzbDetail", {"scode": CODE, "sign": "1"}),
    ("getAssetsDetail", {"scode": CODE, "sign": "1"}),
    ("getFinancialDetail", {"scode": CODE, "sign": "1"}),
]


def _rows_index(recs):
    rows = recs[0].get("year", []) if recs else []
    return [r.get("index") for r in rows if r.get("index")]


def main():
    with httpx.Client(follow_redirects=True) as client:
        for m, params in CANDS:
            url = f"{BASE}/{m}"
            try:
                r = client.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
                if r.status_code != 200 or "records" not in r.text:
                    print(f"[{r.status_code}] {m} {params}  {r.text[:60]}")
                    continue
                recs = (r.json().get("data") or {}).get("records") or []
                idx = _rows_index(recs)
                hits = [w for w in WANT if any(w in i for i in idx)]
                print(f"[200] {m} {params}  科目数={len(idx)}  含目标={hits}")
                if hits:
                    print(f"      全部科目: {idx}")
            except Exception as e:
                print(f"[ERR] {m} {params}  {e}")


if __name__ == "__main__":
    main()
