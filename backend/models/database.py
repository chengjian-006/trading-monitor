import logging

import aiomysql

from backend.core.config import load_config

logger = logging.getLogger(__name__)

_pool: aiomysql.Pool | None = None

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        username      VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(200) NOT NULL,
        salt          VARCHAR(64) NOT NULL,
        role          VARCHAR(10) NOT NULL DEFAULT 'user',
        created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_stock_pool (
        code        VARCHAR(10) NOT NULL,
        user_id     INT NOT NULL DEFAULT 1,
        name        VARCHAR(50) NOT NULL DEFAULT '',
        trade_type  VARCHAR(10) NOT NULL DEFAULT 'short',
        status      VARCHAR(10) NOT NULL DEFAULT 'watch',
        added_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (code, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_signals (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        code        VARCHAR(10) NOT NULL,
        name        VARCHAR(50) NOT NULL DEFAULT '',
        signal_id   VARCHAR(40) NOT NULL,
        signal_name VARCHAR(50) NOT NULL,
        direction   VARCHAR(10) NOT NULL,
        price       DOUBLE,
        detail      TEXT,
        user_id     INT NOT NULL DEFAULT 1,
        triggered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_stock_alerts (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT NOT NULL,
        code              VARCHAR(16) NOT NULL,
        note              VARCHAR(100) NULL,
        conditions        JSON NOT NULL,
        enabled           TINYINT NOT NULL DEFAULT 1,
        status            VARCHAR(12) NOT NULL DEFAULT 'active',
        last_triggered_at DATETIME NULL,
        triggered_price   DECIMAL(10,3) NULL,
        created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_user_code (user_id, code),
        INDEX idx_scan (enabled, status)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_push_pref (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL DEFAULT 1,
        kind        VARCHAR(16) NOT NULL,
        target      VARCHAR(64) NOT NULL DEFAULT '',
        until_date  DATE NOT NULL,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        revoked_at  DATETIME NULL,
        INDEX idx_active (user_id, revoked_at, until_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_guard_throttle (
        trade_date  DATE NOT NULL,
        code        VARCHAR(16) NOT NULL,
        rule        VARCHAR(40) NOT NULL,
        cnt         INT NOT NULL DEFAULT 0,
        last_ts     DOUBLE DEFAULT NULL,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, code, rule)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_kline_cache (
        code        VARCHAR(10) NOT NULL,
        trade_date  VARCHAR(10) NOT NULL,
        open        DOUBLE,
        high        DOUBLE,
        low         DOUBLE,
        close       DOUBLE,
        volume      DOUBLE,
        PRIMARY KEY (code, trade_date)
    )
    """,
    # 全市场 5 分钟 K 线(后复权口径, baostock 源, 供分钟级模型回测).
    # code 6 位(与 kline_cache 一致便于联表); dt=K线结束时刻; 后复权固定 adjustflag=1.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_kline_5m (
        code    VARCHAR(10) NOT NULL,
        dt      DATETIME    NOT NULL,
        open    DOUBLE,
        high    DOUBLE,
        low     DOUBLE,
        close   DOUBLE,
        volume  BIGINT,
        amount  DOUBLE,
        PRIMARY KEY (code, dt),
        INDEX idx_dt (dt)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_market_breadth (
        trade_date   VARCHAR(10) NOT NULL PRIMARY KEY,
        ma20_ratio   DOUBLE,
        ma10_ratio   DOUBLE,
        ma60_ratio   DOUBLE,
        total_count  INT,
        captured_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_rally_track (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        code         VARCHAR(10) NOT NULL,
        name         VARCHAR(50) NOT NULL DEFAULT '',
        signal_id    VARCHAR(40) NOT NULL DEFAULT 'BUY_RALLY_MA20',
        signal_date  VARCHAR(10) NOT NULL,
        entry_price  DOUBLE NOT NULL,
        entry_source VARCHAR(20) NOT NULL DEFAULT '触发价',
        half_sold    TINYINT NOT NULL DEFAULT 0,
        days_held    INT NOT NULL DEFAULT 0,
        status       VARCHAR(10) NOT NULL DEFAULT 'holding',
        close_reason VARCHAR(40) DEFAULT NULL,
        user_id      INT NOT NULL DEFAULT 1,
        created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_rally_code_date (code, signal_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_backtest_runs (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        user_id        INT NOT NULL,
        model_id       VARCHAR(40) NOT NULL,
        model_name     VARCHAR(40) NOT NULL DEFAULT '',
        scope          VARCHAR(10) NOT NULL DEFAULT 'pool',
        koujing        VARCHAR(10) NOT NULL DEFAULT 'daily',
        lookback_days  INT NOT NULL DEFAULT 182,
        window_start   VARCHAR(10) NOT NULL DEFAULT '',
        window_end     VARCHAR(10) NOT NULL DEFAULT '',
        params_json    TEXT,
        overall_json   TEXT,
        monthly_json   MEDIUMTEXT,
        trades_json    MEDIUMTEXT,
        scanned        INT NOT NULL DEFAULT 0,
        trades_total   INT NOT NULL DEFAULT 0,
        trades_truncated TINYINT NOT NULL DEFAULT 0,
        created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_btrun_user (user_id, created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_operation_logs (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL,
        username    VARCHAR(50) NOT NULL,
        action      VARCHAR(50) NOT NULL,
        target      VARCHAR(100) NOT NULL DEFAULT '',
        old_value   JSON,
        new_value   JSON,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_signal_config (
        user_id     INT PRIMARY KEY,
        config      JSON NOT NULL,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_market_reports (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        time_slot   VARCHAR(10) NOT NULL,
        content     TEXT NOT NULL,
        market_data JSON,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_created (created_at DESC)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_scheduled_tasks (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        job_id          VARCHAR(50) UNIQUE NOT NULL,
        name            VARCHAR(100) NOT NULL,
        description     VARCHAR(255) NOT NULL DEFAULT '',
        schedule_type   VARCHAR(10) NOT NULL,
        schedule_config JSON NOT NULL,
        handler         VARCHAR(100) NOT NULL,
        enabled         TINYINT NOT NULL DEFAULT 1,
        last_run_at     DATETIME DEFAULT NULL,
        last_status     VARCHAR(20) DEFAULT NULL,
        consecutive_failures INT NOT NULL DEFAULT 0,
        last_error_msg  VARCHAR(500) NOT NULL DEFAULT '',
        created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_market_snapshot (
        trade_date   VARCHAR(10) NOT NULL PRIMARY KEY,
        index_trends JSON,
        market_stats JSON,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_market_overview (
        id              INT NOT NULL PRIMARY KEY,
        global_indices  JSON,
        a_indices       JSON,
        market_stats    JSON,
        snapshot_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_api_cache (
        cache_key   VARCHAR(100) NOT NULL PRIMARY KEY,
        payload     JSON NOT NULL,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_signal_executions (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT NOT NULL DEFAULT 1,
        signal_pk     INT NOT NULL,
        code          VARCHAR(10) NOT NULL,
        action        VARCHAR(20) NOT NULL,
        actual_price  DOUBLE DEFAULT NULL,
        actual_qty    INT DEFAULT NULL,
        notes         TEXT NULL,
        created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_user_signal (user_id, signal_pk),
        INDEX idx_user_code (user_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_report_feedback (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL DEFAULT 1,
        report_id   INT NOT NULL,
        vote        VARCHAR(10) NOT NULL,
        notes       TEXT NULL,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_user_report (user_id, report_id),
        INDEX idx_report (report_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_popularity_snapshot (
        trade_date   VARCHAR(10) NOT NULL PRIMARY KEY,
        data         JSON,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_popularity_daily (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        code         VARCHAR(10) NOT NULL,
        record_date  DATE NOT NULL,
        `rank`       INT DEFAULT NULL COMMENT '全市场人气排名, 越小越前',
        rank_change  INT DEFAULT NULL COMMENT '排名变化, 负=上升',
        created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_code_date (code, record_date),
        INDEX idx_date (record_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_trades (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT NOT NULL,
        trade_date    DATE NOT NULL,
        trade_time    VARCHAR(8),
        code          VARCHAR(10) NOT NULL,
        name          VARCHAR(20),
        direction     ENUM('buy','sell') NOT NULL,
        quantity      INT NOT NULL,
        price         DECIMAL(10,3) NOT NULL,
        amount        DECIMAL(12,2) NOT NULL,
        fee           DECIMAL(8,2) DEFAULT 0,
        stamp_tax     DECIMAL(8,2) DEFAULT 0,
        transfer_fee  DECIMAL(8,2) DEFAULT 0,
        net_amount    DECIMAL(12,2),
        imported_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user_code (user_id, code),
        INDEX idx_user_date (user_id, trade_date)
    )
    """,
    # 交易回合头 (v1.7.x) — 把交割单按 FIFO 聚成"开仓→清仓"一个回合, 收益分析/买点归因的基座.
    # source='real'(交割单) | 'virtual'(二期再并). MFE/MAE/holding_days/环境列二期回填, 本期留空.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_trade_rounds (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        user_id             INT NOT NULL,
        code                VARCHAR(10) NOT NULL,
        name                VARCHAR(50) NOT NULL DEFAULT '',
        source              VARCHAR(10) NOT NULL DEFAULT 'real',
        source_ref          VARCHAR(40) NOT NULL DEFAULT '',
        status              VARCHAR(10) NOT NULL DEFAULT 'open',
        open_date           DATE NOT NULL,
        open_time           VARCHAR(8) NOT NULL DEFAULT '',
        close_date          DATE DEFAULT NULL,
        close_time          VARCHAR(8) DEFAULT NULL,
        entry_price         DECIMAL(10,3) NOT NULL DEFAULT 0,
        exit_price          DECIMAL(10,3) DEFAULT NULL,
        peak_qty            INT NOT NULL DEFAULT 0,
        is_scaled_in        TINYINT NOT NULL DEFAULT 0,
        is_scaled_out       TINYINT NOT NULL DEFAULT 0,
        total_buy_amount    DECIMAL(12,2) NOT NULL DEFAULT 0,
        total_sell_amount   DECIMAL(12,2) NOT NULL DEFAULT 0,
        total_fee           DECIMAL(10,2) NOT NULL DEFAULT 0,
        realized_pnl        DECIMAL(12,2) NOT NULL DEFAULT 0,
        realized_pnl_pct    DOUBLE DEFAULT NULL,
        holding_days        INT DEFAULT NULL,
        mfe_pct             DOUBLE DEFAULT NULL,
        mfe_date            DATE DEFAULT NULL,
        mae_pct             DOUBLE DEFAULT NULL,
        mae_date            DATE DEFAULT NULL,
        max_drawdown_pct    DOUBLE DEFAULT NULL,
        entry_signal_pk     INT DEFAULT NULL,
        entry_signal_id     VARCHAR(40) DEFAULT NULL,
        entry_model_name    VARCHAR(50) DEFAULT NULL,
        entry_deviation_pct DOUBLE DEFAULT NULL,
        exit_reason         VARCHAR(40) DEFAULT NULL,
        created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE INDEX uk_round (user_id, code, source, open_date, open_time),
        INDEX idx_user_status (user_id, status),
        INDEX idx_entry_signal (entry_signal_id),
        INDEX idx_close_date (close_date)
    )
    """,
    # 交易回合腿 (v1.7.x) — 回合内每一笔买/卖动作, 真实腿 trade_id 指回交割单, 虚拟腿(二期)为 NULL.
    # round_id 外键 ON DELETE CASCADE: 重建回合时先删回合, 腿自动级联删, 保证幂等.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_round_legs (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        round_id     INT NOT NULL,
        leg_type     VARCHAR(4) NOT NULL,
        trade_date   DATE NOT NULL,
        trade_time   VARCHAR(8) NOT NULL DEFAULT '',
        price        DECIMAL(10,3) NOT NULL,
        qty          INT NOT NULL,
        amount       DECIMAL(12,2) NOT NULL DEFAULT 0,
        fee          DECIMAL(10,2) NOT NULL DEFAULT 0,
        is_virtual   TINYINT NOT NULL DEFAULT 0,
        trade_id     INT DEFAULT NULL,
        running_qty  INT NOT NULL DEFAULT 0,
        INDEX idx_round (round_id),
        UNIQUE INDEX uk_leg_trade (trade_id),
        CONSTRAINT fk_leg_round FOREIGN KEY (round_id)
            REFERENCES cfzy_biz_trade_rounds (id) ON DELETE CASCADE
    )
    """,
    # 短线情绪温度快照 (P1) — 每3分钟 + 定点落库, 前端情绪盯盘从这里读, 不参与触发判断
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_emotion_snapshot (
        id                    INT AUTO_INCREMENT PRIMARY KEY,
        trade_date            VARCHAR(10) NOT NULL,
        captured_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        source                VARCHAR(20) NOT NULL DEFAULT '',
        limit_up_count        INT DEFAULT NULL,
        limit_up_history      INT DEFAULT NULL,
        limit_down_count      INT DEFAULT NULL,
        limit_down_history    INT DEFAULT NULL,
        broken_board_count    INT DEFAULT NULL,
        up_count              INT DEFAULT NULL,
        down_count            INT DEFAULT NULL,
        seal_rate             DOUBLE DEFAULT NULL,
        highest_board         INT DEFAULT NULL,
        board_ladder          JSON,
        board_stocks          JSON,
        limit_up_codes        JSON,
        yest_limit_up_premium DOUBLE DEFAULT NULL,
        emotion_phase         VARCHAR(20) NOT NULL DEFAULT '',
        INDEX idx_date_time (trade_date, captured_at)
    )
    """,
    # 股票池"走势"列迷你分时存盘 — 盘中实时写, 非交易时段/盘后接口取空时回退到上一交易日
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_sparkline_snapshot (
        code        VARCHAR(10) NOT NULL PRIMARY KEY,
        trade_date  VARCHAR(10) NOT NULL DEFAULT '',
        data        JSON,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    # 每日分时曲线归档 (固化, 供历史回放) — 收盘冻结全天分时, 按 (code, trade_date) 联合主键逐日累积.
    # 区别于 sparkline_snapshot(只留最新一天, PK 仅 code), 这里保留每一天.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_intraday_snapshot (
        code        VARCHAR(10) NOT NULL,
        trade_date  VARCHAR(10) NOT NULL,
        data        JSON,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (code, trade_date)
    )
    """,
    # 信号前向逐日表现冻结 (v1.7.x) — 每个信号 T+1..T+30 相对触发价的当日 高/低/收盘 收益,
    # 每晚捕获写死, 不受 K线缓存退化影响; 买卖点成功率/持有曲线的长期判断依据.
    # 存 raw 收益(相对 entry=触发价), 方向(买/卖)的成败语义留分析层翻转.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_signal_perf (
        signal_pk   INT NOT NULL,
        day_offset  TINYINT NOT NULL,
        high_pct    DECIMAL(8,2),
        low_pct     DECIMAL(8,2),
        close_pct   DECIMAL(8,2),
        captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (signal_pk, day_offset),
        INDEX idx_signal (signal_pk)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_blogger_posts (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        blogger_fid   VARCHAR(40) NOT NULL,
        blogger_name  VARCHAR(50) NOT NULL DEFAULT '',
        post_id       VARCHAR(64) NOT NULL,
        posted_at     DATETIME DEFAULT NULL,
        content       TEXT,
        stock_codes   VARCHAR(255) NOT NULL DEFAULT '',
        url           VARCHAR(255) NOT NULL DEFAULT '',
        raw           MEDIUMTEXT,
        pushed        TINYINT NOT NULL DEFAULT 0,
        fetched_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE INDEX uk_blogger_post (blogger_fid, post_id),
        INDEX idx_fid_posted (blogger_fid, posted_at)
    )
    """,
    # 自选股风险公告去重台账 (v1.7.x) — 黑天鹅(灰犀牛)预警:
    # risk_announcement_scanner 每日18:00扫自选股巨潮公告, 命中监管/财务硬信号(立案/处罚/问询函/非标/换所/风险警示)
    # 即软提醒; 靠 uk_risk_ann(code, ann_id) 唯一索引同一公告只推一次.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_risk_ann_seen (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        code       VARCHAR(10) NOT NULL,
        name       VARCHAR(50) NOT NULL DEFAULT '',
        ann_id     VARCHAR(64) NOT NULL,
        title      VARCHAR(255) NOT NULL DEFAULT '',
        tags       VARCHAR(120) NOT NULL DEFAULT '',
        ann_date   VARCHAR(10) NOT NULL DEFAULT '',
        url        VARCHAR(255) NOT NULL DEFAULT '',
        pushed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE INDEX uk_risk_ann (code, ann_id),
        INDEX idx_ann_date (ann_date)
    )
    """,
    # 自选股财务红旗快照/去重台账 (v1.7.x) — 黑天鹅预警二期:
    # financial_risk_scanner 每日18:30扫自选股巨潮年报三表算6项红旗(资不抵债/连续亏损/利润现金流背离/累计亏损/高杠杆/营收断崖),
    # 每票一行(PK code)存最新快照; pushed_key=达门槛(任一强/≥2中)时的命中组合, 组合不变不重推.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_fin_risk (
        code         VARCHAR(10) NOT NULL PRIMARY KEY,
        name         VARCHAR(50) NOT NULL DEFAULT '',
        report_year  VARCHAR(4) NOT NULL DEFAULT '',
        score        INT NOT NULL DEFAULT 0,
        flags_json   TEXT,
        pushed_key   VARCHAR(255) NOT NULL DEFAULT '',
        computed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_score (score)
    )
    """,
    # 临近买点快照 (v1.7.x) — 每用户一行, items JSON 存当前接近/触发四买点的自选清单,
    # 由 refresh_near_buy_snapshot 定时整表 UPSERT, 前端监控看板 NearBuyPanel 从此读, 不再实时拉K线.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_near_buy_snapshot (
        user_id     INT NOT NULL PRIMARY KEY,
        trade_date  VARCHAR(10) NOT NULL DEFAULT '',
        computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        near_count  INT NOT NULL DEFAULT 0,
        scanned     INT NOT NULL DEFAULT 0,
        items       JSON
    )
    """,
    # 问财候选榜 (v1.7.540) — 每条选股语句一行, items JSON 存该策略当前选出的候选股清单。
    # 由 scan_wencai 定时跑 pywencai 选股后整行 UPSERT, 前端 WencaiView 读此展示+一键加自选。
    # strategy_id 全局唯一: 预置榜=breakout/pullback/theme(user_id=0 全局共享); 用户自定义榜=u{uid}_q{qid}(user_id=该用户)。
    # user_id (v1.7.546) 区分预置(0)与各用户自定义, 列表按 user_id IN (0, 当前用户) 取。last_error 非空=最近一次拉取失败, items 保留上次成功结果。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_wencai_pool (
        strategy_id   VARCHAR(40) NOT NULL PRIMARY KEY,
        user_id       INT NOT NULL DEFAULT 0,
        strategy_name VARCHAR(40) NOT NULL DEFAULT '',
        query_text    VARCHAR(255) NOT NULL DEFAULT '',
        trade_date    VARCHAR(10) NOT NULL DEFAULT '',
        stock_count   INT NOT NULL DEFAULT 0,
        items         JSON,
        last_error    VARCHAR(255) NOT NULL DEFAULT '',
        computed_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_user (user_id)
    )
    """,
    # 用户自定义问财选股语句 (v1.7.546) — 每用户可增删改自己的常驻榜语句, 由 scan_wencai 定时跑、存进 cfzy_sys_wencai_pool(strategy_id=u{uid}_q{id})。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_wencai_query (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL,
        name        VARCHAR(40) NOT NULL DEFAULT '',
        query_text  VARCHAR(255) NOT NULL,
        enabled     TINYINT NOT NULL DEFAULT 1,
        sort_order  INT NOT NULL DEFAULT 0,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user (user_id)
    )
    """,
    # 市场情绪温度表 (v1.7.x) — 按 日期×题材 记当日各涨停题材的涨停家数(热度),
    # 由 refresh_theme_heat 收盘前后定时聚合涨停池(同花顺 reason_type 首标签)写入, 前端 ThemeHeatPanel 矩阵展示主线演变.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_theme_heat (
        trade_date     VARCHAR(10) NOT NULL,
        theme          VARCHAR(40) NOT NULL,
        limit_up_count INT NOT NULL DEFAULT 0,
        sample_codes   VARCHAR(255) NOT NULL DEFAULT '',
        updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, theme),
        INDEX idx_date (trade_date)
    )
    """,
    # 板块(题材)弱转强/强转弱预判 (v1.7.x) — 每日一行覆盖。
    # rotation_data: 盘中题材轮动状态快照(scan_sector_rotation 每3分钟覆盖, 供看板);
    # predict_data: 14:30 收盘前次日预测(predict_sector_next_day 写一次)。均为 JSON 文本。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_sector_rotation (
        trade_date    VARCHAR(10) NOT NULL,
        rotation_data MEDIUMTEXT,
        predict_data  MEDIUMTEXT,
        rotation_at   DATETIME DEFAULT NULL,
        predict_at    DATETIME DEFAULT NULL,
        updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date)
    )
    """,
    # 自选股集合竞价成交额表 (v1.7.272) — 每交易日 9:26 采集所有自选股的集合竞价
    # 开盘价/昨收/高开幅度/竞价成交额/竞价成交量, 由 record_auction_pool_snapshot 写入.
    # 用途: 后续验证"竞价成交额能否提升弱转强买点胜率"的因子(历史无此数据, 从上线起向前攒).
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_auction_pool (
        code            VARCHAR(10) NOT NULL,
        trade_date      VARCHAR(10) NOT NULL,
        name            VARCHAR(50) NOT NULL DEFAULT '',
        pre_close       DOUBLE NOT NULL DEFAULT 0,
        open_price      DOUBLE NOT NULL DEFAULT 0,
        gap_pct         DOUBLE NOT NULL DEFAULT 0,
        auction_amount  DOUBLE NOT NULL DEFAULT 0,
        auction_volume  DOUBLE NOT NULL DEFAULT 0,
        captured_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (code, trade_date),
        INDEX idx_date (trade_date)
    )
    """,
    # 各买入模型 按周全市场回测结果 (v1.7.x) — 每周拉全A跑近半年回测, 一行=一次运行的一个模型.
    # 含 胜率/清仓天/资金加权占用(卖半)/单笔净收益/扣资金成本/年化资金效率/盈利因子, 供面板按周展示.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_model_backtest (
        run_date       VARCHAR(10) NOT NULL,
        signal_id      VARCHAR(40) NOT NULL,
        model_name     VARCHAR(30) NOT NULL DEFAULT '',
        window_start   VARCHAR(10) NOT NULL DEFAULT '',
        n              INT NOT NULL DEFAULT 0,
        win_rate       DOUBLE NOT NULL DEFAULT 0,
        avg_span       DOUBLE NOT NULL DEFAULT 0,
        avg_eff        DOUBLE NOT NULL DEFAULT 0,
        net_mean       DOUBLE NOT NULL DEFAULT 0,
        net_after_cost DOUBLE NOT NULL DEFAULT 0,
        annualized     DOUBLE NOT NULL DEFAULT 0,
        pf             DOUBLE NOT NULL DEFAULT 0,
        created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_date, signal_id),
        INDEX idx_run (run_date)
    )
    """,
    # 各买入模型 全市场回测 近3月/近6月 胜率+单笔均收益 (v1.7.x): 每日收盘从本地全市场库(kline_cache)重算,
    # 单行/模型(signal_id PK), 供买入提醒底部"全市场回测战绩"展示。口径同周回测(各模型真实出场+扣费0.30%)。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_model_winrate (
        signal_id    VARCHAR(40) NOT NULL,
        model_name   VARCHAR(30) NOT NULL DEFAULT '',
        win_rate_3m  DOUBLE DEFAULT NULL,
        net_3m       DOUBLE DEFAULT NULL,
        n_3m         INT NOT NULL DEFAULT 0,
        win_rate_6m  DOUBLE DEFAULT NULL,
        net_6m       DOUBLE DEFAULT NULL,
        n_6m         INT NOT NULL DEFAULT 0,
        rank_3m      INT DEFAULT NULL,
        rank_n       INT NOT NULL DEFAULT 0,
        run_date     VARCHAR(10) NOT NULL DEFAULT '',
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (signal_id)
    )
    """,
    # 持仓态 → 全市场五年 T+1/T+3 前向收益分布 (v1.7.x): 单行/态(state PK).
    # 每周由 holding_brief.refresh_holding_state_fwd 全市场扫描重算, 持仓研判晚报(20:00)
    # 读此表给每只持仓挂「同类形态历史次日/3日真实分布」当客观概率。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_holding_state_fwd (
        state        VARCHAR(20) NOT NULL,
        n            INT NOT NULL DEFAULT 0,
        up_rate_1    DOUBLE DEFAULT NULL,
        median_1     DOUBLE DEFAULT NULL,
        p10_1        DOUBLE DEFAULT NULL,
        p90_1        DOUBLE DEFAULT NULL,
        up_rate_3    DOUBLE DEFAULT NULL,
        median_3     DOUBLE DEFAULT NULL,
        p10_3        DOUBLE DEFAULT NULL,
        p90_3        DOUBLE DEFAULT NULL,
        run_date     VARCHAR(10) NOT NULL DEFAULT '',
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (state)
    )
    """,
    # 空仓预警·情绪冰点 (v1.7.406): 每交易日一行 — 涨停家数/5日均/昨停溢价/5日均 + 单层状态机状态.
    # state: 0正常 1冰点预警(停开新仓). panic_event: 当日触发恐慌底机会提示(昨停溢价5日均下穿0).
    # source: eod=16:40收盘评估(权威) / intraday=14:40盘中预升级(只升不降, 收盘复核覆盖).
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_cash_alert (
        trade_date  VARCHAR(10) NOT NULL,
        zt_count    INT DEFAULT NULL,
        zt5         DOUBLE DEFAULT NULL,
        prem        DOUBLE DEFAULT NULL,
        prem5       DOUBLE DEFAULT NULL,
        state       TINYINT NOT NULL DEFAULT 0,
        panic_event TINYINT NOT NULL DEFAULT 0,
        source      VARCHAR(10) NOT NULL DEFAULT 'eod',
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_market_risk (
        trade_date     VARCHAR(10) NOT NULL,
        advance_ratio  DOUBLE DEFAULT NULL COMMENT '涨跌比%',
        breadth_ma20   DOUBLE DEFAULT NULL COMMENT '广度MA20%',
        avg_ret_ma5    DOUBLE DEFAULT NULL COMMENT '全市场5日均收益%',
        low52_ratio    DOUBLE DEFAULT NULL COMMENT '52周新低占比%',
        zha_rate       DOUBLE DEFAULT NULL COMMENT '炸板率%',
        state          VARCHAR(10) NOT NULL DEFAULT 'GREEN' COMMENT 'GREEN/YELLOW/RED',
        source         VARCHAR(10) NOT NULL DEFAULT 'eod',
        updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date)
    )
    """,
    # 缩量后放量突破 9:45 vs 10:00 闸门 A/B 向前验 (v1.7.x 临时实验) — 同股同日每档记首次命中.
    # arm: '0945'=9:45起评估 / '1000'=10:00起评估. 对比早15分钟的入场价/封板/假信号差异.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_gate_ab (
        code          VARCHAR(10) NOT NULL,
        trade_date    VARCHAR(10) NOT NULL,
        arm           VARCHAR(6)  NOT NULL,
        name          VARCHAR(50) NOT NULL DEFAULT '',
        trigger_time  DATETIME    NOT NULL,
        trigger_price DOUBLE      NOT NULL DEFAULT 0,
        trigger_level DOUBLE      NOT NULL DEFAULT 0,
        gap_pct       DOUBLE      NOT NULL DEFAULT 0,
        amount_est_yi DOUBLE      NOT NULL DEFAULT 0,
        sealed        TINYINT     NOT NULL DEFAULT 0,
        created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (code, trade_date, arm),
        INDEX idx_date (trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_account (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL DEFAULT 1,
        account_key VARCHAR(32) NOT NULL DEFAULT 'default',
        name VARCHAR(64) NOT NULL DEFAULT '模拟账户',
        initial_capital DECIMAL(14,2) NOT NULL DEFAULT 1000000.00,
        cash DECIMAL(14,2) NOT NULL DEFAULT 1000000.00,
        max_positions INT NOT NULL DEFAULT 10,
        buy_position_pct DECIMAL(6,4) NOT NULL DEFAULT 0.2000,
        unlimited_bullets TINYINT NOT NULL DEFAULT 0,
        commission_rate DECIMAL(7,6) NOT NULL DEFAULT 0.000250,
        min_commission DECIMAL(6,2) NOT NULL DEFAULT 5.00,
        stamp_rate DECIMAL(7,6) NOT NULL DEFAULT 0.001000,
        transfer_rate DECIMAL(7,6) NOT NULL DEFAULT 0.000010,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reset_at DATETIME NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_paper_account (user_id, account_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_position (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        code VARCHAR(8) NOT NULL,
        name VARCHAR(32) NOT NULL DEFAULT '',
        qty INT NOT NULL,
        cost_amount DECIMAL(14,2) NOT NULL,
        open_date DATE NOT NULL,
        open_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        entry_signal_id VARCHAR(48) NOT NULL DEFAULT '',
        entry_model_name VARCHAR(48) NOT NULL DEFAULT '',
        UNIQUE KEY uk_paper_pos (account_id, code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_trade (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        code VARCHAR(8) NOT NULL,
        name VARCHAR(32) NOT NULL DEFAULT '',
        side ENUM('buy','sell') NOT NULL,
        qty INT NOT NULL,
        price DECIMAL(10,3) NOT NULL,
        amount DECIMAL(14,2) NOT NULL,
        fee DECIMAL(10,2) NOT NULL,
        cash_after DECIMAL(14,2) NOT NULL,
        signal_id VARCHAR(48) NOT NULL DEFAULT '',
        signal_name VARCHAR(64) NOT NULL DEFAULT '',
        signal_direction VARCHAR(12) NOT NULL DEFAULT '',
        realized_pnl DECIMAL(14,2) NULL,
        realized_pnl_pct DECIMAL(8,3) NULL,
        note VARCHAR(64) NOT NULL DEFAULT '',
        trade_date DATE NOT NULL,
        trade_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_paper_trade_acct_date (account_id, trade_date),
        KEY idx_paper_trade_sig (account_id, code, signal_id, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_equity (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        snap_date DATE NOT NULL,
        cash DECIMAL(14,2) NOT NULL,
        holdings_mv DECIMAL(14,2) NOT NULL,
        total_equity DECIMAL(14,2) NOT NULL,
        total_return_pct DECIMAL(8,3) NOT NULL,
        position_count INT NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_paper_equity (account_id, snap_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # v1.7.386: 指数字典 — 自选股可关联"对标指数"(任意指数代码, 带市场前缀如 sh000001/sz399006),
    # 字典全局共享可维护, 个股映射存 cfzy_biz_stock_pool.ref_index_code/ref_index_name
    # v1.7.500: 「对标指数」功能已下线, schema 保留休眠(不写 DROP), 本表与下方 ref_index_* 列均不再被代码读写
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_index_dict (
        code        VARCHAR(16) NOT NULL PRIMARY KEY,
        name        VARCHAR(50) NOT NULL DEFAULT '',
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 全市场股票代码→名称字典 (v1.7.x) — 模型回测逐笔明细给全市场票补名(自选池只覆盖少量).
    # 由 refresh_stock_names 定时(每日07:30交易日前)分批走新浪/腾讯批量行情拉名 upsert; 启动时空表非阻塞首填.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_stock_names (
        code VARCHAR(10) NOT NULL PRIMARY KEY,
        name VARCHAR(32) NOT NULL DEFAULT '',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    # 模型回测长任务(全市场/5分钟)的 DB 态 job (v1.7.x) — 方案C: 每个长 job 用 systemd-run 拉独立临时
    # systemd 单元跑(部署/重启主服务杀不死), 进度/结果落此表供前端轮询。短任务(自选+日线)仍走内存态不入此表。
    # status: running/done/error。runner: systemd=独立单元 / inproc=无 systemd 时内存态回退占位。
    # 完成1小时后由启动 GC 清理(只删 done/error, 不动 running)。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_backtest_jobs (
        job_id VARCHAR(16) PRIMARY KEY,
        user_id INT NOT NULL,
        model_id VARCHAR(40) NOT NULL,
        scope VARCHAR(10) NOT NULL,
        koujing VARCHAR(10) NOT NULL,
        lookback_days INT NOT NULL,
        window_start VARCHAR(10) NOT NULL,
        window_end VARCHAR(10) NOT NULL,
        params_json MEDIUMTEXT,
        codes_json MEDIUMTEXT,
        status VARCHAR(10) NOT NULL DEFAULT 'running',
        progress_json TEXT,
        result_json MEDIUMTEXT,
        error VARCHAR(500) NOT NULL DEFAULT '',
        runner VARCHAR(10) NOT NULL DEFAULT 'systemd',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
]


_RENAME_MAP = {
    "cfzy_users": "cfzy_sys_users",
    "cfzy_stock_pool": "cfzy_biz_stock_pool",
    "cfzy_signals": "cfzy_biz_signals",
    "cfzy_kline_cache": "cfzy_sys_kline_cache",
    "cfzy_operation_logs": "cfzy_biz_operation_logs",
    "cfzy_signal_config": "cfzy_biz_signal_config",
    "cfzy_market_reports": "cfzy_sys_market_reports",
    "cfzy_scheduled_tasks": "cfzy_sys_scheduled_tasks",
    "cfzy_market_snapshot": "cfzy_sys_market_snapshot",
    "cfzy_popularity_snapshot": "cfzy_sys_popularity_snapshot",
    "cfzy_popularity_daily": "cfzy_biz_popularity_daily",
}


MIGRATION_STATEMENTS = [
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN user_id INT NOT NULL DEFAULT 1",
    "ALTER TABLE cfzy_biz_stock_pool DROP PRIMARY KEY, ADD PRIMARY KEY (code, user_id)",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN user_id INT NOT NULL DEFAULT 1",
    "ALTER TABLE cfzy_sys_users ADD COLUMN ths_path VARCHAR(500) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN focused TINYINT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_sys_users ADD COLUMN token_version INT NOT NULL DEFAULT 1",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN price DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN pct_change DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN amount DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN speed DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN quote_updated_at DATETIME DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN industry VARCHAR(50) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN volume_ratio DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN free_cap DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN turnover DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN popularity_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN sort_order INT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_sys_users ADD COLUMN wecom_webhook VARCHAR(500) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_sys_users ADD COLUMN push_enabled TINYINT NOT NULL DEFAULT 1",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN indicators JSON DEFAULT NULL",
    "ALTER TABLE cfzy_sys_users ADD COLUMN mobile VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN sector_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN hold_source VARCHAR(10) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_trades ADD UNIQUE INDEX uk_trade (user_id, trade_date, trade_time, code, direction, quantity, price)",
    "ALTER TABLE cfzy_biz_signals ADD INDEX idx_user_triggered (user_id, triggered_at)",
    "ALTER TABLE cfzy_biz_signals ADD INDEX idx_dedup (code, signal_id, user_id, triggered_at)",
    "ALTER TABLE cfzy_biz_operation_logs ADD INDEX idx_user_created (user_id, created_at)",
    "ALTER TABLE cfzy_biz_operation_logs ADD INDEX idx_action (action)",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN strategy TEXT NULL",
    # v1.7.29 AI 真受益核查
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN substance_score TINYINT DEFAULT 0",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN substance_note TEXT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN substance_analysis MEDIUMTEXT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN substance_updated_at DATETIME NULL",
    # v1.7.55: signal_id 列从 VARCHAR(20) 加宽到 VARCHAR(40)，
    # 修复 SECTOR_CAPITAL_INFLOW (21字符) 因 Data too long 写库失败导致资金回流板块预警从不推送
    "ALTER TABLE cfzy_biz_signals MODIFY COLUMN signal_id VARCHAR(40) NOT NULL",
    # v1.7.x: 调度任务失败告警 — 连续失败计数 + 最近一次错误信息
    "ALTER TABLE cfzy_sys_scheduled_tasks ADD COLUMN consecutive_failures INT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_sys_scheduled_tasks ADD COLUMN last_error_msg VARCHAR(500) NOT NULL DEFAULT ''",
    # v1.7.x: 信号闭环 — 触发后回填 1/3/5 日实际收益(收盘价)与 outcome 综合判定
    #   outcome: 'success' | 'fail' | 'neutral' | NULL(未评估)
    #   p1/p3/p5_pct: 第 N 个交易日收盘价相对触发价 (sell/reduce 翻转, +=避损)
    "ALTER TABLE cfzy_biz_signals ADD COLUMN outcome VARCHAR(10) DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN outcome_p1_pct DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN outcome_p3_pct DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN outcome_p5_pct DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN outcome_evaluated_at DATETIME DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD INDEX idx_outcome (outcome, signal_id)",
    # v1.7.x: 信号防重 — 加一个 generated column 当作"按天"维度, 上 UNIQUE
    # 配合 save_signal 的 INSERT IGNORE, 即使并发场景下也不会同日重写同 code+signal_id
    # 如果生产已有同日重复数据, 这两条 ALTER 会失败被吞掉; INSERT IGNORE 不需要索引也能工作
    "ALTER TABLE cfzy_biz_signals ADD COLUMN trigger_date DATE GENERATED ALWAYS AS (DATE(triggered_at)) STORED",
    "ALTER TABLE cfzy_biz_signals ADD UNIQUE INDEX uk_signal_day (code, signal_id, user_id, trigger_date)",
    # v1.7.x: 信号分组 — direction 只有 buy/sell/reduce 三类太粗, 这里给一个 signal_group 维度
    # 取值: entry(左/右侧买点) | exit(均线破位/止盈类卖) | risk(浮亏/止损) | regime(大盘急跌) | sector(板块预警) | quality(强势评分等)
    # 后续聚合统计/UI 分组按 group 切, 比 direction 更细
    "ALTER TABLE cfzy_biz_signals ADD COLUMN signal_group VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_signals ADD INDEX idx_group (signal_group, triggered_at)",
    # v1.7.x: 股票池标签 — 概念题材(逗号分隔, 最多4个) + 连板数(连续涨停交易日数)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN concepts VARCHAR(255) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN limit_up_days INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN tags_updated_at DATETIME DEFAULT NULL",
    # v1.7.x: 自选股逻辑删除 — deleted_at 非空即"已删除"(出池不可见/停扫描),
    # 但历史信号仍在 cfzy_biz_signals、回测 universe 仍纳入 (include_deleted=True)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN deleted_at DATETIME DEFAULT NULL",
    # v1.7.x: 情绪快照增补"曾涨停/曾跌停"(同花顺 history_num) — 收盘封板 vs 当日曾触及涨停
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN limit_up_history INT DEFAULT NULL",
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN limit_down_history INT DEFAULT NULL",
    # v1.7.x: 情绪快照存上涨/下跌家数 → 情绪面板画当日涨停/跌停/上涨/下跌四线趋势
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN up_count INT DEFAULT NULL",
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN down_count INT DEFAULT NULL",
    # v1.7.x: 飞书(Lark)推送 — 与企微平行的第二通道, 独立 webhook + 开关
    "ALTER TABLE cfzy_sys_users ADD COLUMN lark_webhook VARCHAR(500) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_sys_users ADD COLUMN lark_enabled TINYINT NOT NULL DEFAULT 0",
    # v1.7.x: 回踩提醒跟踪 — 标记模型(回踩20MA缩量后突破昨高/回踩10MA缩量后突破昨高), 决定剩半跟踪线(MA20×0.98 / MA10×0.97)
    "ALTER TABLE cfzy_biz_rally_track ADD COLUMN signal_id VARCHAR(40) NOT NULL DEFAULT 'BUY_RALLY_MA20'",
    # v1.7.x: 情绪快照存连板个股明细(≥2板) → 连板梯队详单面板列出具体个股
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN board_stocks JSON",
    # 持仓在最热题材板块内的强弱名次(quote_refresher 每3s用实时涨幅插值写, sector_strength 60s刷名单)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_name VARCHAR(50) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_total INT DEFAULT NULL",
    # 模型近3月胜率排名(供买入提醒标"全模型第X名")
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN rank_3m INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN rank_n INT NOT NULL DEFAULT 0",
    # v1.7.x: 模拟账户成交流水加"成交状态" — 触发买点但买不进(资金不足/无板块交易权限/已持有/仓位满)
    # 也留一笔 status='failed' 的记录 + 失败原因, 让流水反映"想买没买成"。旧行默认 success。
    "ALTER TABLE cfzy_biz_paper_trade ADD COLUMN status ENUM('success','failed') NOT NULL DEFAULT 'success'",
    "ALTER TABLE cfzy_biz_paper_trade ADD COLUMN fail_reason VARCHAR(64) NOT NULL DEFAULT ''",
    # v1.7.x: trigger_date 单列索引 — "当日信号"类查询(rally_reminder每60s/前端轮询/报告)原来
    # 用 DATE(triggered_at)=CURDATE() 函数包列全表扫; uk_signal_day 前导列是 code 帮不上,
    # 加这个索引后配合查询改写 trigger_date=CURDATE() 即可索引命中
    "ALTER TABLE cfzy_biz_signals ADD INDEX idx_trigger_date (trigger_date)",
    # v1.7.386: 自选股关联"对标指数"(带市场前缀的指数代码 + 名称快照, 来自 cfzy_sys_index_dict)
    # v1.7.500: 「对标指数」功能已下线, 以下两列保留休眠(不写 DROP), 代码已不再读写, 返回空串无害
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN ref_index_code VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN ref_index_name VARCHAR(50) NOT NULL DEFAULT ''",
    # v1.7.424: 股票池均线位置筛选(站上20线 / 贴近10线·60线 ±2%) — 补 ma10/ma60(ma20 早已有)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN ma10 DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN ma60 DOUBLE DEFAULT NULL",
    # v1.7.387: 信号EOD自动复核 — 收盘后用真实日线核当日信号, 存疑标记不自动删
    #   eod_audit: 'ok' | 'suspect' | 'unverified' | NULL(未复核/不适用)
    "ALTER TABLE cfzy_biz_signals ADD COLUMN eod_audit VARCHAR(16) DEFAULT NULL",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN eod_audit_note VARCHAR(255) DEFAULT NULL",
    # v1.7.x: 自选股每日人气排名存档 — 每晚22:00拉全量自选人气排名写此表，供回测/区间复盘用
    #   表已在 CREATE TABLE 段落定义, 此处仅作 ALTER 兼容 (新部署直接走 CREATE IF NOT EXISTS)
    # v1.7.494: 模拟账户多账户化 — 同一 user 下按 account_key 区分多个独立账户(默认账户 + 无限子弹账户)。
    #   account_key: 账户标识('default'=原模拟账户, 'unlimited'=无限子弹)
    #   buy_position_pct: 每笔买入目标仓位比例(default 0.20, unlimited 0.05)
    #   unlimited_bullets: 1=无限子弹(现金可透支/不限持仓数/同股可加仓), 0=普通(原逻辑)
    #   唯一键从 (user_id) 改为 (user_id, account_key); 旧 default 行自动落 account_key='default'
    "ALTER TABLE cfzy_biz_paper_account ADD COLUMN account_key VARCHAR(32) NOT NULL DEFAULT 'default'",
    "ALTER TABLE cfzy_biz_paper_account ADD COLUMN buy_position_pct DECIMAL(6,4) NOT NULL DEFAULT 0.2000",
    "ALTER TABLE cfzy_biz_paper_account ADD COLUMN unlimited_bullets TINYINT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_biz_paper_account DROP INDEX uk_paper_account_user",
    "ALTER TABLE cfzy_biz_paper_account ADD UNIQUE KEY uk_paper_account (user_id, account_key)",
    # 性能: cfzy_sys_kline_cache 主键 (code, trade_date), 但风控/胜率/持仓回填等任务按
    #   WHERE trade_date >= %s (不带 code) 查 → 主键用不上, 601万行全表扫描~2.6s且占着连接。
    #   补 trade_date 单列索引, 窄日期范围查询从全表扫降到索引范围扫。
    "ALTER TABLE cfzy_sys_kline_cache ADD INDEX idx_kc_trade_date (trade_date)",
    # v1.7.546: 问财候选榜支持用户自定义语句 — 给已部署的 cfzy_sys_wencai_pool 补 user_id 列
    #   (预置榜=0 全局共享, 用户自定义榜=该用户id; strategy_id 全局唯一靠 u{uid}_q{qid} 命名)
    "ALTER TABLE cfzy_sys_wencai_pool ADD COLUMN user_id INT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_sys_wencai_pool ADD INDEX idx_user (user_id)",
]


async def _rename_tables(conn):
    """Migrate old table names to new biz/sys prefixed names."""
    async with conn.cursor() as cur:
        await cur.execute("SHOW TABLES")
        existing = {row[0] for row in await cur.fetchall()}
        for old_name, new_name in _RENAME_MAP.items():
            if old_name in existing and new_name not in existing:
                await cur.execute(f"RENAME TABLE `{old_name}` TO `{new_name}`")
                logger.info(f"Renamed table {old_name} -> {new_name}")


async def _run_migrations(conn):
    import json as _json
    # v1.7.x: MySQL 错误码区分 —— "幂等已存在"类静默吞掉, 真正的错误必须记 warning
    # 常见幂等错误码:
    #   1060 Duplicate column name
    #   1061 Duplicate key name
    #   1091 Can't DROP; check that column/key exists
    #   1068 Multiple primary key defined
    #   1146 Table doesn't exist (旧表已经被 _rename_tables 处理)
    IDEMPOTENT_ERRNOS = {1060, 1061, 1068, 1091, 1146}
    async with conn.cursor() as cur:
        for stmt in MIGRATION_STATEMENTS:
            try:
                await cur.execute(stmt)
            except aiomysql.MySQLError as e:
                errno = e.args[0] if e.args else 0
                if errno in IDEMPOTENT_ERRNOS:
                    continue  # 已经存在/已经不存在 - 正常 idempotent
                # 其他错误必须可见, 不能再沉默吞掉
                logger.warning(
                    f"[migration] 非幂等错误被跳过, 后续 query 可能崩溃: "
                    f"errno={errno} stmt={stmt[:100]!r} err={e}"
                )
            except Exception as e:
                logger.warning(f"[migration] 未知异常被跳过: stmt={stmt[:100]!r} err={e}")
        migration_tasks = [
            # v1.7.557: 推送降噪·批次E — 系统故障告警(数据源交叉校验/博主拉取中断等)不再实时逐类推,
            # 当日累积盘后 21:00 合成一条「系统健康·盘后汇总」, 无异常则不推。
            ("system_health_digest", "系统健康·盘后汇总·21:00",
             "每日 21:00 把当日累积的系统故障(数据源交叉校验偏差/博主拉取中断等)合成一条汇总推送, 无异常不推; 更紧急的行情源健康仍即时告警",
             "cron", _json.dumps({"hour": 21, "minute": 0}), "run_system_health_digest"),
            # v1.7.554: 推送降噪·批次B③ — 尾盘三卡(真假强势14:30/次日板块14:30/弱势极限14:45)合并成14:40一张
            ("tail_decision_1440", "尾盘决策·14:40(强势评分+次日板块+弱势极限合并)",
             "14:40 一张合并卡: 真假强势评分 + 次日板块预测 + 弱势极限尾盘候选; 原三条 14:30~14:45 独立推送合并",
             "cron", _json.dumps({"hour": 14, "minute": 40}), "run_tail_decision_1440"),
            ("auction_sector_strength_0926", "竞价分析·09:26",
             "9:25集合竞价撮合后推板块强弱硬数据卡: 行业最强/最弱+概念top10(涨家比/领涨股标红自选)+昨日热点承接度+持仓板块名次; 与AI开盘共性卡互补不走AI",
             "cron", _json.dumps({"hour": 9, "minute": 26}), "run_auction_sector_strength"),
            ("post_close_summary_1505", "盘后汇总·15:05",
             "收盘后5分钟汇总所有 alert_timing=post_close 的信号命中，企微一条消息推送",
             "cron", _json.dumps({"hour": 15, "minute": 5}), "run_post_close_summary"),
            ("freeze_intraday_1510", "分时曲线归档·15:10",
             "收盘后冻结股票池当日分时曲线到 cfzy_sys_intraday_snapshot，供分时图历史回放",
             "cron", _json.dumps({"hour": 15, "minute": 10}), "freeze_intraday_snapshots"),
            ("review_summary_2330", "收盘复盘摘要·19:00",
             "晚7点推送复盘摘要(今日信号+买卖点胜率对比+最好/警惕信号)到企微/飞书。胜率由16:00回填先行, 此处胜率为当日最新",
             "cron", _json.dumps({"hour": 19, "minute": 0}), "run_review_summary"),
            ("prefetch_intraday_sparklines", "分时走势预热",
             "每25秒预拉股票池分时数据，让缓存常热，前端打开页面秒返",
             "interval", _json.dumps({"seconds": 25}), "prefetch_intraday_sparklines"),
            ("market_data_refresh", "市场数据刷新", "每60秒刷新大盘指数走势和涨跌停统计",
             "interval", _json.dumps({"seconds": 60}), "refresh_market_data"),
            ("popularity_refresh", "人气排行刷新", "每60秒刷新人气排行榜数据",
             "interval", _json.dumps({"seconds": 60}), "refresh_popularity"),
            ("detect_plunge", "大盘跳水监控", "每30秒检测大盘跳水信号（指数急跌/涨跌恶化/跌停加速）",
             "interval", _json.dumps({"seconds": 30}), "detect_plunge"),
            ("sector_leader", "板块龙头检测", "每45秒检测自选股在板块中的排名",
             "interval", _json.dumps({"seconds": 45}), "refresh_sector_leaders"),
            ("weak_extreme_1130", "S0 弱势极限·上午快照 11:30",
             "扫描股票池，汇总命中 S0 弱势极限的票，企业微信推送一条汇总消息",
             "cron", _json.dumps({"hour": 11, "minute": 30}), "scan_weak_extreme_snapshot"),
            ("weak_extreme_1445", "S0 弱势极限·尾盘快照 14:45",
             "尾盘14:45扫股票池汇总命中弱势极限的票, 单独推一条企微供盘中决策(盘未收用分时外推量, 与11:30同口径); 15:05收盘汇总仍带真实全天量复核",
             "cron", _json.dumps({"hour": 14, "minute": 45}), "scan_weak_extreme_snapshot"),
            ("api_health_check", "外部接口健康检查",
             "每 5 分钟探活东方财富/新浪/akshare 数据源各子接口，结果展示在框架右上角",
             "interval", _json.dumps({"seconds": 300}), "check_all_api_health"),
            # v1.7.97: 老 DB 补登 — 已有用户重启后自动获得这个任务
            ("market_overview_refresh", "市场概览快照",
             "每30秒拉全球+A股指数+涨跌停, 写入 cfzy_sys_market_overview",
             "interval", _json.dumps({"seconds": 30}), "refresh_market_overview"),
            # v1.7.x: 信号闭环 — 每日 16:00 回填 ≥5 个交易日前的买/卖点信号实际收益
            # (只用≥7天前老信号的历史收盘价, 不依赖当日盘后数据, 故盘后即可跑, 在复盘前完成)
            ("backfill_signal_outcomes", "信号闭环·收益回填",
             "每日 16:00 给触发后≥5 个交易日的信号回填 1/3/5 日实际收益(收盘价基)与 success/fail/neutral 判定, 喂给配置页胜率衰减/参数寻参",
             "cron", _json.dumps({"hour": 16, "minute": 0}), "backfill_signal_outcomes"),
            # v1.7.x: 信号前向表现冻结 — 每日 23:10 把每个信号 T+1..T+30 逐日 高/低/收盘 收益写死进 DB,
            # 不受 K线缓存退化影响; 顺带刷新有信号的个股 K 线保鲜。买卖点成功率/持有曲线的长期判断依据。
            ("snapshot_signal_perf", "信号前向表现·逐日冻结",
             "每日 23:10 把捕获窗口内每个信号触发后 T+1..T+30 的当日最高/最低/收盘收益(相对触发价)写死进 cfzy_biz_signal_perf, 永不丢失; 顺带刷新有信号个股 K 线",
             "cron", _json.dumps({"hour": 23, "minute": 10}), "snapshot_signal_perf"),
            # v1.7.499: 人气榜 AI 解读定时全量重刷 — 9:00~22:00 每小时一次(替代原 16:00/19:00 两点)
            ("popularity_ai_hourly", "人气榜 AI 解读·盘中+收盘两点(11:30/15:30)",
             "9:00~22:00 每小时给 TOP20 全量重新生成 AI 解读, 含盘前/盘中/盘后持续发酵的公告/新闻; 标注刷新时间显示在前端",
             "cron", _json.dumps({"hour": "11,15", "minute": 30}), "refresh_popularity_full_ai"),
            # v1.7.x: 自选股每日人气排名存档 — 22:00拉全量自选人气排名写 cfzy_biz_popularity_daily, 供回测/复盘
            ("popularity_daily_2200", "人气排名·每日存档 22:00",
             "每晚22:00拉自选池全量个股人气排名写入 cfzy_biz_popularity_daily(code+日期+排名), 累积历史供回测/区间复盘/情绪分析",
             "cron", _json.dumps({"hour": 22, "minute": 0}), "record_daily_popularity"),
            # v1.7.x: 全市场广度 — 盘后15:35抓全A算站上MA20/10/60比例, 写 cfzy_sys_market_breadth(大盘温度计)
            ("market_breadth_1535", "全市场广度·盘后15:35",
             "盘后抓全市场(剔北交所/ST/退市)日线, 算站上MA20/MA10/MA60个股比例, 写入 cfzy_sys_market_breadth, 供大盘环境温度计参考",
             "cron", _json.dumps({"hour": 15, "minute": 35}), "refresh_market_breadth"),
            # v1.7.x: 交易回合重建 — 收盘后按 FIFO 把交割单聚成回合(头+腿)并归因买点, 供收益分析
            ("rebuild_trade_rounds", "交易回合重建·15:20",
             "收盘后把各用户交割单按 FIFO 聚成开→平交易回合(cfzy_biz_trade_rounds/round_legs)并就近归因买点信号",
             "cron", _json.dumps({"hour": 15, "minute": 20}), "rebuild_trade_rounds"),
            # v1.7.x: 回踩20MA缩量后突破昨高 买卖提醒 — 盘中60s建仓+买入提醒+7%减半; 尾盘14:40判-6%/破MA20/T+10时停
            ("rally_reminder_tick", "回踩买点提醒·盘中",
             "盘中每60秒: 回踩10MA缩量后突破昨高/回踩20MA缩量后突破昨高触发即建跟踪持仓并推买入提醒; 持仓(T+1起)盘中触及+7%推止盈减半",
             "interval", _json.dumps({"seconds": 60}), "rally_reminder_tick"),
            ("rally_reminder_eod", "回踩买点提醒·尾盘14:40",
             "尾盘14:40按收盘价判: 未减半-6%止损/已减半剩半破MA20×0.98清/满10交易日时停; 顺带交割单对账(T+1起)",
             "cron", _json.dumps({"hour": 14, "minute": 40}), "rally_reminder_eod"),
            # v1.7.x: 持仓守护提醒 — 盘中60s, 真实持仓 接近前高(下方≤2%)/盈利保护(峰值≥+10%回吐≤+2%); 只提醒不落库
            ("holding_guard_tick", "持仓守护提醒·盘中",
             "盘中每60秒: 真实持仓现价逼近近60日波段前高(≤2%)推接近前高; 曾赚过≥+10%又回吐到≤+2%推盈利保护(锁利提醒)",
             "interval", _json.dumps({"seconds": 60}), "holding_guard_tick"),
            # 日志保留 30 天 — 每日凌晨3:30 删除 30 天前的操作日志(DB)与轮转日志文件
            ("cleanup_old_logs", "日志清理·每日03:30",
             "每日凌晨删除30天前的操作日志(cfzy_biz_operation_logs)与轮转日志文件 app.log.*, 控制表与磁盘体积",
             "cron", _json.dumps({"hour": 3, "minute": 30}), "cleanup_old_logs"),
            ("self_heal_quotes", "行情陈旧自愈",
             "盘中每90秒扫 quote_updated_at 陈旧(>150s)的自选票, 单独补刷核心行情, 兜底 quote_refresher 偶发漏刷个别票",
             "interval", _json.dumps({"seconds": 90}), "self_heal_stale_quotes"),
            ("data_sanity_check", "行情数据自检告警",
             "盘中每5分钟校验行情健康(陈旧>6min或无价的票数), 超阈值推企微告警, 捕捉不报错但数据明显不对的情况",
             "interval", _json.dumps({"seconds": 300}), "check_data_sanity"),
            # 大盘退潮风控(三模型回测0604唯一验证有效的市场级风控): 涨停家数较前日骤降≥40%→减仓提示
            ("detect_market_ebb", "大盘退潮·减仓提示",
             "盘中(≥11:00)每5分钟判全市场涨停家数较前日是否骤降≥40%, 是则推'退潮减仓'提示(对持仓强势股), 每日最多一次",
             "interval", _json.dumps({"seconds": 300}), "detect_market_ebb"),
            # v1.7.x: 强势退潮 — 昨日涨停股今日平均溢价转负(打板亏钱), 盘中(≥11:00)推一次
            ("detect_strength_ebb", "强势退潮·赚钱效应消失",
             "盘中(≥11:00)每5分钟判昨日涨停股今日平均溢价是否≤阈值(打板/强势资金转亏), 是则推强势退潮提示, 每日一次",
             "interval", _json.dumps({"seconds": 300}), "detect_strength_ebb"),
        ]
        for task in migration_tasks:
            try:
                await cur.execute(
                    "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                    "(job_id, name, description, schedule_type, schedule_config, handler) "
                    "VALUES (%s, %s, %s, %s, %s, %s)", task,
                )
            except Exception:
                pass
        # v1.7.13: 板块最强信号已移除，停掉定时任务
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s",
                ("sector_leader",),
            )
        except Exception:
            pass
        # v1.7.x: 广度刷新已合并全市场日线落库, 独立的 17:00 追加任务(fullmarket_kline_append)
        # 已退役, handler 已删; 清掉存量库的禁用行
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
                ("fullmarket_kline_append",),
            )
        except Exception:
            pass
        # v1.7.547: 问财候选榜改手工触发 — 同花顺问财逆向接口易被反爬, 定时空转徒增风控,
        # 停掉 wencai_scan 定时任务(改 POST /api/wencai/scan 用户按需触发); scan_wencai handler 保留备用
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s",
                ("wencai_scan",),
            )
        except Exception:
            pass
        # v1.7.552: 推送降噪·批次A — 提醒太杂太乱, 下线/降频三处
        #   1) auction_strength_selfcheck(09:31 竞价首日自检): 本就是临时验证任务(确认后可删), 已验完下线
        #   2) report_1400(14:00 午后分析): 与 11:30 午盘 / 15:00 收盘信息重复度高, 下线减噪
        #   3) popularity_ai_hourly(人气榜AI 9-22 每小时=14条/日过密): 改 11:30+15:30 两点(盘中+收盘各一)
        for _jid in ("auction_strength_selfcheck", "report_1400"):
            try:
                await cur.execute(
                    "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s", (_jid,))
            except Exception:
                pass
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET schedule_config = %s, name = %s, description = %s "
                "WHERE job_id = %s",
                (_json.dumps({"hour": "11,15", "minute": 30}),
                 "人气榜 AI 解读·盘中+收盘两点(11:30/15:30)",
                 "每日 11:30(盘中)与 15:30(收盘后)两点给 TOP20 全量重新生成 AI 解读, 含盘前/盘中/盘后持续发酵的公告/新闻; 标注刷新时间显示在前端",
                 "popularity_ai_hourly"))
        except Exception:
            pass
        # v1.7.553: 推送降噪·批次B-① — 09:26 两张竞价卡合并成一张
        #   auction_summary_0926 改跑合并 handler run_auction_0926(AI开盘共性+板块强弱一张卡),
        #   auction_sector_strength_0926 下线(其计算并入合并卡)。
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET handler = %s, name = %s, description = %s "
                "WHERE job_id = %s",
                ("run_auction_0926", "竞价播报·09:26(开盘共性+板块强弱合并)",
                 "9:26 一张合并卡: AI 开盘共性(指数+高开低开榜+强势密度) + 竞价板块强弱(行业/概念最强+持仓关联+昨日承接); 原两条推送合并",
                 "auction_summary_0926"))
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s",
                ("auction_sector_strength_0926",))
        except Exception:
            pass
        # v1.7.554: 推送降噪·批次B③ — 尾盘三卡合并成 14:40 tail_decision_1440(上方 migration_tasks 已 seed),
        # 下线原三条独立任务(计算并入合并卡)。
        for _jid in ("strength_quality_1430", "sector_next_day_predict", "weak_extreme_1445"):
            try:
                await cur.execute(
                    "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s", (_jid,))
            except Exception:
                pass

        # v1.7.345: 弱势极限下午快照 15:00→14:45(盘中可决策, 不再只并入15:05收盘汇总)
        # 存量库删旧 15:00 行(新 weak_extreme_1445 行由上方 seed INSERT IGNORE 补)
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
                ("weak_extreme_1500",),
            )
        except Exception:
            pass

        # v1.7.17: 资金回流板块预警任务 (30秒间隔, 盘中扫所有板块)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("capital_inflow", "资金回流·板块预警",
                 "每30秒扫所有板块，找出符合板块涨≥1%+龙头涨停+市场前5均涨≥5%的板块，推送企微",
                 "interval", _json.dumps({"seconds": 30}), "scan_capital_inflow"),
            )
        except Exception:
            pass

        # v1.7.28: 真假强势评分快照 (cron 14:30, 尾盘前一刻评分稳定)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("strength_quality_1430", "真假强势评分·14:30 快照",
                 "扫股票池跑9维度评分,推送真强势+观望候选清单到企微",
                 "cron", _json.dumps({"hour": 14, "minute": 30}), "scan_strength_quality_snapshot"),
            )
        except Exception:
            pass

        # v1.7.94: 09:45 资金进攻方向 AI 分析
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("attack_direction_0945", "资金进攻方向·09:45 AI 分析",
                 "拉取盘中成交额前20 + 涨幅前50, 用 AI 归纳板块/题材/市值共性, 企微推送",
                 "cron", _json.dumps({"hour": 9, "minute": 45}), "run_attack_direction_analysis"),
            )
        except Exception:
            pass

        # v1.7.96: 09:26 集合竞价后开盘共性 AI 分析
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("auction_summary_0926", "竞价播报·09:26(开盘共性+板块强弱合并)",
                 "9:26 一张合并卡: AI 开盘共性(指数+高开低开榜+强势密度) + 竞价板块强弱(行业/概念最强+持仓关联+昨日承接); 原两条推送合并",
                 "cron", _json.dumps({"hour": 9, "minute": 26}), "run_auction_0926"),
            )
        except Exception:
            pass

        # v1.7.272: 09:26 自选股集合竞价成交额采集 (落库, 供后续验证竞价因子)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("auction_pool_0926", "自选股集合竞价成交额·09:26 采集",
                 "9:25 集合竞价撮合完成后, 采集所有自选股的开盘价/昨收/高开幅度/竞价成交额/竞价成交量, 写入 cfzy_biz_auction_pool",
                 "cron", _json.dumps({"hour": 9, "minute": 26}), "record_auction_pool_snapshot"),
            )
        except Exception:
            pass

        # v1.7.276: 09:31 竞价高开弱转强 首日自检 → 飞书 (临时验证, 确认后删此行下线)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("auction_strength_selfcheck", "竞价高开弱转强·09:31 首日自检(推飞书)",
                 "9:31 核查 情绪时效/竞价采集/竞价额≥1亿只数/买点是否触发/服务健康, 推飞书; 临时验证任务",
                 "cron", _json.dumps({"hour": 9, "minute": 31}), "run_auction_strength_selfcheck"),
            )
        except Exception:
            pass

        # v1.7.x: 各买入模型 全市场按周回测 (周六 08:17, 拉全A跑近半年5模型, 写 cfzy_biz_model_backtest)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("model_backtest_weekly", "各买入模型·全市场按周回测",
                 "每周六08:17拉全A日K, 跑近半年5模型回测(胜率/资金加权占用/年化资金效率/盈利因子), 写 cfzy_biz_model_backtest",
                 "cron", _json.dumps({"day_of_week": "sat", "hour": 8, "minute": 17}), "run_model_backtest_weekly"),
            )
        except Exception:
            pass

        # v1.7.98: 下线 report_0926 (与 auction_summary_0926 重叠, 9:26 只保留聚焦集合竞价的那个)
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
                ("report_0926",),
            )
        except Exception:
            pass

        # v1.7.499: 人气榜 AI 解读由 16:00/19:00 两点改为 9-22 每小时(popularity_ai_hourly), 清掉旧两行
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id IN (%s, %s)",
                ("popularity_ai_1600", "popularity_ai_1900"),
            )
        except Exception:
            pass

        # v1.7.x: 风险公告(18:00)+财务红旗(18:30)合并成「黑天鹅预警」单卡(blackswan_scan 18:30), 清掉旧两行
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id IN (%s, %s)",
                ("risk_ann_scan", "fin_risk_scan"),
            )
        except Exception:
            pass

        # v1.7.x: 板块退潮(detect_sector_ebb)已并入 sector_rotation 的「强转弱」(只推持仓踩线题材),
        # 独立任务退役, handler 已删; 清掉存量库的行
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
                ("detect_sector_ebb",),
            )
        except Exception:
            pass

        # v1.7.x: 提醒节流 flush 兜底 (60秒间隔, 把所有到期的合并缓冲推出)
        try:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("alert_throttle_flush", "提醒节流·合并发送",
                 "每60秒兜底 flush 已到节流窗口期的合并提醒(资金回流·板块预警、卖点撤销 等)",
                 "interval", _json.dumps({"seconds": 60}), "flush_alert_throttle"),
            )
        except Exception:
            pass


# ── v1.7.181: 买卖点 signal_id 统一规范化重命名 (幂等) ──
# 旧 signal_id → (新 signal_id, 新静态名; None=名称动态不强改)
SIGNAL_ID_RENAMES: dict[str, tuple[str, str | None]] = {
    "S0_WEAK_EXTREME":       ("BUY_WEAK_EXTREME",     "弱势极限（左侧）"),
    "STRONG_START":          ("BUY_STRONG_START",     "强势起点（右侧）"),
    "SS1_SELL":              ("SELL_BREAK_MA5",        "短线卖 跌破MA5"),
    "SS2_SELL":              ("SELL_BREAK_MA10",       "短线卖 跌破MA10"),
    "SS3_SELL":              ("SELL_BREAK_MA20",       "短线卖 跌破MA20"),
    "SR1_REDUCE":            ("SELL_TAKE_PROFIT",      None),
    "TRAIL_STOP_PROFIT":     ("SELL_TRAIL_STOP",       None),
    "RR_TAKE_PROFIT":        ("SELL_RR_TARGET",        None),
    "TIME_STOP":             ("SELL_TIME_STOP",        None),
    "PLOSS_5":               ("SELL_LOSS_5",           "浮亏止损 -5%"),
    "PLOSS_8":               ("SELL_LOSS_8",           "浮亏止损 -8%"),
    "PLOSS_10":              ("SELL_LOSS_10",          "浮亏止损 -10%"),
    "CAPITAL_INFLOW_SECTOR": ("SECTOR_CAPITAL_INFLOW", "资金回流·板块预警"),
    "PLUNGE_INDEX_DROP":     ("PLUNGE_INDEX",          None),
    "STRENGTH_QUALITY":      ("SCORE_STRENGTH",        None),
    "MAINSTREAM_THEME":      ("SCORE_THEME",           None),
}
# 配置 JSON 键改名: signal 重命名 + 配置键独有的 CAPITAL_INFLOW → SECTOR_CAPITAL_INFLOW
SIGNAL_CONFIG_KEY_RENAMES: dict[str, str] = {
    **{old: new for old, (new, _) in SIGNAL_ID_RENAMES.items()},
    "CAPITAL_INFLOW": "SECTOR_CAPITAL_INFLOW",
}
# 历史死码 (代码已不再产生): 直接删除其历史行
LEGACY_SIGNAL_IDS: list[str] = [
    "WEAK_EXTREME_PERSIST", "W1_VOL_BREAK", "S4_BUY", "S0_WEAK_EXTREME_MA20",
    "W2_MA_SUPPORT", "SECTOR_LEADER", "PLOSS_3", "S3_BUY", "PLOSS_DRAG", "PLOSS_15",
]


async def _migrate_signal_ids(conn):
    """v1.7.181: 统一买卖点 signal_id 编码 — 幂等. 重命名活跃信号 + 删历史死码 + 配置键改名."""
    import json as _json
    async with conn.cursor() as cur:
        # 1) 重命名活跃 signal_id (+ 静态名)
        for old, (new, new_name) in SIGNAL_ID_RENAMES.items():
            try:
                if new_name is not None:
                    await cur.execute(
                        "UPDATE cfzy_biz_signals SET signal_id=%s, signal_name=%s WHERE signal_id=%s",
                        (new, new_name, old),
                    )
                else:
                    await cur.execute(
                        "UPDATE cfzy_biz_signals SET signal_id=%s WHERE signal_id=%s",
                        (new, old),
                    )
            except Exception as e:
                logger.warning(f"[migrate_signal_ids] rename {old}->{new} 失败: {e}")
        # 2) 删除历史死码行
        try:
            ph = ",".join(["%s"] * len(LEGACY_SIGNAL_IDS))
            await cur.execute(
                f"DELETE FROM cfzy_biz_signals WHERE signal_id IN ({ph})", LEGACY_SIGNAL_IDS,
            )
        except Exception as e:
            logger.warning(f"[migrate_signal_ids] 删除历史死码失败: {e}")
        # 3) 配置 JSON 键改名 (活跃键迁移, 历史键保留)
        try:
            await cur.execute("SELECT user_id, config FROM cfzy_biz_signal_config")
            for user_id, cfg_text in await cur.fetchall():
                if not cfg_text:
                    continue
                try:
                    cfg = _json.loads(cfg_text)
                except Exception:
                    continue
                changed = False
                for old, new in SIGNAL_CONFIG_KEY_RENAMES.items():
                    if old in cfg:
                        if new not in cfg:
                            cfg[new] = cfg[old]
                        del cfg[old]
                        changed = True
                if changed:
                    await cur.execute(
                        "UPDATE cfzy_biz_signal_config SET config=%s WHERE user_id=%s",
                        (_json.dumps(cfg, ensure_ascii=False), user_id),
                    )
        except Exception as e:
            logger.warning(f"[migrate_signal_ids] 配置键改名失败: {e}")
    logger.info("[migrate_signal_ids] 买卖点编码规范化迁移完成")


async def _seed_admin(conn):
    from backend.core.auth import hash_password
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT id FROM cfzy_sys_users WHERE username = %s", ("admin",))
        if not await cur.fetchone():
            pw_hash, salt = hash_password("admin123")
            await cur.execute(
                "INSERT INTO cfzy_sys_users (username, password_hash, salt, role) VALUES (%s, %s, %s, %s)",
                ("admin", pw_hash, salt, "admin"),
            )
            logger.info("Seeded admin user")


async def _seed_scheduled_tasks(conn):
    import json
    async with conn.cursor(aiomysql.DictCursor) as cur:
        # v1.7.x: 增量任务 — 不受"表为空才种子"限制, 始终 INSERT IGNORE
        # 给已部署(表非空)的生产库补建新任务; job_id UNIQUE 保证幂等不重复
        incremental = [
            ("stock_tags_refresh", "股票池标签刷新",
             "每20分钟刷新股票池概念题材与连板数标签", "interval",
             {"seconds": 1200}, "refresh_stock_tags"),
            ("emotion_refresh", "情绪温度刷新",
             "每3分钟拉涨停/炸板池, 算封板率/连板梯队/情绪阶段, 写 cfzy_sys_emotion_snapshot", "interval",
             {"seconds": 180}, "refresh_emotion_snapshot"),
            ("blogger_posts_scan", "博主发帖跟踪",
             "每5分钟拉同花顺投资圈博主(全能的野人)新动态, 解析个股标签, 新帖推送企微/飞书", "interval",
             {"seconds": 300}, "scan_blogger_posts"),
            ("near_buy_refresh", "临近买点快照",
             "每3分钟扫全自选+持仓, 算各票距四买点(弱势极限/回踩10MA缩量后突破昨高/回踩20MA缩量后突破昨高/强势起点)的接近度(触发/接近两档), 写 cfzy_sys_near_buy_snapshot, 供监控看板临近买点榜", "interval",
             {"seconds": 180}, "refresh_near_buy_snapshot"),
            ("theme_heat_refresh", "题材热度快照",
             "交易日每5分钟聚合涨停池(同花顺涨停题材首标签)各题材涨停家数, 写 cfzy_sys_theme_heat, 供监控看板市场情绪温度表(日期×题材矩阵)", "interval",
             {"seconds": 300}, "refresh_theme_heat"),
            # v1.7.547: 问财候选榜改「手工触发」(原 wencai_scan 定时任务下线) — 同花顺问财逆向接口反爬易失效,
            # 定时空转徒增风控, 改为用户在问财候选榜页点「立即跑问财」按需触发(POST /api/wencai/scan)。
            # 存量库的 wencai_scan 任务行由下方迁移块置 enabled=0; scan_wencai handler 保留(避免孤儿告警, 备将来重启用)。
            ("sector_strength_refresh", "持仓板块内强弱",
             "交易日每60秒刷持仓最热题材板块的全成分股涨幅名单(内存缓存), 供 quote_refresher 每3s用实时涨幅插值算板块内名次, 写 cfzy_biz_stock_pool.board_rank", "interval",
             {"seconds": 60}, "refresh_sector_strength"),
            ("sector_rotation_scan", "板块轮动·弱强转换",
             "交易日每3分钟按题材聚合涨停池(涨停家数/最高连板/炸板), 算盘中状态(启动/升温/高潮/退潮/冷)写 cfzy_sys_sector_rotation 供看板; 状态跃迁(弱转强启动/强转弱退潮)节流推送", "interval",
             {"seconds": 180}, "scan_sector_rotation"),
            ("sector_next_day_predict", "次日板块预测",
             "每交易日14:30用 theme_heat 多日涨停序列+今日质地做次日预测(弱转强候选/强转弱候选/强势延续/疑似终结), 推送+写 cfzy_sys_sector_rotation.predict_data; 启发式未回测", "cron",
             {"hour": 14, "minute": 30}, "predict_sector_next_day"),
            ("model_winrate_refresh", "模型胜率·每日重算",
             "每日17:30从本地全市场库(kline_cache)重算5个买入模型 近3月/近6月 胜率+单笔均收益, 写 cfzy_biz_model_winrate, 供买入提醒带全市场回测战绩", "cron",
             {"hour": 17, "minute": 30}, "refresh_model_winrate"),
            # v1.7.x: 持仓态前向分布·每周重算 — 全市场五年扫描很重且分布周与周几乎不变, 周日19:00跑一次,
            # 写 cfzy_biz_holding_state_fwd, 供持仓研判晚报(20:00)给每只持仓挂「同类形态历史次日/3日真实分布」客观概率
            ("holding_state_fwd_refresh", "持仓态前向分布·每周重算",
             "每周日19:00扫全市场五年日线缓存, 按持仓态(多头站均线/回踩支撑/高位放量滞涨/跌破MA20/缩量整理)"
             "统计各态 T+1/T+3 真实前向收益分布(上涨概率/中位/p10/p90), 写 cfzy_biz_holding_state_fwd", "cron",
             {"day_of_week": "sun", "hour": 19, "minute": 0}, "refresh_holding_state_fwd"),
            # v1.7.x: 持仓研判晚报 — 交易日前夜20:00对持仓逐股数据体检+AI次日方向性建议(持有/减/清/加+目标价/止损价)+
            # 同类形态历史前向分布客观概率, 推一张卡。内部判"明天是交易日"才发(周五晚/节假日前不发)。
            ("holding_evening_report", "持仓研判晚报·20:00",
             "交易日前夜20:00对持仓逐股做数据体检(成本/浮盈/持仓态/同类形态历史次日分布/建仓模型实测胜率/板块内强弱/守护信号)"
             "+喂DeepSeek出次日方向性操作建议(持有/减仓/清仓/加仓+目标价+止损价), 推一张飞书卡+微信文本; 空仓只发一行", "cron",
             {"hour": 20, "minute": 0}, "run_holding_evening_report"),
            # v1.7.x: 风险公告(原18:00)+财务红旗(原18:30)合并成一张「黑天鹅预警」两区域卡, 18:30一次发出
            # (旧 risk_ann_scan/fin_risk_scan 两行由上方 _seed 迁移块 DELETE 清掉)
            ("blackswan_scan", "自选股黑天鹅预警·18:30",
             "每日18:30并发跑①风险公告(巨潮cninfo近7天公告, 命中立案/处罚/问询/非标/变更会计所/ST等硬信号)"
             "②财务红旗(巨潮年报三表+新浪明细9项打分, 任一强或≥2中红旗), 把两边本次新增合并成一张两区域卡推送; "
             "各自去重(cfzy_biz_risk_ann_seen / cfzy_biz_fin_risk.pushed_key), ≥1区域有新增才发; 纯提示不碰买卖点", "cron",
             {"hour": 18, "minute": 30}, "scan_blackswan_alerts"),
            # v1.7.x: 原在 defaults 块, 但存量库(表非空)会被早返回跳过 → 生产从未注册, 资金曲线恒空。挪到增量块补建。
            ("paper_equity_snapshot", "模拟账户收盘盯市", "每交易日15:05对模拟持仓盯市并写资金曲线",
             "cron", {"hour": 15, "minute": 5}, "snapshot_paper_equity"),
            ("signal_eod_audit", "信号EOD自动复核",
             "每交易日17:00用收盘真实日线复核当日全部信号(K线序列指纹/触发价区间/涨跌家数自洽/指数波幅容纳急跌), "
             "数据层假象标记存疑(不自动删)并推送提醒, 写 cfzy_biz_signals.eod_audit", "cron",
             {"hour": 17, "minute": 0}, "signal_eod_audit"),
            # v1.7.x: 市场风险两级预警(替代空仓预警 v1.7.406).
            # GREEN(正常)/YELLOW(谨慎)/RED(空仓) 三态, 回测: RED期胜率30.3%均值-3.56%(vs全部+3.63%)
            ("market_risk_eod", "市场风险·收盘评估16:40",
             "每交易日16:40从全市场日线(kline_cache)+新浪快照算涨跌比/广度/5日均收益/新低比/炸板率, 跑两级状态机"
             "(GREEN→YELLOW:广度<30%或涨跌比<30%或炸板>60%; YELLOW→RED:5日均收益<-1%或新低>15%或广度<15%), 状态迁移推送+落库 cfzy_biz_market_risk",
             "cron", {"hour": 16, "minute": 40}, "market_risk_eod"),
            ("market_risk_intraday", "市场风险·盘中预升级14:40",
             "尾盘14:40同口径估当日指标, 达RED进入条件提前升级推送(只升不降, 16:40收盘复核为准), 给尾盘买点打标",
             "cron", {"hour": 14, "minute": 40}, "market_risk_intraday"),
            ("market_risk_realtime", "市场风险·实时检测(10-14:30每5分)",
             "盘中10:00-14:30每5分钟用自选池实时行情(pct_change)算涨跌比/均收益, 更严阈值(涨跌<22%+均收益<-2%→RED; 涨跌<28%或均<-1%→YELLOW), 只升不降, 同日同状态不重复推",
             "interval", {"seconds": 300}, "market_risk_realtime"),
            ("cross_check", "数据源交叉校验·60分钟",
             "每60分钟抽检涨跌幅(新浪vs东财)/涨跌家数(新浪vs腾讯)/行情覆盖率, 超阈值飞书告警",
             "interval", {"seconds": 3600}, "run_cross_check"),
            # v1.7.499: 人气榜 AI 解读改每小时(9-22)全量重刷, 替代原 16:00/19:00 两点 (旧两行由下方 _run_migrations DELETE 清掉)
            ("popularity_ai_hourly", "人气榜 AI 解读·盘中+收盘两点(11:30/15:30)",
             "9:00~22:00 每小时给 TOP20 全量重新生成 AI 解读, 含盘前/盘中/盘后持续发酵的公告/新闻; 标注刷新时间显示在前端", "cron",
             {"hour": "11,15", "minute": 30}, "refresh_popularity_full_ai"),
            # 股票池自定义预警: 与股票池扫描同节奏(交易时段), 逐用户独立条件检测+推送
            ("custom_alert_scan", "自定义预警检测",
             "交易时段按股票池扫描同节奏检测用户自定义预警(价格/涨跌幅/接近均线/上穿跌破均线), 满足即逐用户推送并标记触发",
             "interval", {"seconds": load_config().get("scan_interval_seconds", 30)}, "check_custom_alerts"),
            # 全市场股票名称刷新: 每日07:30(交易日前)走新浪批量行情拉全A名写 cfzy_sys_stock_names,
            # 供模型回测逐笔明细给全市场票补名(自选池只覆盖少量)
            ("stock_names_refresh", "全市场名称刷新",
             "每日07:30用新浪批量行情拉全市场(kline_cache DISTINCT code)股票名称, upsert 进 cfzy_sys_stock_names, 供模型回测逐笔明细全市场票补名",
             "cron", {"hour": 7, "minute": 30}, "refresh_stock_names"),
        ]
        for job_id, name, desc, stype, sconfig, handler in incremental:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (job_id, name, desc, stype, json.dumps(sconfig), handler),
            )

        await cur.execute("SELECT COUNT(*) AS cnt FROM cfzy_sys_scheduled_tasks")
        row = await cur.fetchone()
        if row and row["cnt"] > len(incremental):
            return
        app_cfg = load_config()
        scan_interval = app_cfg.get("scan_interval_seconds", 30)
        defaults = [
            ("scan", "股票池扫描", "定时扫描股票池，检测交易信号", "interval", {"seconds": scan_interval}, "scan_stock_pool"),
            ("quote_refresh", "行情刷新", "高频刷新股票实时行情数据", "interval", {"seconds": 3}, "refresh_quotes"),
            # v1.7.98: report_0926 已下线 (与 auction_summary_0926 重叠, 后者更聚焦集合竞价共性)
            ("report_1000", "早盘分析 10:00", "AI生成早盘跟踪报告", "cron", {"hour": 10, "minute": 0}, "run_market_report"),
            ("report_1130", "午盘分析 11:30", "AI生成上午收盘报告", "cron", {"hour": 11, "minute": 30}, "run_market_report"),
            ("report_1400", "午后分析 14:00", "AI生成午后分析报告", "cron", {"hour": 14, "minute": 0}, "run_market_report"),
            ("report_1500", "收盘分析 15:00", "AI生成收盘总结报告", "cron", {"hour": 15, "minute": 0}, "run_market_report"),
            # paper_equity_snapshot 已挪到上方 incremental 块(存量库也能补建), 此处不再重复
            ("market_data_refresh", "市场数据刷新", "每60秒刷新大盘指数走势和涨跌停统计", "interval", {"seconds": 60}, "refresh_market_data"),
            ("popularity_refresh", "人气排行刷新", "每60秒刷新人气排行榜数据", "interval", {"seconds": 60}, "refresh_popularity"),
            ("popularity_daily_2200", "人气排名·每日存档 22:00", "每晚22:00拉自选池全量人气排名写 cfzy_biz_popularity_daily 供回测/复盘", "cron", {"hour": 22, "minute": 0}, "record_daily_popularity"),
            ("market_risk_eod", "市场风险·收盘评估16:40", "每日16:40算涨跌比/广度/5日均收益/新低比/炸板率跑两级状态机(GREEN/YELLOW/RED)", "cron", {"hour": 16, "minute": 40}, "market_risk_eod"),
            ("market_risk_intraday", "市场风险·盘中预升级14:40", "尾盘14:40同口径估当日指标达RED条件提前升级(只升不降)", "cron", {"hour": 14, "minute": 40}, "market_risk_intraday"),
            ("market_risk_realtime", "市场风险·实时检测(10-14:30每5分)", "盘中10:00-14:30每5分钟用自选池实时行情算涨跌比/均收益, 只升不降", "interval", {"seconds": 300}, "market_risk_realtime"),
            ("cross_check", "数据源交叉校验·60分钟", "每60分钟抽检涨跌幅(新浪vs东财)/涨跌家数(新浪vs腾讯)/行情覆盖率, 超阈值飞书告警", "interval", {"seconds": 3600}, "run_cross_check"),
            # v1.7.97: 实时市场概览快照, 单行 UPSERT, 前端 MarketOverviewBar 从 DB 读
            ("market_overview_refresh", "市场概览快照", "每30秒拉全球+A股指数+涨跌停, 写入 cfzy_sys_market_overview", "interval", {"seconds": 30}, "refresh_market_overview"),
        ]
        for job_id, name, desc, stype, sconfig, handler in defaults:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_sys_scheduled_tasks "
                "(job_id, name, description, schedule_type, schedule_config, handler) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (job_id, name, desc, stype, json.dumps(sconfig), handler),
            )
        logger.info("Seeded default scheduled tasks")


async def init_db():
    global _pool
    cfg = load_config().get("database", {})
    _pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 3306),
        user=cfg.get("user", "root"),
        password=cfg.get("password", ""),
        db=cfg.get("db", "trading"),
        charset="utf8mb4",
        autocommit=True,
        # 生产 DB 跨云(火山引擎 RDS), 单查询往返~44ms、建连接~280ms。
        # minsize 提到 5 常驻保温连接, 避免冷连接每次付 280ms 重连;
        # maxsize 提到 25 缓解前台接口 + 一堆高频后台任务(3s/30s/60s)争抢连接排队;
        # pool_recycle 1h 主动回收, 防远端 wait_timeout 静默掐断后拿到死连接。
        minsize=5,
        maxsize=25,
        pool_recycle=3600,
    )
    async with _pool.acquire() as conn:
        await _rename_tables(conn)
        async with conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                await cur.execute(stmt)
        await _run_migrations(conn)
        await _migrate_signal_ids(conn)
        await _seed_admin(conn)
        await _seed_scheduled_tasks(conn)
    logger.info("Database initialized")


async def close_db():
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


def get_pool() -> aiomysql.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db() first.")
    return _pool
