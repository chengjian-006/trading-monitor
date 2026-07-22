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
        preset            VARCHAR(12) NOT NULL DEFAULT '',
        repeat_daily      TINYINT NOT NULL DEFAULT 0,
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
    CREATE TABLE IF NOT EXISTS cfzy_sys_disclosure_calendar (
        code          VARCHAR(16) NOT NULL,
        report_year   VARCHAR(8) NOT NULL,
        report_type   VARCHAR(8) NOT NULL,
        name          VARCHAR(50) NOT NULL DEFAULT '',
        appoint_date  DATE DEFAULT NULL,
        actual_date   DATE DEFAULT NULL,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (code, report_year, report_type),
        INDEX idx_appoint (appoint_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_earnings_forecast (
        code          VARCHAR(16) NOT NULL,
        report_date   VARCHAR(12) NOT NULL,
        name          VARCHAR(50) NOT NULL DEFAULT '',
        notice_date   DATE DEFAULT NULL,
        predict_type  VARCHAR(16) NOT NULL DEFAULT '',
        forecast_group VARCHAR(8) NOT NULL DEFAULT '',
        amp_lower     DOUBLE DEFAULT NULL,
        amp_upper     DOUBLE DEFAULT NULL,
        content       VARCHAR(500) NOT NULL DEFAULT '',
        pushed_at     DATETIME DEFAULT NULL,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (code, report_date),
        INDEX idx_notice (notice_date)
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
    # 回测结论登记表 (v1.7.711) — 系统对外宣称的每一个战绩数字都必须登记在此, 代码/前端
    # 只引用 claim_key, 不写字面量。
    # 起因: 0719 全库扫描发现约 50 处硬编码战绩数字, 它们来自某次一次性分析, 写进代码后
    # 与来源彻底断链 —— 源头脚本改了/样本过期了/结论被证伪了, 代码里的数字纹丝不动。实例:
    # 推送里「胜率30%均值-3.6%」用了很久其实来自带前视偏差的旧回测; 模型图鉴写「实测胜率
    # 74%」而同页实时表是 54%。
    # kind=auto: 有数据源可定时重算, 永不过期; kind=manual: 一次性研究结论, 到期提醒复验。
    # 体检项 claim_stale 扫本表, computed_at 超 ttl_days 即报"结论待复验"。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_backtest_claims (
        claim_key    VARCHAR(64)  NOT NULL PRIMARY KEY,
        value_json   TEXT         NULL,
        text         VARCHAR(500) NOT NULL DEFAULT '',
        src          VARCHAR(120) NOT NULL DEFAULT '',
        window_desc  VARCHAR(64)  NOT NULL DEFAULT '',
        kind         VARCHAR(10)  NOT NULL DEFAULT 'manual',
        ttl_days     INT          NOT NULL DEFAULT 180,
        computed_at  DATETIME     NOT NULL,
        updated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    # 告警去重/冷却状态 (v1.7.699) — 统一六套并行的进程内冷却计时器。
    # 原来 data_sanity(30min) / task_registry(60min每job) / cross_check(按日) /
    # data_health(按日) / wencai(12h) / alert_throttle(15min每类) 各自用模块级 dict 记时,
    # 服务一重启全部归零 —— 而近14天重启约97次(约7次/天, 主要来自部署), 于是"持续成立"
    # 的问题(如行情陈旧)每次重启后都会重新推一遍, 同一问题一天能轰炸五六条。
    # 落库后冷却期跨重启生效。alert_key 为调用方自定的稳定键(如 task_fail:job_id)。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_alert_dedup (
        alert_key    VARCHAR(96) NOT NULL PRIMARY KEY,
        last_sent_at DATETIME    NOT NULL,
        sent_count   INT         NOT NULL DEFAULT 1,
        updated_at   TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_sent (last_sent_at)
    )
    """,
    # 系统体检结果 (v1.7.698) — 每轮体检把**每一项**的判定结果落库, 再推报告。
    # 为什么必须落库: system_health 的旧做法是进程内累积 + 21:00 推送 + finally 无条件清空,
    # 推送失败 = 当日全部故障记录永久蒸发, 且重启即丢。落库后推送只是"展示层",
    # 推失败下轮还能补报, 也留下可回溯的历史(用于看某项是偶发还是持续劣化)。
    # ok: 1通过 0失败; severity: critical/warn/info; actual/expected 存实际值与期望值原文。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_health_check (
        run_at     DATETIME     NOT NULL,
        check_key  VARCHAR(48)  NOT NULL,
        category   VARCHAR(16)  NOT NULL DEFAULT '',
        name       VARCHAR(64)  NOT NULL DEFAULT '',
        severity   VARCHAR(10)  NOT NULL DEFAULT 'warn',
        ok         TINYINT      NOT NULL DEFAULT 1,
        actual     VARCHAR(255) NOT NULL DEFAULT '',
        expected   VARCHAR(255) NOT NULL DEFAULT '',
        detail     VARCHAR(500) NOT NULL DEFAULT '',
        PRIMARY KEY (run_at, check_key),
        INDEX idx_key_time (check_key, run_at)
    )
    """,
    # 体检报告推送心跳 (v1.7.698) — 单行表, 记最后一次**成功推送**体检报告的时刻。
    # 告警通路自身没有心跳时, 系统可以在完全静默的状态下跑任意久(飞书token过期/群解散/
    # 出口IP变更导致 is_production 恒False, 都不会有任何响声)。报告里带上"距上次成功推送
    # N 小时", 就把"没消息"和"消息发不出去"区分开了。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_health_heartbeat (
        id            TINYINT     NOT NULL PRIMARY KEY DEFAULT 1,
        last_push_at  DATETIME    DEFAULT NULL,
        last_fail_at  DATETIME    DEFAULT NULL,
        fail_streak   INT         NOT NULL DEFAULT 0,
        updated_at    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    # 指数 5 分钟 K 线 (v1.7.692) — 上证/深成/创业板指, 新浪源每 5 分钟增量追加。
    # code 必须带市场前缀(sh000001/sz399001/sz399006): 裸码会与个股撞车 —— cfzy_sys_kline_5m
    # 里的 "000001" 实为平安银行而非上证指数(0719 排查确认)。与个股 5m 分表存, 互不污染。
    # 注意: 本表为**不复权**原始指数点位(指数不除权, 无复权概念); 而 cfzy_sys_kline_5m
    # 是个股**后复权**价, 两表价格口径不同, 不可直接比较。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_index_kline_5m (
        code    VARCHAR(16) NOT NULL,
        dt      DATETIME    NOT NULL,
        open    DOUBLE,
        high    DOUBLE,
        low     DOUBLE,
        close   DOUBLE,
        volume  BIGINT,
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
        deal_no       VARCHAR(40) DEFAULT NULL,
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
    # 飞书群「藏龙岛观点」— 只存群主(藏龙岛)发的消息, message_id 唯一去重, 不推送。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_lark_coach_posts (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        message_id      VARCHAR(80) NOT NULL,
        chat_id         VARCHAR(80) NOT NULL DEFAULT '',
        sender_open_id  VARCHAR(80) NOT NULL DEFAULT '',
        coach_name      VARCHAR(50) NOT NULL DEFAULT '',
        posted_at       DATETIME DEFAULT NULL,
        content         MEDIUMTEXT,
        msg_type        VARCHAR(20) NOT NULL DEFAULT 'text',
        raw             MEDIUMTEXT,
        fetched_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE INDEX uk_coach_msg (message_id),
        INDEX idx_coach_posted (posted_at)
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
    # 问财观点参考 (v1.7.627) — 同花顺问财 chat「智能调度」投顾式推荐的存档. 本地油猴代跑(登录态浏览器发 stream-query
    #   SSE, 走 aime deep_research/普通 agent), 把口语问题的整段话术 + 从话术里撞出的股票 上报落库. 明确是 LLM 观点非回测信号.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_wencai_opinion (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT NOT NULL DEFAULT 0,
        question    VARCHAR(255) NOT NULL DEFAULT '',
        answer_text MEDIUMTEXT,
        stocks      JSON,
        agent_mode  VARCHAR(20) NOT NULL DEFAULT '',
        trace_id    VARCHAR(64) NOT NULL DEFAULT '',
        uploader    VARCHAR(40) NOT NULL DEFAULT '',
        reasoning   MEDIUMTEXT,
        conclusion  JSON,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user_time (user_id, created_at)
    )
    """,
    # 每日涨停复盘存档 (v1.7.572) — 明细: 每交易日每只涨停股一行(代码/名称/板数/连板标签/涨停概念/涨幅/炸板),
    # 由 run_limit_up_daily 收盘后15:35拉同花顺涨停池写入. 供「每日涨停复盘」看板/导出/推送 + 概念上榜历史分析.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_limit_up_pool (
        trade_date   VARCHAR(10) NOT NULL,
        code         VARCHAR(10) NOT NULL,
        name         VARCHAR(50) NOT NULL DEFAULT '',
        height       INT NOT NULL DEFAULT 1,
        streak_label VARCHAR(20) NOT NULL DEFAULT '',
        reason       VARCHAR(255) NOT NULL DEFAULT '',
        pct          DOUBLE DEFAULT NULL,
        open_times   INT NOT NULL DEFAULT 0,
        PRIMARY KEY (trade_date, code),
        INDEX idx_date (trade_date),
        INDEX idx_reason (reason(64))
    )
    """,
    # 每日涨停复盘·日汇总 (v1.7.572) — 每交易日一行: 涨停/曾涨停/跌停/炸板/封板率.
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_limit_up_daily (
        trade_date        VARCHAR(10) NOT NULL,
        limit_up_count    INT DEFAULT NULL,
        limit_up_history  INT DEFAULT NULL,
        limit_down_count  INT DEFAULT NULL,
        broken_board_count INT DEFAULT NULL,
        seal_rate         DOUBLE DEFAULT NULL,
        updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date)
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
        monthly_json TEXT NULL,
        max_drawdown DOUBLE NULL,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (signal_id)
    )
    """,
    # 胜率重算「断点续算」暂存 (v1.7.x): 每票算完把窗口内 [(模型名,触发日,净收益)] 落这里(哪怕空也落, 标记已算)。
    # 服务高频重启会杀掉 21:00 的 6h 长任务, 有了逐票暂存, 重启后从断点接着算、被杀不白算;
    # 全部票齐了再一次性聚合写 cfzy_biz_model_winrate, 然后清空本表。anchor=锚点交易日, 换日即弃旧。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_model_winrate_stage (
        anchor      VARCHAR(10) NOT NULL,
        code        VARCHAR(10) NOT NULL,
        trades_json MEDIUMTEXT NULL,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (anchor, code)
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
    # 交易日记 (v1.7.669): 手动记录每笔买卖的理由/心态/复盘, 事后回看决策模式, 与"交易分析(客观数据)"互补
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_trade_journal (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL DEFAULT 1,
        code VARCHAR(8) NOT NULL DEFAULT '',
        name VARCHAR(32) NOT NULL DEFAULT '',
        side VARCHAR(12) NOT NULL DEFAULT '',
        trade_date DATE NULL,
        price DECIMAL(10,3) NULL,
        qty INT NULL,
        reason TEXT NULL,
        emotion VARCHAR(24) NOT NULL DEFAULT '',
        review TEXT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_journal_user_date (user_id, trade_date)
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
    # 全市场行业映射 (v1.7.598) — 问财口径「所属同花顺行业」(三级), industry_map_refresh 每周日刷新,
    # 供板块共振·禁补仓提示(sector_cocrash_guard 14:30)算各行业大跌占比。
    """
    CREATE TABLE IF NOT EXISTS cfzy_sys_industry_map (
        code VARCHAR(10) NOT NULL PRIMARY KEY,
        industry VARCHAR(64) NOT NULL DEFAULT '',
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
    # 官网内测申请 (v1.7.613) — 主域名官网表单免鉴权提交, 落此表 + 飞书通知。
    # ip 用于防刷(同 IP 24h 上限)与溯源; status: new(待处理)/contacted(已联系)/rejected。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_beta_apply (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        contact    VARCHAR(60) NOT NULL DEFAULT '',
        remark     VARCHAR(500) NOT NULL DEFAULT '',
        ip         VARCHAR(45) NOT NULL DEFAULT '',
        user_agent VARCHAR(255) NOT NULL DEFAULT '',
        status     VARCHAR(20) NOT NULL DEFAULT 'new',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_ip_created (ip, created_at),
        INDEX idx_created (created_at)
    )
    """,
    # AI 交易教练复盘缓存 (Phase1 T3) — 同用户+同区间+同天命中缓存, 免得每次刷新都重调LLM。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_coach_report (
        user_id     INT NOT NULL,
        period_key  VARCHAR(40) NOT NULL,
        gen_date    DATE NOT NULL,
        facts_json  MEDIUMTEXT NOT NULL,
        narrative   MEDIUMTEXT NULL,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, period_key, gen_date)
    )
    """,
    # AI 个股研判缓存 (Phase2 T2) — 同用户+同票+同天命中缓存, 免得每次点开都重调LLM。
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_stock_review (
        user_id     INT NOT NULL,
        code        VARCHAR(10) NOT NULL,
        gen_date    DATE NOT NULL,
        facts_json  MEDIUMTEXT NOT NULL,
        narrative   MEDIUMTEXT NULL,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, code, gen_date)
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
    # v1.7.746: 藏龙岛观点转发到自建群 —— 逐条转发状态(NULL=待转发, 失败下轮重试)
    "ALTER TABLE cfzy_biz_lark_coach_posts ADD COLUMN relayed_at DATETIME DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN user_id INT NOT NULL DEFAULT 1",
    # v1.7.571: 原来这里有一条 "ALTER ... DROP PRIMARY KEY, ADD PRIMARY KEY (code,user_id)",
    #   它每次启动都"成功"执行(非幂等错误, 吞错机制拦不住)=每次重启对 stock_pool 全表重建+元数据锁,
    #   与 3 秒一轮的行情写入锁等。已挪到 _run_migrations 里做条件迁移(见 _migrate_stock_pool_pk):
    #   只在当前主键不是 (code,user_id) 时才改。建表本就定义 PK(code,user_id), 新库/已迁移库都直接跳过。
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
    # 自选分组/标签/备注 (v1.7.670)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN grp VARCHAR(24) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN tags VARCHAR(120) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN note VARCHAR(255) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN turnover DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN popularity_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN sort_order INT NOT NULL DEFAULT 0",
    "ALTER TABLE cfzy_sys_users ADD COLUMN wecom_webhook VARCHAR(500) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_sys_users ADD COLUMN push_enabled TINYINT NOT NULL DEFAULT 1",
    "ALTER TABLE cfzy_biz_signals ADD COLUMN indicators JSON DEFAULT NULL",
    "ALTER TABLE cfzy_sys_users ADD COLUMN mobile VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN sector_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN hold_source VARCHAR(10) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_trades ADD COLUMN deal_no VARCHAR(40) DEFAULT NULL",
    # deal_no(成交编号)进唯一键: 等量拆单(同秒同量同价但成交编号不同)在DB层不再被 INSERT IGNORE 丢掉;
    # 老数据 deal_no=NULL, MySQL 唯一键视 NULL 互不相同, 向后兼容(应用层 filter_new_records 主去重)。
    # 既有7列 uk_trade 由 _migrate_trades_uk_deal_no 条件重建(此 ADD 对既有库报1061被幂等吞)。
    "ALTER TABLE cfzy_biz_trades ADD UNIQUE INDEX uk_trade (user_id, trade_date, trade_time, code, direction, quantity, price, deal_no)",
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
    # v1.7.x: 短线情绪快指标 — 两市成交额/量能(放量缩量%)/0-100情绪温度分/四阶段(冰点·回暖·高潮·退潮)
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN market_amount DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN volume_ratio DOUBLE DEFAULT NULL",
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN emotion_score INT DEFAULT NULL",
    "ALTER TABLE cfzy_sys_emotion_snapshot ADD COLUMN emotion_cycle VARCHAR(10) DEFAULT NULL",
    # 持仓在最热题材板块内的强弱名次(quote_refresher 每3s用实时涨幅插值写, sector_strength 60s刷名单)
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_name VARCHAR(50) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_rank INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_stock_pool ADD COLUMN board_total INT DEFAULT NULL",
    # 模型近3月胜率排名(供买入提醒标"全模型第X名")
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN rank_3m INT DEFAULT NULL",
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN rank_n INT NOT NULL DEFAULT 0",
    # v1.7.x: 图鉴果仁式策略卡 — 逐月胜率序列(JSON) + 逐笔权益曲线最大回撤(百分点)
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN monthly_json TEXT NULL",
    "ALTER TABLE cfzy_biz_model_winrate ADD COLUMN max_drawdown DOUBLE NULL",
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
    # v1.7.626: 均线快捷提醒 — preset('ma10'|'ma20'|'ma60', 空=普通自定义) 标记一键开关来源;
    #   repeat_daily=1: 触发后不停用, 每股每档每天最多提醒一次(次日自动恢复监控)
    "ALTER TABLE cfzy_biz_stock_alerts ADD COLUMN preset VARCHAR(12) NOT NULL DEFAULT ''",
    "ALTER TABLE cfzy_biz_stock_alerts ADD COLUMN repeat_daily TINYINT NOT NULL DEFAULT 0",
    # v1.7.633: 问财观点上报人昵称(共用 token 分发扩展时区分是谁问的)
    "ALTER TABLE cfzy_biz_wencai_opinion ADD COLUMN uploader VARCHAR(40) NOT NULL DEFAULT ''",
    # v1.7.636: 问财观点 思考过程(reasoning) + 结构化结论(conclusion) 上网页/存档
    "ALTER TABLE cfzy_biz_wencai_opinion ADD COLUMN reasoning MEDIUMTEXT",
    "ALTER TABLE cfzy_biz_wencai_opinion ADD COLUMN conclusion JSON",
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


