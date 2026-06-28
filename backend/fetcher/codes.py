"""股票/指数代码与各数据源 secid/symbol 之间的转换 - v1.7.x.

各数据源 secid 风格:
  EastMoney: '1.000001' (1=上证, 0=深证, 沪深通用)
  Sina:      'sh000001'
  THS:       'hs_000001'

所有 fetcher 子模块只能用这里定义的 4 个函数, 不要 inline 复制.
"""


def _code_to_sina(code: str) -> str:
    code = code.strip().zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _normalize_code(code: str) -> str:
    """剥去 sh/sz 前缀返回 6 位代码."""
    code = code.strip()
    if code.startswith(("sh", "sz", "SH", "SZ")):
        code = code[2:]
    return code.zfill(6)


def _code_to_em(code: str) -> str:
    code = code.strip().zfill(6)
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _code_to_ths(code: str) -> str:
    return f"hs_{code.strip().zfill(6)}"
