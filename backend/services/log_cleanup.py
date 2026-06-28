"""每日日志清理 — 30 天保留。

每天凌晨由定时任务 cleanup_old_logs 执行:
  1) 删除 cfzy_biz_operation_logs 表里 30 天前的操作日志(控制表体积)。
  2) 删除按天轮转产生的 app.log.* 旧文件里 30 天前的(app.log 本身由 TimedRotatingFileHandler
     backupCount=30 自动保留近 30 天, 这里再兜一道, 清掉任何残留)。
"""
import glob
import logging
import os
import time

from backend.models import repository

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30


async def cleanup_old_logs():
    # 1) DB 操作日志
    try:
        n = await repository.purge_old_logs_days(RETENTION_DAYS)
        logger.info(f"[log_cleanup] 删除 {RETENTION_DAYS} 天前操作日志 {n} 条")
    except Exception as e:
        logger.warning(f"[log_cleanup] 操作日志清理失败: {e}")

    # 2) 轮转日志文件 app.log.YYYY-MM-DD
    try:
        cutoff = time.time() - RETENTION_DAYS * 86400
        removed = 0
        for f in glob.glob("app.log.*"):
            try:
                if os.path.isfile(f) and os.path.getmtime(f) < cutoff:
                    os.remove(f)
                    removed += 1
            except OSError:
                pass
        if removed:
            logger.info(f"[log_cleanup] 删除轮转日志文件 {removed} 个")
    except Exception as e:
        logger.warning(f"[log_cleanup] 日志文件清理失败: {e}")
