"""股票池批量 UPDATE 单往返化测试 (backend/models/repo/stocks.py).

背景: 生产 DB 跨云 44ms 往返; aiomysql 的 executemany 只对 INSERT...VALUES 合并多值,
UPDATE 会退化为逐条 execute → N 行 = N 次往返 (80 只/块 ≈ 3.5s 纯往返).
四个批量函数改为单条 `UPDATE ... SET col = CASE code WHEN %s THEN %s ... END WHERE code IN (...)`,
每块(<=500 行)一次往返, 纯更新语义绝不插新行.

测试用 AsyncMock 替换模块内 _execute (stocks.py 经 _db._execute 走连接池拿 cursor.execute),
断言: 生成 SQL 形状 / 全参数化(值不进 SQL 文本) / 往返(=execute 调用)次数 / 参数顺序.
"""
from unittest.mock import AsyncMock

import pytest

from backend.models.repo import stocks


@pytest.fixture
def mock_execute(monkeypatch):
    m = AsyncMock()
    monkeypatch.setattr(stocks, "_execute", m)
    return m


def _placeholders_match(sql: str, params) -> bool:
    return sql.count("%s") == len(params)


# ---------------------------------------------------------------- core quotes

class TestBatchUpdateCoreQuotes:
    async def test_empty_input_no_roundtrip(self, mock_execute):
        await stocks.batch_update_core_quotes([])
        mock_execute.assert_not_awaited()

    async def test_single_row_single_roundtrip(self, mock_execute):
        await stocks.batch_update_core_quotes(
            [{"code": "600519", "price": 1700.5, "pct_change": 2.1, "amount": 5.2e9}])
        assert mock_execute.await_count == 1
        sql, params = mock_execute.await_args.args
        # 单条 UPDATE, 不是 executemany 逐条
        assert sql.upper().startswith("UPDATE CFZY_BIZ_STOCK_POOL SET")
        assert "CASE code WHEN %s THEN %s END" in sql
        assert "WHERE code IN (%s)" in sql
        assert "quote_updated_at=NOW()" in sql
        # 全参数化: 值绝不拼进 SQL 文本
        assert "600519" not in sql and "1700.5" not in sql
        assert _placeholders_match(sql, params)
        # 参数顺序 = SET 子句出现顺序(price/pct_change/amount 各 (code,val)), 最后 WHERE IN codes
        assert list(params) == ["600519", 1700.5, "600519", 2.1, "600519", 5.2e9, "600519"]

    async def test_multi_rows_still_single_roundtrip(self, mock_execute):
        updates = [{"code": f"60000{i}", "price": 10.0 + i, "pct_change": float(i),
                    "amount": 1e8 * (i + 1)} for i in range(3)]
        await stocks.batch_update_core_quotes(updates)
        assert mock_execute.await_count == 1  # 改前: executemany 退化 = 3 次往返
        sql, params = mock_execute.await_args.args
        assert sql.count("WHEN %s THEN %s") == 3 * 3  # 3 列 x 3 行
        assert "WHERE code IN (%s,%s,%s)" in sql
        assert _placeholders_match(sql, params)
        # price 列 CASE 参数在最前, WHERE IN 的 codes 在最后
        assert list(params[:6]) == ["600000", 10.0, "600001", 11.0, "600002", 12.0]
        assert list(params[-3:]) == ["600000", "600001", "600002"]

    async def test_chunking_over_500(self, mock_execute):
        updates = [{"code": f"{i:06d}", "price": 1.0, "pct_change": 0.0, "amount": 0.0}
                   for i in range(501)]
        await stocks.batch_update_core_quotes(updates)
        assert mock_execute.await_count == 2  # 500 + 1
        sql1, params1 = mock_execute.await_args_list[0].args
        sql2, params2 = mock_execute.await_args_list[1].args
        assert sql1.count("WHEN %s THEN %s") == 3 * 500
        assert sql2.count("WHEN %s THEN %s") == 3 * 1
        assert _placeholders_match(sql1, params1) and _placeholders_match(sql2, params2)


# ---------------------------------------------------------------- full quotes

