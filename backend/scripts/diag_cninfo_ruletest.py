"""回测精准版风险规则在合力泰上的命中(去噪后)。
py -3 backend/scripts/diag_cninfo_ruletest.py
"""
import datetime as dt
import httpx

UA = "Mozilla/5.0"
URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"


def match_risk(title: str) -> list[str]:
    """返回命中的风险标签; 空=非风险。带方向/子串护栏。"""
    t = title
    tags = []

    # 方向护栏: "撤销...风险警示/退市风险" = 摘帽利好, 直接放过
    is_revoke = ("撤销" in t and ("风险警示" in t or "退市风险" in t)) or "撤销退市风险" in t

    # 1. 监管立案/处罚 —— 最强信号
    if "立案" in t:
        tags.append("立案调查")
    if "行政处罚" in t or "事先告知书" in t or "行政监管措施" in t:
        tags.append("行政处罚")
    # 2. 交易所函件
    if "问询函" in t or "关注函" in t:
        tags.append("交易所问询")
    # 3. 非标审计意见 —— 子串护栏: 排除"无保留意见"; 收"非标"明示
    if ("保留意见" in t and "无保留意见" not in t) or "无法表示意见" in t \
       or "否定意见" in t or "非标意见" in t or "非标准审计意见" in t:
        tags.append("非标审计意见")
    # 4. 换所 —— 只认变更/解聘/改聘, 排除续聘/选聘/履职/评估等例行
    if "会计师事务所" in t and any(k in t for k in ("变更", "解聘", "改聘")) \
       and not any(k in t for k in ("续聘", "选聘", "履职", "评估", "意见书")):
        tags.append("变更会计师事务所")
    # 5. 风险警示 —— "实施...风险警示/退市风险"且非摘帽(撤销/申请撤销)
    if not is_revoke and "撤销" not in t and "实施" in t \
       and ("风险警示" in t or "退市风险" in t):
        tags.append("被实施风险警示")

    return tags


def pull(code: str, org_id: str, plate: str) -> list[dict]:
    seen = {}
    for pg in range(1, 9):
        p = {"stock": f"{code},{org_id}", "tabName": "fulltext", "pageSize": "30",
             "pageNum": str(pg), "column": plate, "plate": plate,
             "seDate": "2024-06-01~2026-06-20", "isHLtitle": "false"}
        r = httpx.post(URL, data=p, headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
        for a in (r.json().get("announcements") or []):
            aid = a.get("announcementId")
            if aid and aid not in seen:
                seen[aid] = a
    return list(seen.values())


def main():
    anns = pull("002217", "9900004262", "sz")
    print(f"去重公告 {len(anns)} 条\n")
    hits, skipped = [], []
    for a in anns:
        title = a.get("announcementTitle", "")
        ms = a.get("announcementTime", 0)
        tags = match_risk(title)
        # 收集被精准规则放过、但旧粗规则会误中的(撤销/无保留/续聘)用于核对
        old_kw = any(k in title for k in ("立案", "保留意见", "风险警示", "退市风险",
                                          "会计师事务所", "问询函", "处罚", "事先告知书"))
        if tags:
            hits.append((ms, title, tags))
        elif old_kw:
            skipped.append((ms, title))
    hits.sort(); skipped.sort()
    print(f"=== 精准规则命中 {len(hits)} 条(真风险时间线)===")
    for ms, t, tags in hits:
        d = dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        print(f"  {d}  {t}  [{','.join(tags)}]")
    print(f"\n=== 被护栏正确放过 {len(skipped)} 条(旧粗规则会误报)===")
    for ms, t in skipped:
        d = dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        print(f"  {d}  {t}")


if __name__ == "__main__":
    main()
