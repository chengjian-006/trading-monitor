"""信号引擎默认配置 + 用户覆盖合并 - v1.7.x.

DEFAULT_SIGNAL_CONFIG:    每个 signal_id 的参数默认值, 调用方按需 deep-merge 用户 override.
EXTRA_FILTERS:            通用阈值过滤(RSI/量比/涨幅), 任何信号都可在 user_config 里加.
get_merged_config:        deepcopy 默认 + 用户 override 单点写入, 不污染共享状态.
"""
import copy


EXTRA_FILTERS = {
    "filter_rsi_min":        {"indicator": "rsi",         "op": ">="},
    "filter_rsi_max":        {"indicator": "rsi",         "op": "<="},
    "filter_vol_ratio_min":  {"indicator": "vol_ratio_5", "op": ">="},
    "filter_vol_ratio_max":  {"indicator": "vol_ratio_5", "op": "<="},
    "filter_pct_change_min": {"indicator": "pct_change",  "op": ">=", "scale": 0.01},
    "filter_pct_change_max": {"indicator": "pct_change",  "op": "<=", "scale": 0.01},
}


DEFAULT_SIGNAL_CONFIG: dict = {
    "BUY_WEAK_EXTREME": {
        # 弱势极限(MA10 ∪ MA20 锚点) v1.7.79: MA10/MA20 合并为一个信号, 任一锚点贴近即可命中
        #   主升浪回踩: 前面有过涨幅≥15%的主升浪, 且峰值距今≤30个交易日 (v1.7.189: 15→30, 加量且提质)
        #   绝对地量:   今日量 ≤ 近10日最低量 × 1.1 (v1.7.247: 1.0→1.1 放宽; 回测证明绝对地量是数量阀门非质量阀门, 与相对缩量冗余, 放宽多约1/3信号而期望/盈利因子不变)
        #   相对缩量:   今日量 ≤ 近10日均量 × 0.70 (v1.7.189: 0.80→0.70 收紧; 缩量越深反弹越可靠)
        #   长期未破:   close > MA60
        #   中期未破:   close > MA20
        #   贴近锚点:   close 距 MA10 ∈ [-2%, +2%]  OR  close 距 MA20 ∈ [-2%, +2%]
        #   前置确认:   v1.7.150 — 要求 T-1..T-N 也满足弱势极限, 排除"昙花一现的孤日缩量"
        # 输出 detail 中标注命中的锚点(MA10/MA20)及距最近一根均线的位置信息
        "enabled": True,
        "require_prior_rally": True,
        "rally_peak_within_bars": 30,    # v1.7.189: 15→30 (回测显示放宽窗口加量且提质, 30后到顶)
        "intraday_earliest_minute": 600,
        "vol_floor_window": 10,
        "vol_floor_tolerance": 1.1,        # v1.7.247: 1.0→1.1 放宽 (回测: 绝对地量是数量阀门非质量阀门)
        "vol_shrink_avg10_ratio": 0.70,    # v1.7.189: 0.80→0.70 (相对缩量收紧; 扫描显示缩量越深胜率越高)
        "ma10_above_max_pct": 2.0,
        "ma10_below_max_pct": 2.0,
        "ma20_above_max_pct": 2.0,
        "ma20_below_max_pct": 2.0,
        "prior_weak_days_required": 1,    # v1.7.155: 2→1 (回测 30 天 N=2 仅 1 笔, N=1 6 笔合理)
    },
    "BUY_STRONG_START": {
        # 强势起点（右侧）v1.7.89: 左侧 S0 缩量地量后, 今日放量站上均线
        "enabled": True,
        # v1.7.417: 去掉10:00时间门槛(600→0)。早盘外推系数极小(9:31≈0.009 → 1分钟量×100倒推全天),
        #   量/额(amount_est)门槛形同虚设, 故原靠时间闸门挡开盘噪音。改为用"实时累计成交额(amount_now,
        #   未外推)≥5亿"作流动性硬闸门(见 min_amount_now)替代时间限制: 真实已成交够 5亿即可触发, 不必干等10点。
        #   预估全天额(min_full_day_amount)保留, 二者并存——前者=已发生的真实成交, 后者=外推全天流动性。
        "intraday_earliest_minute": 0,
        "lookback_days": 5,
        "vol_multiplier": 2.0,        # v1.7.184: 3.0→2.0 (主力杠杆; 漏斗诊断证明 3x 是 0 触发主瓶颈)
        "vol_avg_window": 10,         # v1.7.179: 绝对量门槛回看天数 N
        "min_vol_vs_avgN": 1.5,       # v1.7.179: 今日量 ≥ 近N日均量 × k (0=关闭; v1.7.184 诊断证明此门槛从不生效, 暂留)
        "min_full_day_amount": 1_000_000_000,  # v1.7.184: 20亿→10亿 (放小中盘进来) — 外推全天额
        "min_amount_now": 500_000_000,  # v1.7.417: 实时累计成交额(未外推)≥5亿, 替代10点时间门槛防早盘伪起爆 (0=关闭)
        "max_gain_from_base_pct": 10.0,  # v1.7.420: 现价距弱势极限基准日收盘涨幅>10%不报, 挡已大涨的晚到追高 (0=关闭)
        "min_pct_change": 2.0,
    },
    "BUY_RALLY_MA20": {
        # 主升浪回踩20MA缩量后突破昨高·缩量后突破昨高 (v1.7.x 新增右侧买点, 补弱势极限抓不到的急跌/高量回踩)
        #   昨日: 主升浪(峰值≤30日内) + 回踩20日线(±3%) + 缩量(<近10日均量×0.8) + 近10日均额>20亿
        #   今日: 盘中最高 突破昨高×(1+2.5%) → 买点 (2.5% 过滤假突破; 配置页可调)
        # 回测(自选股近1年): 触发48 胜率46% 胜负比1.9:1 平均+6.9%(T+5), 抓到多氟多
        # v1.7.463: 流动性闸门从"实时累计成交额≥10亿"改为"放量确认"双闸 (同回踩MA10 v1.7.462):
        #   旧闸门要干等成交额堆够10亿, 小盘急拉票等到时价格已飞;
        #   新双闸 ① 当日量≥近10日均量×vol_mult_avg10(放量确认真突破, 盘中由U型系数外推全天量, 回测=全天量, 口径一致)
        #         ② min_full_day_amount=5亿 作累计额底线(防纯地量空壳)。
        #   全市场回测(2025-06~2026-05): 旧10亿闸 928笔/胜率58.9%/盈利因子1.99 → 双闸量≥1.5× 660笔/69.4%/3.49,
        #   样本外 60.6%/2.05 → 67.6%/3.04(非过拟合); 边际票(5亿~10亿,旧闸误挡)加放量后70.3%/3.59 反优于基线。
        "enabled": True,
        "intraday_earliest_minute": 0,
        "require_prior_rally": True,
        "rally_peak_within_bars": 30,         # 主升浪峰值距今(到昨日) ≤ N 交易日
        "ma20_touch_pct": 3.0,                # 昨日 close 距 MA20 在 ±% 内 = 回踩
        "shrink_ratio": 0.8,                  # 昨日量 < 近10日均量 × 此值 = 缩量(质量关键)
        "amount_avg_window": 10,              # 成交额/量 均值回看天数(用于昨日缩量判定)
        "min_full_day_amount": 500_000_000,   # v1.7.463: 10亿→5亿, 仅作累计额底线(放量确认是主闸门)
        "vol_mult_avg10": 1.5,                # v1.7.463: 当日(盘中外推全天)量 ≥ 近10日均量 ×1.5 = 放量确认
        "breakout_pct": 2.5,                  # 今日突破昨高 > 此% 才买(过滤假突破; 2/2.5/3 可调)
    },
    "BUY_RALLY_MA10": {
        # 回踩10MA缩量后突破昨高(右侧) v1.7.x: 同回踩20MA缩量后突破昨高, 但回踩锚点改MA10、容差收紧±1%(贴近MA10), 卖出剩半跟踪MA10×0.98
        #   昨日: 主升浪(峰值≤30日) + 回踩10日线(±1%) + 缩量(<近10日均量×0.8)
        #   今日: 盘中最高突破昨高×(1+2.5%) + 放量确认(当日量≥近10日均量×1.5) + 累计成交额≥5亿底线 → 买点
        # 全市场半年回测: 旧"累计额≥10亿"闸门 612笔 胜率57% 盈利因子1.92
        # v1.7.462: 流动性闸门改"放量确认"双闸 → 样本外 59%→70% / 盈利因子1.88→2.90, 且突破即报不再干等成交额堆够
        "enabled": True,
        # v1.7.462: 流动性闸门从"实时累计成交额≥10亿"改为"放量确认"双闸 ——
        #   旧闸门要干等成交额堆够10亿, 小盘急拉票等到时价格已飞(东方国信0618: 10:03突破时才4.5亿, 等到10亿已10:24/+10.7%);
        #   新双闸 ① 当日量≥近10日均量×vol_mult_avg10(放量确认真突破, 盘中由U型系数外推全天量, 回测=全天量, 口径一致)
        #         ② min_full_day_amount=5亿 作累计额底线(防纯地量空壳)。
        #   回测B2(量≥1.5×): 东方国信改在 10:04/+5.3% 即报, 提前20分钟少追5.4个点, 且样本外胜率/盈利因子双升。
        "intraday_earliest_minute": 0,
        "require_prior_rally": True,
        "rally_peak_within_bars": 30,
        "touch_ma": "ma10",                   # 回踩锚点用MA10
        "ma20_touch_pct": 1.0,                # 回踩容差±1%(贴近MA10)
        "shrink_ratio": 0.8,
        "amount_avg_window": 10,
        "min_full_day_amount": 500_000_000,   # v1.7.462: 10亿→5亿, 仅作累计额底线(放量确认是主闸门)
        "vol_mult_avg10": 1.5,                # v1.7.462: 当日(盘中外推全天)量 ≥ 近10日均量 ×1.5 = 放量确认
        "breakout_pct": 2.5,
    },
    "BUY_VOL_BREAKOUT": {
        # 缩量突破昨高 (v1.7.248 新右侧买点): 昨日缩量整理 → 今日放量突破昨高, 不锚定均线、不要主升浪前置
        #   昨日: 昨量 < 近10日均量(截至昨日) × 0.8 (缩量整理)
        #   今日: 量 ≥ 2×昨量 且 ≥ 1.5×近10日均 + 最高突破昨高×(1+2%) + 收盘站上MA10/MA20 + 成交额≥10亿
        # 全市场半年双段回测(入场/出场均网格寻优): 胜率65% 均值+3.1% 盈利因子2.24(样本内2.19/外2.29)
        # 本质=1日微型平台突破; 与回踩同源但不锚均线、不要主升浪, 故触发更广。出场建议右侧快出(+7%卖半/破MA10/-6%/T10)
        "enabled": True,
        # v1.7.428: 去掉10:00时间门槛(600→0), 流动性改双闸(去时间窗后挡早盘U型外推伪突破):
        #   ①外推全天额 amount_est ≥ 10亿(min_full_day_amount)
        #   ②实时累计额 amount_now ≥ 5亿(min_amount_now, 未外推真实成交)
        #   放量条件本就用真实累计量(≥2×昨量), 早盘几分钟攒不出, 与②共同挡住开盘噪音。
        # v1.7.430: 外推额门槛曾试调20亿, 半年全市场回测证明明显拖累(2.0/1.5参数下笔数301→124、
        #   单笔净收益+3.26%→+1.63%、盈利因子2.53→1.60; 10-20亿中小盘突破是优质票源被误挡), 改回10亿。
        #   早盘伪突破由②实时累计额≥5亿(真实成交,非外推)兜底, 无需靠抬高外推额门槛。
        "intraday_earliest_minute": 0,
        "shrink_ratio": 0.8,                   # 昨量 < 近10日均量 × 此值 (缩量整理; 寻优固定)
        "vol_mult_prev": 2.0,                  # 今日量 ≥ 昨量 × 此值 (回测: 1.5~3.0胜率66~68%差异小)
        "vol_mult_avg10": 1.5,                 # 今日量 ≥ 近10日均量 × 此值 (质量主杠杆; 1.5是数量×质量甜点, 2.0更精但信号腰斩)
        "breakout_pct": 2.0,                   # 今日最高突破昨高 > 此% (2.0 优于 2.5/3.0)
        "min_full_day_amount": 1_000_000_000,  # 外推全天成交额 ≥ 10亿 (v1.7.430 回测确认优于20亿)
        "min_amount_now": 500_000_000,         # v1.7.428: 实时累计成交额(未外推) ≥ 5亿, 配合去时间窗防早盘伪突破 (0=关闭; 回测无此字段回退amount_est自动满足)
        "REQ_PREV_SHADOW": True,               # v1.7.336 新增: 要求缩量日有长下影线(盘中砸下被买回=下方承接)
        "PREV_SHADOW_MIN": 0.4,                # 缩量日下影线/当日振幅 ≥ 此值 (≥0.4 是甜点: 半年回测 PF2.33→2.87/胜率66→70%/内外均70%; ≥0.5/0.6 过严稀释)
        "zt_setup_skip": True,                 # v1.7.519: 排除"封板假缩量" — 缩量设置日(昨日)若是涨停封板, 低换手系封死所致非整理蓄势, 次日突破=涨停后加速段非休整启动, 不触发(实例: 株冶集团06-22高开秒板缩量0.43倍被误判)
        "zt_setup_pct_min": 9.5,               # 昨日认定为涨停封板的涨幅阈值(%): 收盘≈最高 且 涨幅≥此值 (板无关近似, 覆盖主板10%/创业科创20%/北交所30%封板; ST5%封板漏掉可接受)
        "chase_limit_skip": True,              # v1.7.520: 排除"触发侧追涨停" — 现价已逼近今日涨停板时不发买点, 防炸板高位接盘(实例: 洪田股份603800 06-25 09:44冲涨停84.95=昨收×1.10被误发)
        "chase_limit_buffer_pct": 1.0,         # 现价距涨停板 ≤ 此%(板幅感知: 主板10%/创业科创20%/北交所30%/ST5%) 视为接近涨停不触发; 回测无code自动跳过此闸
    },
    "BUY_PLATFORM_BREAKOUT": {
        # 中继平台突破 (v1.7.323 新右侧买点): 多日横盘窄平台 → 收盘突破上沿; 与缩量突破(本质=1日微型平台)区分
        #   平台: 今日前 L=12 日横盘, 收盘振幅≤15%; 中继前置: 平台前20日内主升≥20%
        #   今日: 收盘价 ≥ 平台上沿×(1+0.5%) + 放量≥平台均量×1.2 + 全天成交额≥10亿
        # 全市场半年回测(出场同回踩10MA缩量后突破昨高): L8主配置胜率69%/均值+4.3%/盈利因子3.08(样本外2.85/2026切片3.02);
        #   L12盈利因子升到~3.3, 自选池2026实测326笔/胜率74%/均值+5.4%/盈利因子4.03 → 默认采用L12(质量全面优于L8, 笔数仍够)
        # 硬约束: ①必须收盘确认(改盘中口径PF塌到1.72)→默认尾盘14:45门槛; ②顺势动量, 退潮/分化月走平, 靠引擎层regime闸门自动降级停发
        "enabled": True,
        "intraday_earliest_minute": 880,       # 14:40 尾盘 (收盘确认近似: 此时现价≈收盘价; 较14:45早5分钟留更多操作时间, 代价是离真收盘略远一丝)
        "L": 12,                               # 平台长度(交易日; 默认12=自选池实测胜率74%/盈利因子4.03优于8; L6/8/12/15寻优PF2.99/3.08/3.29/3.39, 越长越精越少)
        "A": 0.15,                             # 平台收盘振幅上限 (≤10/15/20% 寻优PF3.13/3.08/3.00)
        "N_PRIOR": 20,                         # 中继前置回看窗
        "R": 0.20,                             # 中继前置最小涨幅 (可选过滤, 提质降量: 去掉PF仍2.98但样本翻倍均值降)
        "REQ_PRIOR": True,                     # 要求中继前置主升
        "REQ_RISE": True,                      # v1.7.325 新增: 要求平台"缓升台阶"形态(后半收盘中位/前半∈[RISE_MIN,RISE_MAX]), 剔走平/下倾/陡冲
        "RISE_MIN": 0.0,                       # 中位上行下限(必须不下倾)
        "RISE_MAX": 0.05,                      # 中位上行上限(小幅缓升, 收窄到5%是甜点; 半年回测单加此项 PF3.29→3.34、样本外胜率69→71%; 放宽到8%稀释无效)
        "BUF": 0.005,                          # 突破上沿缓冲
        "MODE": "close",                       # 突破口径: close=收盘确认(必须) / high=盘中(PF塌到1.72, 勿用)
        "V": 1.2,                              # 突破日放量 ≥ 平台均量 × 此值 (=回测主配置; 1.0/1.2/1.5 寻优PF3.03/3.08/3.24, 影响很小; 太极6/2突破仅1.29x, 设1.3会漏掉它)
        "REQ_VOL": True,                       # 要求突破放量
        "REQ_HOLD": False,                     # 要求平台不破MA20 (可选)
        "min_full_day_amount": 1_000_000_000,  # 今日全天成交额 ≥ 10亿
    },
    "BUY_AUCTION_STRENGTH": {
        # 竞价高开弱转强 (v1.7.275): 强势缩量回调次日竞价高开 + 大盘极端情绪 + 个股竞价额过亿
        #   昨日: 收>MA20>MA60 + 近20日涨幅≥15% + 缩量(≤近10日均量×0.8)且昨涨幅∈[-5%,+1%]且收>MA10
        #   今日: 竞价高开∈[3%, 9%] (9:26触发); 情绪门控(引擎层): 红盘≥3500(热)或绿盘≥3500(冰点), 剔中性;
        #         竞价额门槛(引擎层): 个股集合竞价成交额 ≥ 5000万 (0614从1亿下调, 提信号量)
        # 注: 形态(强势弱转强S)回测净+2.80%/盈利因子1.98; 但"涨跌家数门控"与"竞价额≥5000万"两道为新增、未回测, 上线后向前验。
        "enabled": True,
        "intraday_earliest_minute": 566,    # 9:26 (集合竞价撮合后, 连续竞价9:30前)
        "rally_min_pct": 15.0,              # 近20日主升浪幅度下限
        "amount_avg_window": 10,            # 缩量判定的均量回看天数
        "shrink_ratio": 0.8,               # 昨量 ≤ 近10日均量 × 此值 = 缩量
        "min_t1_ret": -5.0,                # 昨涨幅下限(%)
        "max_t1_ret": 1.0,                 # 昨涨幅上限(%) — 小回调
        "gap_min_pct": 3.0,                # 今竞价高开下限(%)
        "gap_max_pct": 9.0,                # 今竞价高开上限(%) — 排除接近涨停的一字/不可买
        "breadth_extreme": 3500,           # 涨/跌家数 ≥ 此值 = 极端情绪(热/冰点)放行, 否则中性剔除
        "min_auction_amount": 50_000_000,  # 个股集合竞价成交额下限(元) = 5000万
    },
    # S1_BUY / S2_BUY / S3_BUY / S4_BUY 已下线 v1.7.90 (右侧统一交给 BUY_STRONG_START)
    # _detect_s3_rally_pullback 保留, 仅供策略回测页历史分析使用
    # SS1/SS2/SS3: 持仓股盘中跌破 MA5/MA10/MA20 信号
    #   emit_all (读 SELL_BREAK_MA5): False=同时触发只推最深破位(最低支撑均线); True=全推
    # confirm_after_minute=870: MA5破位只在工作日14:30后判(尾盘确认, 早盘/午休噪音大) v1.7.403
    "SELL_BREAK_MA5": {"enabled": True, "anchor": "ma5",  "break_pct": 2.0, "emit_all": False,
                       "confirm_after_minute": 870},
    "SELL_BREAK_MA10": {"enabled": True, "anchor": "ma10", "break_pct": 2.0},
    "SELL_BREAK_MA20": {"enabled": True, "anchor": "ma20", "break_pct": 2.0},
    "SELL_TAKE_PROFIT": {
        "enabled": True,
        "target_pct": 7.0,
    },
    # v1.7.x 主动止盈/止损体系 (默认关, 配置页可逐个开)
    "SELL_TRAIL_STOP": {
        "enabled": False,
        "min_gain_pct": 5.0,
        "drawdown_pct": 7.0,
    },
    "SELL_RR_TARGET": {
        "enabled": False,
        "stop_loss_pct": 5.0,
        "target_r": 2.0,
    },
    "SELL_TIME_STOP": {
        "enabled": False,
        "min_days": 5,
        "flat_threshold_pct": 3.0,
    },
    # 弱势极限 左侧差异化出场 (v1.7.x): 仅对"弱势极限建仓"的持仓生效, 触发时静音全部右侧卖点.
    #   回测 N≈2200 真检测器重扫+样本内外: 纯持有最优(右侧快出砍2/3利润), 最近半年样本内 T+15 见顶,
    #   -12% 硬止损单位保护成本最低. 建仓来源由 get_holdings_entry_model 就近匹配信号历史归因.
    "SELL_WEAK_STOP": {
        "enabled": True,
        "threshold_pct": 12.0,    # 浮亏 ≥ 此% → 清仓 (左侧硬止损; 回测 -12%>-10%)
        "confirm_persist_sec": 300,  # v1.7.421: 连续碰线满此秒才推, 防早盘插针误触 (0=立即推)
        "skip_on_up_day": True,      # v1.7.422: 现价≥昨收(当日上涨/平盘)不报止损, 持仓回血中不催卖 (False=任何时刻碰线即报)
    },
    "SELL_WEAK_TIME": {
        "enabled": True,
        "hold_days": 15,          # 持有满 T+此值 交易日 → 清仓 (封顶日; 样本内 T+15 见顶)
    },
    # 持仓警戒线 (v1.7.402: 只保留 -10% 一档, -5/-8 两档已按用户要求下线 — 检测器不再读它们;
    #   v1.7.x: 已下线的 SELL_LOSS_5 / SELL_LOSS_8 死配置项移除)
    "SELL_LOSS_10": {"enabled": True, "threshold_pct": 10.0, "confirm_persist_sec": 300, "skip_on_up_day": True},  # v1.7.421 确认延迟 + v1.7.422 上涨日不报(现价≥昨收不催卖)
    # M1_BUY / M2_BUY / MS1_SELL / MS2_SELL 已下线 v1.7.90 (中线信号体系撤除)
    # 市场风险两级预警 (替代已下线的空仓预警 CASH_ALERT). GREEN/YELLOW/RED 三态.
    # 阈值依据与状态机细节见 services/market_risk_controller.py. 回测在 bt_risk_state_machine.py.
    "MARKET_RISK": {"enabled": True,
                    "yellow_enter_breadth": 30.0, "yellow_enter_advance": 30.0,
                    "yellow_enter_zha": 60.0, "red_enter_avg5": -1.0,
                    "red_enter_low52": 15.0, "red_enter_breadth": 15.0,
                    "yellow_exit_breadth": 38.0, "yellow_exit_advance": 42.0,
                    "red_exit_breadth": 25.0, "red_exit_advance": 40.0},
    "PLUNGE_INDEX": {
        "enabled": True,
        "time_window_min": 10,
        "drop_threshold_pct": 1.0,
    },
    "PLUNGE_BREADTH": {
        "enabled": True,
        "down_up_ratio": 3.0,
        "drop_gt3_pct": 25.0,
    },
    "PLUNGE_SPEED": {
        "enabled": True,
        "time_window_min": 5,
        "new_limit_down": 8,
    },
    "MAIN_RALLY": {
        # 主升浪量化定义(供 trading_concepts.detect_main_rally 使用)
        "enabled": True,
        "lookback_n": 30,
        "breakout_vol_mult": 1.2,
        "min_gain_pct": 15.0,
        "max_drawdown_pct": 8.0,
    },
    "SECTOR_CAPITAL_INFLOW": {
        # 资金回流·板块预警 (v1.7.19): 板块强势 + 龙头真涨停 + 板块内强势密度
        "enabled": True,
        "min_sector_pct": 1.0,
        "leader_limit_up_pct": 9.5,
        "sector_top_n_stocks": 10,
        "min_sector_top_avg_pct": 4.0,
    },
    "SCORE_STRENGTH": {
        # 真假强势评分 v2 (9 维度评分, 弱市维度 G/H/I 提升弱市识别)
        "enabled": True,
        "min_persist_days": 3,
        "healthy_vol_min": 0.5,
        "healthy_vol_max": 0.8,
        "extreme_low_vol": 0.3,
        "big_buy_min_count": 2,
        "big_net_outflow_warn": 10000000,
        "long_persist_days": 5,
        "real_strong_threshold": 65,
        "observe_threshold": 40,
        # 弱市维度 v2
        "counter_trend_proximity": 0.005,
        "relative_strong_pct": 5.0,
        "relative_medium_pct": 3.0,
        "sector_rank_top_strong": 3,
        "sector_rank_top_medium": 10,
    },
    "SCORE_THEME": {
        # 主流题材 阶段 1 极简版: ① 板块涨 ≥3% ② 龙头涨 ≥7% ③ 板块涨幅榜前 5
        "enabled": True,
        "min_sector_pct": 3.0,
        "min_leader_pct": 7.0,
        "max_rank": 5,
    },
}


def get_merged_config(user_config: dict | None) -> dict:
    """deepcopy 默认 + 用户 override key-wise update. 不会污染 DEFAULT_SIGNAL_CONFIG."""
    cfg = copy.deepcopy(DEFAULT_SIGNAL_CONFIG)
    if user_config:
        for sig_id, params in user_config.items():
            if not isinstance(params, dict):
                continue
            if sig_id in cfg:
                cfg[sig_id].update(params)
            else:
                cfg[sig_id] = params
    return cfg
