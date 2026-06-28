# -*- coding: utf-8 -*-
"""巨潮 data20 财务接口名探测: 批量试候选路径, 找返回200且带records的真接口。
服务器跑: venv/bin/python -m backend.scripts.diag_cninfo_fin_endpoints
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
BASE = "http://www.cninfo.com.cn/data20"

# (分组, 方法名) 候选
CANDS = [
    ("financialData", "getFinancialIndicator"),
    ("financialData", "getFinancialIndicatorData"),
    ("financialData", "getFinancialBriefData"),
    ("financialData", "getMainIndicator"),
    ("financialData", "getKeyIndicator"),
    ("financialData", "getFinancialSummary"),
    ("financialData", "getBalanceSheet"),
    ("financialData", "getProfitStatement"),
    ("financialData", "getCashFlowStatement"),
    ("financialData", "getCashFlow"),
    ("financialData", "financialIndex"),
    ("financialAnalysis", "getFinancialAnalysisData"),
    ("financialAnalysis", "getMainFinancialIndicator"),
    ("financialAnalysis", "getFinancialIndicator"),
    ("financialAnalysisData", "getFinancialAnalysisData"),
    ("financialIndex", "getFinancialIndex"),
    ("companyOverview", "getCompanyIntroduction"),  # 已知可用, 作对照
]


def main():
    with httpx.Client(follow_redirects=True) as client:
        for grp, m in CANDS:
            url = f"{BASE}/{grp}/{m}?scode={CODE}"
            try:
                r = client.get(url, headers={"User-Agent": UA}, timeout=12)
                txt = r.text[:120].replace("\n", " ")
                hit = "<== 可用" if (r.status_code == 200 and "records" in r.text and "Not Found" not in r.text) else ""
                print(f"[{r.status_code}] {grp}/{m}  {txt}  {hit}")
            except Exception as e:
                print(f"[ERR] {grp}/{m}  {e}")


if __name__ == "__main__":
    main()
