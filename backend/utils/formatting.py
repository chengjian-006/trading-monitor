"""通用格式化 — 此前各 service 各抄一份(_fmt_amount/_fmt_pct), 统一到这里。"""


def fmt_amount(value: float) -> str:
    """成交额/市值格式化为人类可读: 亿 / 万 / 元。0 或无效 → '-'。"""
    if not value or value <= 0:
        return "-"
    if value >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if value >= 1e4:
        return f"{value / 1e4:.0f}万"
    return f"{int(value)}元"


def fmt_pct(value, decimals: int = 2) -> str:
    """百分比格式化, 带正负号。非数字 → '-'。"""
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:+.{decimals}f}%"