class TestBatchUpdateQuotes:
    async def test_empty_input_no_roundtrip(self, mock_execute):
        await stocks.batch_update_quotes([])
        mock_execute.assert_not_awaited()

    async def test_single_row_sql_shape_and_params(self, mock_execute):
        u = {"code": "000001", "price": 12.3, "pct_change": -1.2, "amount": 3.4e8,
             "speed": 0.5, "industry": "银行", "volume_ratio": 1.8, "free_cap": 2.1e11,
             "turnover": 0.9, "popularity_rank": 66, "ma20": 12.0, "ma10": 12.1, "ma60": 11.5}
        await stocks.batch_update_quotes([u])
        assert mock_execute.await_count == 1
        sql, params = mock_execute.await_args.args
        # 慢字段的 COALESCE/NULLIF 兜底语义必须原样保留
        assert "speed=COALESCE(CASE code WHEN %s THEN %s END,speed)" in sql
        assert "industry=COALESCE(NULLIF(CASE code WHEN %s THEN %s END,''),industry)" in sql
        assert "volume_ratio=COALESCE(NULLIF(CASE code WHEN %s THEN %s END,0),volume_ratio)" in sql
        assert "free_cap=COALESCE(NULLIF(CASE code WHEN %s THEN %s END,0),free_cap)" in sql
        assert "turnover=COALESCE(NULLIF(CASE code WHEN %s THEN %s END,0),turnover)" in sql
        assert "quote_updated_at=NOW()" in sql
        assert "WHERE code IN (%s)" in sql
        assert "000001" not in sql and "银行" not in sql
        assert _placeholders_match(sql, params)
        # 12 列 x (code,val) + WHERE IN 1 code = 25 个参数
        expected_vals = [12.3, -1.2, 3.4e8, 0.5, "银行", 1.8, 2.1e11, 0.9, 66, 12.0, 12.1, 11.5]
        assert list(params) == [p for v in expected_vals for p in ("000001", v)] + ["000001"]

    async def test_optional_fields_default(self, mock_execute):
        await stocks.batch_update_quotes(
            [{"code": "300750", "price": 200.0, "pct_change": 3.0, "amount": 9e9, "speed": None}])
        sql, params = mock_execute.await_args.args
        assert _placeholders_match(sql, params)
        # industry 缺省 '' / 其余可选字段缺省 None (与旧 executemany 版取值口径一致)
        vals = list(params)[1::2][:12]  # 12 个 CASE 的 THEN 值
        assert vals == [200.0, 3.0, 9e9, None, "", None, None, None, None, None, None, None]

    async def test_multi_rows_single_roundtrip(self, mock_execute):
        updates = [{"code": f"00000{i}", "price": 1.0, "pct_change": 0.0, "amount": 0.0,
                    "speed": None} for i in range(80)]
        await stocks.batch_update_quotes(updates)
        assert mock_execute.await_count == 1  # 改前: 80 次往返 ≈ 3.5s


# ------------------------------------------------------------ board strength

class TestBatchUpdateBoardStrength:
    async def test_empty_input_no_roundtrip(self, mock_execute):
        await stocks.batch_update_board_strength([])
        mock_execute.assert_not_awaited()

    async def test_single_row(self, mock_execute):
        await stocks.batch_update_board_strength(
            [{"code": "002415", "board_name": "安防", "board_rank": 3, "board_total": 25}])
        assert mock_execute.await_count == 1
        sql, params = mock_execute.await_args.args
        assert "board_name=CASE code WHEN %s THEN %s END" in sql
        assert "WHERE code IN (%s)" in sql
        # 按 code 更新不带 user_id 条件(板块强弱与 user 无关, 同 code 各用户行一并刷)
        assert "user_id" not in sql
        assert _placeholders_match(sql, params)
        assert list(params) == ["002415", "安防", "002415", 3, "002415", 25, "002415"]

    async def test_multi_rows_defaults(self, mock_execute):
        await stocks.batch_update_board_strength(
            [{"code": "600000"}, {"code": "600001", "board_name": "银行", "board_rank": 1,
                                  "board_total": 10}])
        assert mock_execute.await_count == 1
        sql, params = mock_execute.await_args.args
        assert _placeholders_match(sql, params)
        assert list(params) == ["600000", "", "600001", "银行",
                                "600000", None, "600001", 1,
                                "600000", None, "600001", 10,
                                "600000", "600001"]


# ---------------------------------------------------------------- sort order

class TestBatchUpdateSortOrder:
    async def test_empty_input_no_roundtrip(self, mock_execute):
        await stocks.batch_update_sort_order(1, [])
        mock_execute.assert_not_awaited()

    async def test_single_user_scoped(self, mock_execute):
        await stocks.batch_update_sort_order(7, ["600519", "000001", "300750"])
        assert mock_execute.await_count == 1  # 改前: 3 次往返
        sql, params = mock_execute.await_args.args
        assert "sort_order=CASE code WHEN %s THEN %s WHEN %s THEN %s WHEN %s THEN %s END" in sql
        # 与前三个函数不同: 手动排序是单用户操作, 必须限定 user_id
        assert "WHERE user_id=%s AND code IN (%s,%s,%s)" in sql
        assert _placeholders_match(sql, params)
        assert list(params) == ["600519", 0, "000001", 1, "300750", 2,
                                7, "600519", "000001", "300750"]

    async def test_chunking_keeps_global_index(self, mock_execute, monkeypatch):
        monkeypatch.setattr(stocks, "_BATCH_CHUNK", 2)
        await stocks.batch_update_sort_order(1, ["a", "b", "c"])
        assert mock_execute.await_count == 2
        _, params2 = mock_execute.await_args_list[1].args
        # 第二块的下标必须接着全局顺序(2), 不能从 0 重新数
        assert list(params2) == ["c", 2, 1, "c"]
