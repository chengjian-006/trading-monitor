"""涨跌停板幅 — 此前散在 4 处(各自 if code.startswith 链, 边界码归类还不一致),
统一到这里。采用最完整的规则集(原 stock_tag_refresher._limit_threshold), 顺带修正
689(科创CDR)/92(北交所)/301 等边界码在别处被误判的问题。
"""


def get_limit_pct(code: str, name: str = "") -> float:
    """该股涨跌停板幅(%): ST 5 / 科创·创业 20 / 北交所 30 / 主板 10。"""
    if "ST" in (name or "").upper():
        return 5.0
    c = str(code or "")
    c = c[2:] if c[:2].lower() in ("sh", "sz") else c
    if c.startswith(("688", "689", "300", "301")):
        return 20.0
    if c.startswith(("8", "4", "92")):
        return 30.0
    return 10.0


def is_new_listing(name: str) -> bool:
    """是否为「无涨跌幅限制」的次新股标识 —— 名称带 N(上市首日) / C(上市后 2~5 日)。

    放在这里而非各自实现: 与 get_limit_pct 是同一件事的两面 —— 这类票在标识期内
    根本没有涨跌停板, 任何"是否涨停"的判定对它们都无意义(首日可涨 100%+)。

    用途(v1.7.704): 资金回流·板块预警要求"龙头涨停"作为板块资金回流的证据, 但新股
    首日暴涨反映的是打新情绪、不是板块资金回流, 且对它"涨停"这个条件天然恒真 ——
    等于任何板块只要当天有新股上市就可能被误判。实测近一月 25 条里有 3 条(12%)
    龙头是 N 开头新股。
    """
    n = (name or "").upper().strip()
    return n.startswith("N") or n.startswith("C")


def is_at_limit_up(code: str, pct: float, name: str = "", tol: float = 0.15) -> bool:
    """当前涨幅是否已到涨停(允许 tol 容差, 覆盖 9.98%/19.97% 等四舍五入)。"""
    if not code:
        return False
    return pct >= get_limit_pct(code, name) - tol