async def _migrate_stock_pool_pk(cur):
    """v1.7.571: 条件迁移 stock_pool 主键到 (code, user_id) — 仅当当前主键不同时才改,
    避免每次启动无谓 DROP+ADD 全表重建。建表已定义 PK(code,user_id), 故新库/已迁移库直接跳过。"""
    try:
        await cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'cfzy_biz_stock_pool' "
            "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION")
        cols = [r[0] for r in await cur.fetchall()]
        if cols == ["code", "user_id"]:
            return   # 已是目标主键, 无需改
        await cur.execute("ALTER TABLE cfzy_biz_stock_pool DROP PRIMARY KEY, ADD PRIMARY KEY (code, user_id)")
        logger.info(f"[migration] stock_pool 主键 {cols} → (code, user_id) 已迁移")
    except Exception as e:
        logger.warning(f"[migration] stock_pool 主键条件迁移跳过: {e}")


async def _migrate_trades_uk_deal_no(cur):
    """既有 cfzy_biz_trades.uk_trade(7列)重建为含 deal_no(成交编号)的8列, 让等量拆单不被唯一键丢。
    条件迁移(仿 _migrate_stock_pool_pk): 已含 deal_no 或索引未建则跳过, 避免每次启动重建索引。"""
    try:
        await cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cfzy_biz_trades' "
            "AND INDEX_NAME='uk_trade' ORDER BY SEQ_IN_INDEX")
        cols = [r[0] for r in await cur.fetchall()]
        if not cols or "deal_no" in cols:
            return   # 索引尚未建(全新库由MIGRATION建8列版) 或 已含deal_no → 无需改
        await cur.execute("ALTER TABLE cfzy_biz_trades DROP INDEX uk_trade")
        await cur.execute(
            "ALTER TABLE cfzy_biz_trades ADD UNIQUE INDEX uk_trade "
            "(user_id, trade_date, trade_time, code, direction, quantity, price, deal_no)")
        logger.info(f"[migration] cfzy_biz_trades uk_trade {cols} → 含 deal_no 已迁移")
    except Exception as e:
        logger.warning(f"[migration] uk_trade deal_no 条件迁移跳过: {e}")


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
        await _migrate_stock_pool_pk(cur)   # v1.7.571: 条件迁移主键(替代每次重建的盲ALTER)
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
        # 列 deal_no 已由上面 ADD COLUMN 就位后, 条件重建 uk_trade(含 deal_no)
        await _migrate_trades_uk_deal_no(cur)
        migration_tasks = [
            # v1.7.572: 每日涨停复盘存档+推送 — 收盘后15:40拉同花顺涨停池(每只带涨停概念/板数/炸板)
            # 存 cfzy_sys_limit_up_pool/daily, 并推一张飞书复盘卡(概览+连板梯队+热点分布, 精华版)。
            ("limit_up_daily_1540", "涨停复盘·存档+推送·15:40",
             "每交易日15:40拉同花顺涨停池存档(每只涨停股带涨停概念/板数/炸板)+推一张飞书复盘卡(数据概览+连板梯队+热点分布), 供看板/导出/概念上榜历史分析",
             "cron", _json.dumps({"hour": 15, "minute": 40}), "run_limit_up_daily"),
            # v1.7.557: 推送降噪·批次E — 系统故障告警(数据源交叉校验/博主拉取中断等)不再实时逐类推,
            # 当日累积盘后 21:00 合成一条「系统健康·盘后汇总」, 无异常则不推。
            ("system_health_digest", "系统健康·盘后汇总·21:00",
             "每日 21:00 把当日累积的系统故障(数据源交叉校验偏差/博主拉取中断等)合成一条汇总推送, 无异常不推; 更紧急的行情源健康仍即时告警",
             "cron", _json.dumps({"hour": 21, "minute": 0}), "run_system_health_digest"),
            # v1.7.573: 财报披露日历(防御) + 预增榜(进攻·克制) — 数据走东财datacenter(生产可达)
            ("disclosure_calendar_refresh", "财报预约披露·刷新·08:20",
             "每日 08:20 拉当前报告期定期报告预约披露时间表落库(慢变, 顺带捕捉披露日变更), 供披露日历提醒读取",
             "cron", _json.dumps({"hour": 8, "minute": 20}), "refresh_disclosure_calendar"),
            # v1.7.651: 08:40 独立披露卡下线 — 披露内容并入 19:00 晚盘复盘总结(review_summary),
            #   晚上提前一天知道明日/近期披露, 同属防御; 08:20 refresh_disclosure_calendar 保留当数据源。
            ("earnings_forecast_scan", "预增榜·当日正向业绩预告·18:30",
             "每日 18:30 拉当日新出业绩预告落库, 把正向预告(预增/略增/扭亏等)推一张预增榜卡(自选/持仓命中置顶+全市场大幅预增TopN); 回测背书仅快进快出非埋伏神器",
             "cron", _json.dumps({"hour": 18, "minute": 30}), "run_earnings_forecast_scan"),
            # v1.7.554: 尾盘三卡合并成14:40 tail_decision; v1.7.651: 14:40尾盘决策整卡下线(用户拍板精简盘后推送,
            #   真假强势/次日板块预测不再推; 弱势极限候选仍有11:30快照)。
            ("auction_sector_strength_0926", "竞价分析·09:26",
             "9:25集合竞价撮合后推板块强弱硬数据卡: 行业最强/最弱+概念top10(涨家比/领涨股标红自选)+昨日热点承接度+持仓板块名次; 与AI开盘共性卡互补不走AI",
             "cron", _json.dumps({"hour": 9, "minute": 26}), "run_auction_sector_strength"),
            # v1.7.651: 15:05 盘后信号汇总下线(真假强势/主流题材, 与晚盘复盘总结重叠, 用户拍板精简)。
            ("freeze_intraday_1510", "分时曲线归档·15:10",
             "收盘后冻结股票池当日分时曲线到 cfzy_sys_intraday_snapshot，供分时图历史回放",
             "cron", _json.dumps({"hour": 15, "minute": 10}), "freeze_intraday_snapshots"),
            ("review_summary_2330", "晚盘复盘总结·19:00",
             "晚7点一张晚盘复盘总结: 持仓今日表现(逐票涨跌+浮盈) + 今日信号+近90天买卖点胜率+最好/警惕信号 + 近期财报披露(未来7天)。胜率由16:00回填先行, 此处为当日最新; 披露内容并入(原08:40独立卡下线)",
             "cron", _json.dumps({"hour": 19, "minute": 0}), "run_review_summary"),
            ("prefetch_intraday_sparklines", "分时走势预热",
             "每25秒预拉股票池分时数据，让缓存常热，前端打开页面秒返",
             "interval", _json.dumps({"seconds": 25}), "prefetch_intraday_sparklines"),
            ("market_data_refresh", "市场数据刷新", "每60秒刷新大盘指数走势和涨跌停统计",
             "interval", _json.dumps({"seconds": 60}), "refresh_market_data"),
            ("popularity_refresh", "人气排行刷新", "每60秒刷新人气排行榜数据",
             "interval", _json.dumps({"seconds": 60}), "refresh_popularity"),
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
            # v1.7.x: 止损强制升级 — 硬止损(弱势极限-12%/浮亏止损)连续≥2个交易日未执行(持仓仍在)→ 升级🚨红卡,
            # 带累计多亏, 开盘09:30+午间11:20各推一次; 卡片内置"当日/本周不提醒"(stop_snooze, 只静音升级不影响其它推送)
            ("stop_escalation_0930", "止损未执行升级·开盘09:30",
             "开盘扫真实持仓, 硬止损连续≥2交易日未执行(持仓仍在且现价未站回首次止损位)推🚨升级红卡, 带累计多亏与当日/本周静音开关",
             "cron", _json.dumps({"hour": 9, "minute": 30}), "stop_escalation_tick"),
            ("stop_escalation_1120", "止损未执行升级·午间11:20",
             "午间再扫一次硬止损未执行升级, 兼顾下午决断(口径同开盘)",
             "cron", _json.dumps({"hour": 11, "minute": 20}), "stop_escalation_tick"),
            # v1.7.582: 尾盘破位警戒 — 持仓股14:40贴线判MA5/10/20破位(现价<MA即算), 连续N日从日线回算,
            # 每日尾盘都报直到收复; 与SELL_BREAK_MA*事件卡(≥2%深度只报首日)互补; 卡带ma_watch_snooze逐票静音
            ("ma_break_watch_1440", "尾盘破位警戒·14:40",
             "交易日尾盘对真实持仓逐票贴线判跌破MA5/MA10/MA20(现价<均线即算), 标注连续N日尾盘破位, 合并一张警戒卡, 收复自动消失",
             "cron", _json.dumps({"hour": 14, "minute": 40}), "run_ma_break_watch"),
            # v1.7.597: 分时二波过前高实时提醒 — 每30s纯读分时预热缓存, 全自选逐票判"第一波放量冲高→
            # 回落缩量→第二波放量拉升创当日新高"(确认后报), 每股每天一次; 只提醒不落信号库; surge_snooze逐票静音
            ("second_surge_scan", "二波过前高·实时提醒",
             "盘中(09:45~15:00)每30秒纯读分时缓存, 对全自选池判分时二波过前高形态(第一波放量冲高→回落降温→二波放量拉升创当日新高), 命中实时提醒, 每股每天一次",
             "interval", _json.dumps({"seconds": 30}), "run_second_surge_scan"),
            # v1.7.598: 板块共振·禁补仓提示 — 盘中(09:45~15:00)每3分钟拉全市场涨跌幅, 判"板块共振跌"
            # (大盘正常<10%但某行业大跌占比超出大盘≥20pp、成员≥8), 命中票再过个股破位闸(距峰≤-15%+
            # 收MA20下+MA20拐头), 自选池破位命中才推; 每票每天一次去重; 回测背书该语境抄底/补仓期望为负;
            # 恐慌普跌日不适用不发(该语境历史抄底为正); 不落信号库
            ("sector_cocrash_scan", "板块共振·禁补仓提示·实时",
             "盘中09:45~15:00每3分钟拉全市场涨跌幅(新浪列表页), 判板块共振跌(全市场大跌占比<10%且某行业超出大盘≥20pp、成员≥8), 自选池命中票再过个股破位闸后合并推一张禁补仓提示卡, 每票每天一次",
             "interval", _json.dumps({"seconds": 180}), "run_sector_cocrash_watch"),
            # v1.7.598: 全市场行业映射·每周刷新 — 问财拉「全部A股所属同花顺行业」(三级行业, 全覆盖)
            # upsert 进 cfzy_sys_industry_map; 行业归属极少变动, 周频=少撞风控; 失败保留旧映射
            ("industry_map_refresh", "全市场行业映射·周日19:20",
             "每周日19:20用问财(pywencai)拉全部A股所属同花顺行业, upsert进cfzy_sys_industry_map, 供板块共振禁补仓提示算行业大跌占比; 拉取失败保留旧映射",
             "cron", _json.dumps({"day_of_week": "sun", "hour": 19, "minute": 20}), "run_industry_map_refresh"),
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
            # detect_market_ebb / detect_strength_ebb 已退役 (大盘退潮/强势退潮预警去除, v1.7.737)
            # v1.7.x: 推送机制 — 盘前「今日关注」摘要卡(第四批, 只取系统内现成数据不拉外部接口)
            ("morning_focus_0850", "盘前今日关注·08:50",
             "交易日 08:50 一张盘前情报卡: 持仓/昨日信号/今日披露 KPI三栏 + 昨日买点追踪(股票|模型|昨收涨跌) + 今日披露一句摘要(08:40披露日历卡照发) + 大盘风险档/止损压力/到线订阅当前状态; 全空不发, 每日一次DB去重",
             "cron", _json.dumps({"hour": 8, "minute": 50}), "run_morning_focus"),
            # v1.7.x: 推送机制 — 推送健康度周报(第四批; 无推送量日志表, 只统计 push_pref 用户动作并注明口径)
            ("push_health_weekly", "推送健康度周报·周五17:10",
             "每周五 17:10 统计本周(近5个交易日) cfzy_biz_push_pref 用户动作(静音/关模型/已处理/已卖出/到线订阅), 推一张系统灰卡: KPI三栏+动作分布+建议(被关最多的模型点名去模型图鉴); 数据不足一周也发并注明口径起始日",
             "cron", _json.dumps({"day_of_week": "fri", "hour": 17, "minute": 10}), "run_push_health_report"),
            # AI交易教练 Phase1 T5: 每周日22:30给交易者本人(user_id=1, 交割单所在)生成近一月复盘
            # (听模型对比/模型归因/盈亏周期/习惯), 单渠道全局推送只发一张卡, 无平仓不打扰
            ("trade_coach_weekly", "交易复盘·周日22:30",
             "每周日22:30给交易者生成近一月AI交易复盘(听模型对比/模型归因/盈亏周期/习惯)并推送, 无平仓不推",
             "cron", _json.dumps({"day_of_week": "sun", "hour": 22, "minute": 30}), "run_trade_coach_weekly"),
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
        for _jid in ("strength_quality_1430", "weak_extreme_1445"):
            try:
                await cur.execute(
                    "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s", (_jid,))
            except Exception:
                pass
        # v1.7.784: sector_next_day_predict 曾随 v1.7.554 并入 tail_decision_1440 而被停; 而 v1.7.651
        # 又删了 tail_decision_1440(那才是唯一真正落库次日预测的调用) → predict_data 自 2026-07-17 断更,
        # 面板「次日预测」空。重新启用其独立 14:30 定时(handler 已改「只落库不推送」, 尊重盘后推送精简)。
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET enabled = 1 WHERE job_id = %s",
                ("sector_next_day_predict",))
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

        # v1.7.651: 盘后推送精简(用户拍板) — 下线三张卡, 内容并入 19:00 晚盘复盘总结(review_summary):
        #   tail_decision_1440(14:40尾盘决策=真假强势+次日板块+弱势极限候选) / post_close_summary_1505(15:05盘后汇总) /
        #   disclosure_reminder(08:40早盘披露卡, 披露内容挪进晚盘复盘的「近期披露」段)。存量库删这三行(seed 已同步移除)。
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id IN (%s, %s, %s)",
                ("tail_decision_1440", "post_close_summary_1505", "disclosure_reminder"),
            )
            # 存量行改名(INSERT IGNORE 不更新已存在行, 故显式 UPDATE): 收盘复盘 → 晚盘复盘总结
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET name = %s, description = %s WHERE job_id = %s",
                ("晚盘复盘总结·19:00",
                 "晚7点一张晚盘复盘总结: 持仓今日表现 + 今日信号+近90天胜率+最好/警惕 + 近期财报披露(未来7天, 原08:40独立卡下线并入)",
                 "review_summary_2330"))
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
                ("attack_direction_0945", "资金进攻方向·09:45",
                 "开盘15分钟, 涨停扎堆题材(涨停池)+ 领涨行业(板块榜)双口径, 叠自选命中, 飞书卡推送",
                 "cron", _json.dumps({"hour": 9, "minute": 45}), "run_attack_direction_analysis"),
            )
            # v1.7.587: 该任务由 AI 版重构为确定性版, INSERT IGNORE 不改已存行, 补一条幂等 UPDATE 刷新标签
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET name=%s, description=%s "
                "WHERE job_id=%s",
                ("资金进攻方向·09:45",
                 "开盘15分钟, 涨停扎堆题材(涨停池)+ 领涨行业(板块榜)双口径, 叠自选命中, 飞书卡推送",
                 "attack_direction_0945"),
            )
        except Exception:
            pass

        # v1.7.599: 胜率重算切5分钟诚实口径 — 存量行改到每晚21:00(等20:00的5分钟追加完成), 刷新标签
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET schedule_config=%s, name=%s, description=%s "
                "WHERE job_id=%s",
                (_json.dumps({"hour": 21, "minute": 0}),
                 "模型胜率·每日重算(5分钟诚实口径)",
                 "每晚21:00(等20:00的5分钟K线追加完成后)按5分钟真实可成交口径重算全部买入模型 近3月/近6月 胜率+单笔均收益, 写 cfzy_biz_model_winrate, 供买入提醒带全市场回测战绩",
                 "model_winrate_refresh"),
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

        # v1.7.737: 大盘预警去除第一批 —— 自选池口径的市场风险实时检测(名实不符/无独立回测背书)、
        # 大盘退潮/强势退潮、大盘急跌 四个推送预警退役, handler 已从 TASK_HANDLERS 移除; 清存量库行。
        # (market_risk_eod / market_risk_intraday 暂留作全市场状态源, 喂顶栏风险灯+买点戳, 下一批再改造)
        try:
            await cur.execute(
                "DELETE FROM cfzy_sys_scheduled_tasks WHERE job_id IN (%s, %s, %s, %s)",
                ("market_risk_realtime", "detect_market_ebb", "detect_strength_ebb", "detect_plunge"),
            )
        except Exception:
            pass

        # v1.7.750: 藏龙岛观点采集节流改「交易日09:00-11:30/13:00-15:00每1分钟·其余3分钟」(原盘后10分钟),
        # 逻辑在 scanner 自节流, 这里只同步存量库的任务描述文案
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET description = %s WHERE job_id = %s",
                ("交易日09:00-11:30/13:00-15:00每1分钟, 其余(午休/盘后/周末)每3分钟拉飞书群群主(藏龙岛)消息入库(只存不推), 供藏龙岛观点页",
                 "lark_coach_scan"),
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
            ("lark_coach_scan", "藏龙岛观点跟踪",
             "交易日09:00-11:30/13:00-15:00每1分钟, 其余(午休/盘后/周末)每3分钟拉飞书群群主(藏龙岛)消息入库(只存不推), 供藏龙岛观点页", "interval",
             {"seconds": 60}, "scan_coach_posts"),
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
            ("model_winrate_refresh", "模型胜率·每日重算(5分钟诚实口径)",
             "每晚21:00(等20:00的5分钟K线追加完成后)按5分钟真实可成交口径重算全部买入模型 近3月/近6月 胜率+单笔均收益, 写 cfzy_biz_model_winrate, 供买入提醒带全市场回测战绩", "cron",
             {"hour": 21, "minute": 0}, "refresh_model_winrate"),
            # v1.7.599: 5分钟K线每日追加 — 胜率5分钟诚实口径的数据前提(表此前为一次性回填停在06-18)
            # v1.7.698: 系统体检 — 断言式定时校验(任务健康/数据新鲜度/外部接口/业务规则),
            # 每日 08:10(盘前, 早于所有交易日任务, 有问题当天还来得及处理)。结果先落
            # cfzy_sys_health_check 再推报告: 推送只是展示层, 推失败不丢数据、下轮补报。
            # 无论有无异常都推 —— "没消息"和"告警系统自己挂了"必须能区分, 报告里带推送心跳。
            ("health_report", "系统体检·每日08:10",
             "跑全部体检项(任务从未跑过/超期/连续失败、关键表数据新鲜度按交易日判、外部接口真拉一次校验内容、"
             "业务规则自洽), 结果落库并推一张体检报告卡; 带执行项数与推送心跳自检", "cron",
             {"hour": 8, "minute": 10}, "run_health_report"),
            # v1.7.692: 指数5分钟K线增量 — baostock 不支持指数分钟线(实测0根), 东财生产IP被封,
            # 故走新浪(实测服务器直连可用, datalen上限1023根≈21交易日滚动窗)。盘中每5分钟追加,
            # 幂等upsert(当前未走完的bar会被反复覆盖成最新值, 收盘自然定格)。非交易时段 TaskSkipped。
            ("index_kline_5m_append", "指数5分钟K线·盘中增量",
             "交易时段每5分钟从新浪拉上证/深成/创业板指的5分钟K线, 幂等upsert进 cfzy_sys_index_kline_5m; "
             "code带市场前缀(sh000001等)防与个股撞码; 非交易时段跳过", "interval",
             {"seconds": 300}, "append_index_kline_5m"),
            ("kline_5m_append", "5分钟K线·每日追加20:00",
             "每晚20:00用baostock逐票增量追加5分钟K线(后复权)到 cfzy_sys_kline_5m: 库内票续尾, 新入池票回补近一年; 幂等upsert, 当晚数据未出次日自动补齐", "cron",
             {"hour": 20, "minute": 0}, "append_kline_5m"),
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
            # v1.7.614: 模拟盘持仓守护 — 修「模拟盘只买不卖」的根因。卖点只对用户本人持仓下发
            # (scanner: 非持仓票只推买点), 模拟盘自己买的票一条卖点都收不到 → 仓位被亏损票占死。
            # 本任务用模拟盘自己的成本/建仓日/建仓买点独立跑卖点检测, 命中就在模拟盘内成交。
            ("paper_guard_tick", "模拟盘持仓守护·盘中",
             "盘中每60秒扫模拟账户(默认/无限子弹)自有持仓, 用各自的成本/建仓日/建仓买点跑一遍卖点检测"
             "(止盈/止损/跌破MA5·MA10·MA20/弱势极限左侧出场), 命中即在模拟盘内成交; "
             "不推送、不落信号库、不进模型胜率统计", "interval",
             {"seconds": 60}, "paper_guard_tick"),
            ("signal_eod_audit", "信号EOD自动复核",
             "每交易日17:00用收盘真实日线复核当日全部信号(K线序列指纹/触发价区间/涨跌家数自洽/指数波幅容纳急跌), "
             "数据层假象标记存疑(不自动删)并推送提醒, 写 cfzy_biz_signals.eod_audit", "cron",
             {"hour": 17, "minute": 0}, "signal_eod_audit"),
            # v1.7.x: 市场风险预警(替代空仓预警 v1.7.406). GREEN(正常)/YELLOW(谨慎)/RED(危险) 三态,
            # OOS 实测三档胜率单调递减(数字见 market_risk_controller docstring / 登记表)
            ("market_risk_eod", "市场风险·收盘评估16:40",
             "每交易日16:40从全市场日线(kline_cache)+新浪快照算涨跌比/广度/5日均收益/新低比/炸板率, 跑两级状态机"
             "(GREEN→YELLOW:广度<30%或涨跌比<30%或炸板>60%; YELLOW→RED:5日均收益<-1%或新低>15%或广度<15%), 状态迁移推送+落库 cfzy_biz_market_risk",
             "cron", {"hour": 16, "minute": 40}, "market_risk_eod"),
            ("market_risk_intraday", "市场风险·盘中预升级14:40",
             "尾盘14:40同口径估当日指标, 达RED进入条件提前升级推送(只升不降, 16:40收盘复核为准), 给尾盘买点打标",
             "cron", {"hour": 14, "minute": 40}, "market_risk_intraday"),
            # market_risk_realtime 已退役 (自选池口径大盘预警去除, v1.7.737)
            # v1.7.752 (Deploy 2B): 全市场口径盘中监测接棒 — 升档即时预警 + 过缓冲带才降档的退出机制
            ("market_risk_watch", "市场风险·盘中监测5分钟",
             "盘中(10:00-14:30)每5分钟读 market_overview 快照(全市场涨跌家数+三大指数涨跌幅), "
             "盘面恶化即时升档预警(正常→谨慎→危险), 明显转好过退出缓冲带才降档/解除(30分钟冷静期, 每日最多4条); "
             "档位最终以16:40收盘状态机为准",
             "interval", {"seconds": 300}, "market_risk_watch"),
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
            # v1.7.x: 均线到线提醒·一次性订阅扫描 — 个股买卖信号卡「🔔 到线提醒」链接订阅
            # (cfzy_biz_push_pref kind=ma_alert_10/20/60, target=code, 60天过期自动作废);
            # 交易时段闸在模块内自判(is_workday + 09:30~11:30/13:00~15:00)
            ("ma_touch_alert_scan", "均线到线提醒·盘中60秒",
             "交易时段每60秒扫全部生效的到线提醒订阅(推送卡一键订阅10/20/60日线), 现价进入对应均线±0.3%贴线带即推提醒卡并撤销订阅行(一次性); 订阅时已贴线须先离带再回触才算(防误触, 状态内存); 不落信号库",
             "interval", {"seconds": 60}, "run_ma_touch_alert"),
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


