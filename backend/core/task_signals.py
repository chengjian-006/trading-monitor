"""调度任务的控制信号 (中性模块, 无第三方依赖, 供 task_registry 与各 handler 共用, 避免循环导入)。"""


class TaskSkipped(Exception):
    """Handler 主动跳过本次执行 (如非交易日), 属"没到该干活的时候", 不是失败。

    wrapped_handler 收到它: 只更新 last_run_at + last_status='skipped',
    绝不清零 consecutive_failures / 抹掉 last_error_msg —— 防止周末空跑把
    工作日的真实失败掩盖掉 (v1.7.x 胜率静默停写事故的观测根因之一)。
    """
