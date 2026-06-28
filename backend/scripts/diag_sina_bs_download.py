# -*- coding: utf-8 -*-
"""探新浪资产负债表"下载格式"(tab分隔, 比HTML好解析), 抽商誉/其他应收款/短期借款多年值。
服务器跑: venv/bin/python -m backend.scripts.diag_sina_bs_download
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"
URL = f"http://money.finance.sina.com.cn/corp/go.php/vDOWN_BalanceSheet/displaytype/4/stockid/{CODE}/ctrl/all.phtml"
WANT = ("商誉", "其他应收款", "短期借款", "货币资金", "长期借款", "应付债券", "报告日期")


def main():
    with httpx.Client(follow_redirects=True) as client:
        r = client.get(URL, headers={"User-Agent": UA}, timeout=20)
        r.encoding = "gb2312"
        txt = r.text
        print(f"HTTP {r.status_code}  长度={len(txt)}")
        lines = txt.splitlines()
        print(f"行数={len(lines)}")
        for ln in lines:
            head = ln.split("\t")[0].strip()
            if any(w in head for w in WANT):
                cells = [c.strip() for c in ln.split("\t")]
                # 报告日期行打全部, 科目行打前6列
                print(f"  {head}: {cells[1:7] if head != '报告日期' else cells[1:9]}")


if __name__ == "__main__":
    main()