def _build_db_ssl(ssl_cfg):
    """H1(v1.7.654): config 驱动的库连接 TLS。**默认(config 无 database.ssl 键)返回 None → 不加密,
    与历史行为完全一致零改变**; 只有显式配置才启用, 故对现网零风险。

    ssl_cfg 形态(config.json 的 database.ssl):
      缺省/None/False/空   → None(明文, 现状)
      True                 → 加密但不校验服务器证书(check_hostname=False, CERT_NONE), 最省事
      {"ca": "/path.pem"}  → 用指定 CA 证书校验服务器身份(最严格)
      {"verify": false}    → 显式加密不校验

    火山 veDB 若在控制台开了 SSL: config 填 "ssl": true 即加密上线; 要证书校验则填 ca 路径。
    注意: 服务端若 have_ssl=DISABLED(未开 SSL), 即便这里传了 ctx, aiomysql 仍会静默退回明文
    —— 故 init_db 连上后会跑 _verify_db_encrypted 自检真实链路是否加密, 没加密就打醒目告警。
    """
    if not ssl_cfg:
        return None
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ca = ssl_cfg.get("ca") if isinstance(ssl_cfg, dict) else None
    # 有 ca 默认校验; 无 ca(仅 True 或 {}) 默认不校验(veDB 证书未必链到公网 CA)
    verify = ssl_cfg.get("verify", bool(ca)) if isinstance(ssl_cfg, dict) else False
    if ca:
        ctx.load_verify_locations(ca)
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
    return ctx


