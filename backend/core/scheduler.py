from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 显式钉死东八区: 否则 APScheduler 回落系统本地时区, 云机若为 UTC 会把所有 cron 整体偏 8 小时
# (09:26 竞价卡跑到 17:26、盘中/盘后任务全错位), 且悄无声息。
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
