"""一次性探针: 验证巨潮 cninfo 公告接口能否按股票代码查到风险公告。
用法: py -3 backend/scripts/diag_cninfo_probe.py
样本: 002217 合力泰(刚被ST), 看能否查到"立案/处罚/风险警示"类公告。
"""
import json
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

STOCK_JSON = "http://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"


def get_org_id(client: httpx.Client, code: str) -> str | None:
    """从巨潮股票字典拉 code -> orgId 映射。"""
    r = client.get(STOCK_JSON, headers={"User-Agent": UA}, timeout=20)
    data = r.json()
    rows = data.get("stockList", data) if isinstance(data, dict) else data
    for row in rows:
        if row.get("code") == code:
            return row.get("orgId")
    return None


def query_anns(client: httpx.Client, code: str, org_id: str,
               se_date: str = "", page: int = 1) -> list[dict]:
    """查某票公告。plate=sz/sh; se_date 形如 '2024-01-01~2026-06-20' 拉历史。"""
    plate = "sz" if code[0] in "03" else "sh"
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": "50",
        "pageNum": str(page),
        "column": plate,
        "category": "",
        "plate": plate,
        "seDate": se_date,
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    r = client.post(QUERY_URL, data=payload, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    }, timeout=20)
    j = r.json()
    return j.get("announcements") or []


import datetime as dt

# 一期硬信号词(剔除质押/诉讼等高频噪音, 留监管+审计+ST类)
RISK_KW = ("立案", "行政处罚", "事先告知书", "问询函", "关注函", "保留意见",
           "无法表示意见", "否定意见", "会计师事务所", "风险警示", "退市风险")


def _fmt(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def main():
    code = "002217"
    with httpx.Client() as client:
        org_id = get_org_id(client, code)
        print(f"orgId({code}) = {org_id}")
        if not org_id:
            print("!! 没拿到 orgId, 字典结构可能变了")
            return
        # 回测: 拉近 2 年全量公告, 翻页, 列出所有风险命中的时间线
        all_anns: list[dict] = []
        for page in range(1, 11):
            batch = query_anns(client, code, org_id,
                               se_date="2024-06-01~2026-06-20", page=page)
            if not batch:
                break
            all_anns.extend(batch)
        print(f"近2年拉到公告 {len(all_anns)} 条\n")
        hits = []
        for a in all_anns:
            title = a.get("announcementTitle", "")
            kw = [k for k in RISK_KW if k in title]
            if kw:
                hits.append((a.get("announcementTime", 0), title, kw))
        hits.sort()
        print(f"=== 规则命中的风险公告时间线 ({len(hits)} 条) ===")
        for t, title, kw in hits:
            print(f"  {_fmt(t)}  {title}  [命中: {','.join(kw)}]")


if __name__ == "__main__":
    main()
