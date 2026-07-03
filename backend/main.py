import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Ensure the project root is on sys.path so `backend.*` imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.config import load_config
from backend.core.scheduler import scheduler
from backend.models.database import init_db, close_db
from backend.models.repository import purge_old_logs
from backend.services import task_manager

from backend.routers import stocks, signals, kline, search, config, scan, ws, ths, auth, users, logs, signal_config, popularity, market_report, scheduled_tasks, backtest, trade_analysis, substance, api_health as api_health_router, signal_executions, sector, emotion, market_breadth, near_buy, auction_pool, paper_trading, lark_templates, blogger, alerts, sector_rotation, quick, wencai, limit_up

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        # 按天轮转 + 仅保留近30天(超出自动删除), 配合每日 cleanup_old_logs 任务兜底清理
        TimedRotatingFileHandler("app.log", when="midnight", backupCount=30, encoding="utf-8"),
    ],
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 清理1小时前已完成/出错的回测长任务 DB 行(只删 done/error, 不动 running 的 systemd 任务)
    try:
        from backend.models.repo import backtest_jobs_db
        await backtest_jobs_db.gc_old()
    except Exception as e:  # noqa: BLE001
        logger.warning("回测 job GC 失败(忽略): %s", e)
    await purge_old_logs(3)
    logger.info("Purged operation logs older than 3 months")
    await task_manager.seed_default_tasks()
    await task_manager.load_and_register_all_tasks()
    scheduler.start()
    logger.info("Scheduler started with tasks loaded from database")
    # 启动后立即跑一次外部接口健康检查，避免前端打开时还是 unknown
    import asyncio
    from backend.services.api_health import check_all_api_health
    asyncio.create_task(check_all_api_health())
    # 全市场名称表为空时, 后台非阻塞首填(走新浪批量行情), 不卡启动; 已有则跳过留给定时任务
    from backend.services.stock_names_refresher import ensure_stock_names_seeded
    asyncio.create_task(ensure_stock_names_seeded())
    yield
    scheduler.shutdown()
    await close_db()


app = FastAPI(title="观潮", lifespan=lifespan)

# Register routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(stocks.router)
app.include_router(alerts.router)
app.include_router(signals.router)
app.include_router(kline.router)
app.include_router(search.router)
app.include_router(config.router)
app.include_router(scan.router)
app.include_router(ws.router)
app.include_router(ths.router)
app.include_router(logs.router)
app.include_router(signal_config.router)
app.include_router(popularity.router)
app.include_router(market_report.router)
app.include_router(scheduled_tasks.router)
app.include_router(backtest.router)
app.include_router(trade_analysis.router)
app.include_router(substance.router)
app.include_router(api_health_router.router)
app.include_router(signal_executions.router)
app.include_router(sector.router)
app.include_router(emotion.router)
app.include_router(market_breadth.router)
app.include_router(near_buy.router)
app.include_router(auction_pool.router)
app.include_router(paper_trading.router)
app.include_router(lark_templates.router)
app.include_router(blogger.router)
app.include_router(sector_rotation.router)
app.include_router(quick.router)
app.include_router(wencai.router)
app.include_router(limit_up.router)

# Serve Vue production build if it exists
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")

if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    _DIST_ROOT = os.path.normpath(FRONTEND_DIST)

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # v1.7.568: 防路径穿越 — 归一化后必须仍在 dist 目录内才回该文件, 否则一律回 index.html。
        #   原来直接 os.path.join+FileResponse, 构造 `../../config.json` 可读走生产凭证。
        file_path = os.path.normpath(os.path.join(_DIST_ROOT, full_path))
        in_dist = file_path == _DIST_ROOT or file_path.startswith(_DIST_ROOT + os.sep)
        if in_dist and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_DIST_ROOT, "index.html"))


if __name__ == "__main__":
    # v1.7.568: 绑 127.0.0.1 而非 0.0.0.0 — 生产由 systemd uvicorn(已 --host 127.0.0.1)+nginx 反代,
    #   此直跑分支仅本地调试用, 不应对公网暴露。
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8888, reload=False)
