"""部署后只读探针: 确认生产服务器能连通巨潮 cninfo(东财在生产被封, cninfo 需实证)。
不推送、不写库。服务器上跑: venv/bin/python backend/scripts/diag_cninfo_server_reach.py
"""
import asyncio

from backend.fetcher.cninfo import get_org_id_map, query_announcements
from backend.services.risk_announcement_scanner import match_risk


async def main():
    org_map = await get_org_id_map()
    print("orgId 字典条数:", len(org_map))
    code = "002217"  # 合力泰
    org_id = org_map.get(code)
    print(f"{code} orgId:", org_id)
    if not org_id:
        print("!! 字典没拿到, 数据源可能不通")
        return
    anns = await query_announcements(code, org_id, days=30)
    print(f"近30天公告 {len(anns)} 条; 命中风险的:")
    for a in anns:
        tags = match_risk(a["title"])
        if tags:
            print(f"  {a['date']} [{'/'.join(tags)}] {a['title']}")


if __name__ == "__main__":
    asyncio.run(main())
