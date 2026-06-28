"""复现/回归测试: _QUOTABLE 里的字面量 % 会让 pymysql 的 `query % args` 抛
ValueError: unsupported format character (self_heal_quotes / data_sanity 连续失败的根因)。

不连库、不需 pymysql —— 直接复现 pymysql/aiomysql 内部 execute 前做的 `query % escaped_args`。
旧值 (LIKE '88%') 应 FAIL 并报 index 113 处的 ''' ; 修好后应 PASS。
"""
import ast
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "models" / "repo" / "stocks.py"

tree = ast.parse(SRC.read_text(encoding="utf-8"))
QUOTABLE = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_QUOTABLE":
                QUOTABLE = ast.literal_eval(node.value)
assert QUOTABLE is not None, "源码里找不到 _QUOTABLE"

# 按 stocks.py 原样重建两条"带参数"的 SQL
q_stale = (f"SELECT code FROM cfzy_biz_stock_pool WHERE {QUOTABLE} "
           "AND (quote_updated_at IS NULL OR quote_updated_at < NOW() - INTERVAL %s SECOND) "
           "GROUP BY code LIMIT %s")
q_health = ("SELECT COUNT(*) AS total, "
            "SUM(quote_updated_at IS NULL OR quote_updated_at < NOW() - INTERVAL %s SECOND) AS stale, "
            "SUM(price IS NULL OR price = 0) AS null_price "
            f"FROM (SELECT code, MAX(quote_updated_at) AS quote_updated_at, MAX(price) AS price "
            f"FROM cfzy_biz_stock_pool WHERE {QUOTABLE} GROUP BY code) t")

cases = [("get_stale_quote_codes", q_stale, (150, 80)),
         ("count_quote_health", q_health, (360,))]

failures = []
for name, q, params in cases:
    try:
        q % params          # pymysql/aiomysql 内部正是这一步
    except (ValueError, TypeError) as e:
        # %' 在参数前 → ValueError 非法格式符; 在参数后 → TypeError 参数不足. 两者都=查询坏了
        failures.append(f"{name}: {type(e).__name__}: {e}")

if failures:
    print("FAIL — pymysql %-格式化会抛异常:")
    for f in failures:
        print("   ", f)
    sys.exit(1)
print("PASS — 两条带参 SQL 都能干净格式化")
