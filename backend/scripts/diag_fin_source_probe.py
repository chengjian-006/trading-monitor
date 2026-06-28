# -*- coding: utf-8 -*-
"""二期财务数据源探针: 验证能否免费拿到结构化三大报表/财务指标。
py -3.13 backend/scripts/diag_fin_source_probe.py
样本 002217 合力泰。探: A.巨潮几个候选财务接口  B.网易财经CSV财报(备选)。
"""
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CODE = "002217"


def probe_cninfo(client):
    print("=== A. 巨潮候选财务接口 ===")
    cands = [
        ("公司概况", f"http://www.cninfo.com.cn/data20/companyOverview/getCompanyIntroduction?scode={CODE}"),
        ("主要指标", f"http://www.cninfo.com.cn/data20/financialData/getIndicatorData?scode={CODE}"),
        ("财务摘要", f"http://www.cninfo.com.cn/data20/financialData/financialMainData?scode={CODE}"),
    ]
    for name, url in cands:
        try:
            r = client.get(url, headers={"User-Agent": UA}, timeout=15)
            body = r.text[:160].replace("\n", " ")
            print(f"  [{name}] HTTP {r.status_code}  {body}")
        except Exception as e:
            print(f"  [{name}] 失败 {e}")


def probe_163(client):
    print("\n=== B. 网易财经 CSV 财报(备选) ===")
    # zcfzb=资产负债表 lrb=利润表 xjllb=现金流量表 cwbbzy=财务报表摘要(含主要指标)
    sheets = {
        "财务摘要cwbbzy": f"http://quotes.money.163.com/service/cwbbzy_{CODE}.html",
        "资产负债zcfzb": f"http://quotes.money.163.com/service/zcfzb_{CODE}.html",
        "利润表lrb": f"http://quotes.money.163.com/service/lrb_{CODE}.html",
        "现金流xjllb": f"http://quotes.money.163.com/service/xjllb_{CODE}.html",
    }
    for name, url in sheets.items():
        try:
            r = client.get(url, headers={"User-Agent": UA}, timeout=15)
            r.encoding = "gbk"
            lines = r.text.strip().splitlines()
            print(f"  [{name}] HTTP {r.status_code}  行数={len(lines)}")
            if lines:
                # 表头(报告期)取前3列, 再抽1-2个关键科目行
                hdr = lines[0].split(",")[:5]
                print(f"     报告期头: {hdr}")
                for ln in lines[1:]:
                    cells = ln.split(",")
                    key = cells[0].strip()
                    if any(k in key for k in ("经营活动产生的现金流量净额", "归属于母公司",
                                              "货币资金", "商誉", "营业总收入", "其他应收款")):
                        print(f"     {key}: {cells[1:4]}")
        except Exception as e:
            print(f"  [{name}] 失败 {e}")


def main():
    with httpx.Client(follow_redirects=True) as client:
        probe_cninfo(client)
        probe_163(client)


if __name__ == "__main__":
    main()
