"""stock_extra THS realhead 熔断 + 独立池 测试。"""
import time

from backend.fetcher import stock_extra as mod


class TestThsRhBreaker:
    def setup_method(self):
        mod._ths_rh_fail_streak = 0
        mod._ths_rh_open_until = 0.0

    def test_not_blocked_initially(self):
        assert not mod._ths_rh_blocked()

    def test_trips_after_max_failures(self):
        for _ in range(mod._THS_RH_FAIL_MAX):
            mod._ths_rh_record(False)
        assert mod._ths_rh_blocked()

    def test_success_resets(self):
        for _ in range(mod._THS_RH_FAIL_MAX):
            mod._ths_rh_record(False)
        assert mod._ths_rh_blocked()
        mod._ths_rh_record(True)
        assert not mod._ths_rh_blocked()
        assert mod._ths_rh_fail_streak == 0

    def test_cooldown_expires(self):
        for _ in range(mod._THS_RH_FAIL_MAX):
            mod._ths_rh_record(False)
        assert mod._ths_rh_blocked()
        mod._ths_rh_open_until = time.monotonic() - 1
        assert not mod._ths_rh_blocked()

    def test_re_trips_after_cooldown_probe_fail(self):
        for _ in range(mod._THS_RH_FAIL_MAX):
            mod._ths_rh_record(False)
        mod._ths_rh_open_until = time.monotonic() - 1
        assert not mod._ths_rh_blocked()
        mod._ths_rh_record(False)
        assert mod._ths_rh_blocked()

    def test_isolated_client_not_shared(self):
        from backend.fetcher.http_client import _get_client
        ths_client = mod._get_ths_extra_client()
        shared_client = _get_client()
        assert ths_client is not shared_client
        mod._ths_extra_client = None
