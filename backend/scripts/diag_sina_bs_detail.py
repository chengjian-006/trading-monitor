# -*- coding: utf-8 -*-
"""探新浪财务明细从生产IP可达性+是否含商誉/其他应收款/短期借款(二期2.1退路)。
服务器跑: venv/bin/python -m backend.scripts.diag_sina_bs_detail
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
WANT = ("商誉", "其他应收款", "短期借款", "应收账款", "存货", "应付债券")

URLS = {
    "新浪资产负债表phtml": f"https://money.finance.sina.com.cn/corp/go.php/vFD_BalanceSheet/stockid/{CODE}/ctrl/2025/displaytype/4.phtml",
    "新浪财务摘要phtml": f"https://money.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/{CODE}.phtml",
}


def main():
    with httpx.Client(follow_redirects=True) as client:
        for name, url in URLS.items():
            try:
                r = client.get(url, headers={"User-Agent": UA}, timeout=15)
                r.encoding = "gb2312"
                txt = r.text
                hits = [w for w in WANT if w in txt]
                print(f"[{r.status_code}] {name}  长度={len(txt)}  含目标={hits}")
            except Exception as e:
                print(f"[ERR] {name}  {e}")


if __name__ == "__main__":
    main()
