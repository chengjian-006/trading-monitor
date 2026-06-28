"""部署后核对: cfzy_biz_risk_ann_seen 表 + risk_ann_scan 定时任务是否就位。
服务器上跑: venv/bin/python backend/scripts/diag_check_risk_deploy.py
"""
import json
import pymysql

c = json.load(open("config.json"))["database"]
conn = pymysql.connect(host=c["host"], port=c.get("port", 3306),
                       user=c["user"], password=c["password"], db=c["db"])
cur = conn.cursor()
cur.execute("SHOW TABLES LIKE 'cfzy_biz_risk_ann_seen'")
print("风险公告去重表存在:", cur.fetchone() is not None)
cur.execute("SELECT job_id, enabled, schedule_type, schedule_config, handler "
            "FROM cfzy_sys_scheduled_tasks WHERE job_id='risk_ann_scan'")
print("定时任务:", cur.fetchone())
conn.close()
