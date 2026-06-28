# -*- coding: utf-8 -*-
"""巨潮 data20 财务报表接口参数破解: getCashFlowStatement 存在但 validate fail, 试参数组合。
服务器跑: venv/bin/python -m backend.scripts.diag_cninfo_fin_params
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
ORG = "9900004262"
BASE = "http://www.cninfo.com.cn/data20/financialData"

# 已知存在的报表接口 + 猜测同族
METHODS = ["getCashFlowStatement", "getBalanceSheetStatement", "getIncomeStatement",
           "getProfitStatement", "getBalanceSheet"]

PARAM_SETS = [
    {"scode": CODE, "orgId": ORG},
    {"scode": CODE, "type": "1"},
    {"scode": CODE, "reportType": "1"},
    {"scode": CODE, "rtype": "1"},
    {"scode": CODE, "sign": "1"},
    {"scode": CODE, "companyType": "1"},
]


def main():
    with httpx.Client(follow_redirects=True) as client:
        for m in METHODS:
            for ps in PARAM_SETS:
                url = f"{BASE}/{m}"
                try:
                    r = client.get(url, params=ps, headers={"User-Agent": UA}, timeout=12)
                    txt = r.text[:140].replace("\n", " ")
                    flag = ""
                    if r.status_code == 200 and "records" in r.text:
                        flag = "  <=== 成功!"
                    elif "validate" in r.text:
                        flag = "  (存在,验证未过)"
                    print(f"[{r.status_code}] {m} {ps}  {txt}{flag}")
                except Exception as e:
                    print(f"[ERR] {m} {ps}  {e}")
            print()


if __name__ == "__main__":
    main()
