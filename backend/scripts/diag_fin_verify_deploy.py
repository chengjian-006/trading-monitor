# -*- coding: utf-8 -*-
"""二期部署后验证: 用生产模块对合力泰/茅台/平安跑真实评分 + 查表/cron。
服务器跑: venv/bin/python -m backend.scripts.diag_fin_verify_deploy
"""
import asyncio
import json

import pymysql

from backend.fetcher.cninfo import get_financial_statements
from backend.services.financial_risk_scanner import compute_flags, push_key, risk_score


async def _one(code, name):
    fin = await get_financial_statements(code)
    flags, year = compute_flags(fin["income"], fin["cashflow"], fin["balance"])
    key = push_key(flags)
    fl = "; ".join(f"{f['label']}({f['strength']})" for f in flags) or "无红旗"
    print(f"  {name}({code}) {year}年报 风险分{risk_score(flags)} → {'推' if key else '不推'}  [{fl}]")


async def live_test():
    print("=== 生产模块活体评分 ===")
    await _one("002217", "合力泰")
    await _one("600519", "贵州茅台")
    await _one("000001", "平安银行")


def db_check():
    print("\n=== DB 核对 ===")
    c = json.load(open("config.json"))["database"]
    conn = pymysql.connect(host=c["host"], port=c.get("port", 3306),
                           user=c["user"], password=c["password"], db=c["db"])
    cur = conn.cursor()
    cur.execute("SHOW TABLES LIKE 'cfzy_biz_fin_risk'")
    print("财务红旗表存在:", cur.fetchone() is not None)
    cur.execute("SELECT job_id, enabled, schedule_config, handler "
                "FROM cfzy_sys_scheduled_tasks WHERE job_id='fin_risk_scan'")
    print("定时任务:", cur.fetchone())
    conn.close()


if __name__ == "__main__":
    asyncio.run(live_test())
    db_check()