async def _verify_db_encrypted(conn):
    """H1 自检: config 要求了 TLS, 核实链路真加密了没(防服务端未开 SSL 时静默明文的假安全感)。"""
    try:
        async with conn.cursor() as cur:
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            row = await cur.fetchone()
        cipher = row[1] if row else ""
        if cipher:
            logger.info(f"[db] TLS 已生效, cipher={cipher}")
        else:
            logger.warning(
                "[db] config 配置了 database.ssl 但实际连接仍是明文(Ssl_cipher 为空) —— "
                "服务端很可能未开启 SSL(火山 veDB 需在控制台启用), 数据仍跨公网明文传输!"
            )
    except Exception as e:
        logger.warning(f"[db] TLS 状态自检失败(不影响连接): {e}")


async def init_db():
    global _pool
    cfg = load_config().get("database", {})
    ssl_ctx = _build_db_ssl(cfg.get("ssl"))
    pool_kwargs = dict(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 3306),
        user=cfg.get("user", "root"),
        password=cfg.get("password", ""),
        db=cfg.get("db", "trading"),
        charset="utf8mb4",
        autocommit=True,
        # 生产 DB 跨云(火山引擎 veDB), 单查询往返~44ms、建连接~280ms。
        # minsize 提到 5 常驻保温连接, 避免冷连接每次付 280ms 重连;
        # maxsize 提到 25 缓解前台接口 + 一堆高频后台任务(3s/30s/60s)争抢连接排队;
        # pool_recycle 1h 主动回收, 防远端 wait_timeout 静默掐断后拿到死连接。
        minsize=5,
        maxsize=25,
        pool_recycle=3600,
    )
    if ssl_ctx is not None:
        pool_kwargs["ssl"] = ssl_ctx   # H1: 仅当 config 显式配置 database.ssl 才传, 否则维持现状
    _pool = await aiomysql.create_pool(**pool_kwargs)
    async with _pool.acquire() as conn:
        if ssl_ctx is not None:
            await _verify_db_encrypted(conn)
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
