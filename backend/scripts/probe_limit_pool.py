"""涨停池数据源探测 — 短线情绪盯盘模块 P1 落地前置验证。

目的: 在 prod IP 上确认两个涨停池源能否取到, 以及字段能否支撑设计所需 5 指标:
  封板率 / 炸板率 / 连板梯队 / 最高连板 / 昨涨停今日溢价。

跑法 (项目根目录):
  python -m backend.scripts.probe_limit_pool            # 默认探当天
  python -m backend.scripts.probe_limit_pool 20260529   # 指定交易日 (周末/节假日必须指定)

不依赖 DB、不写 api_metrics, 用独立 httpx client (trust_env=False, 对齐 http_client.py)。
只读探测, 不改任何数据。
"""
import asyncio
import json
import sys

import httpx

# Windows GBK 控制台无法打印 ✓/✗ 等字符, 强制 stdout 用 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.10jqka.com.cn/",
}

# 东财公开 ut, 涨停池/炸板池/跌停池共用
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        trust_env=False,
    )


def _sample_fields(pool: list[dict], n: int = 1) -> None:
    """打印样本记录的全部字段名 + 前 n 条值, 供人工核对字段含义。"""
    if not pool:
        print("    (池为空)")
        return
    print(f"    字段名: {sorted(pool[0].keys())}")
    for item in pool[:n]:
        print(f"    样本: {json.dumps(item, ensure_ascii=False)}")


async def probe_em_pool(client, name: str, endpoint: str, date: str) -> dict | None:
    """探东财某个池 (涨停/炸板/跌停)。返回 data dict 或 None。"""
    url = (f"https://push2ex.eastmoney.com/{endpoint}"
           f"?ut={EM_UT}&dpt=wz.ztzt&Pageindex=0&pagesize=300"
           f"&sort=fbt%3Aasc&date={date}&_=0")
    print(f"\n[东财·{name}] {endpoint}")
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        print(f"    HTTP {resp.status_code}  bytes={len(resp.content)}")
        if resp.status_code >= 400:
            print(f"    ✗ 失败: {resp.text[:200]}")
            return None
        data = resp.json()
        rc = data.get("rc")
        pool = (data.get("data") or {}).get("pool") or []
        print(f"    rc={rc}  池内股票数={len(pool)}")
        _sample_fields(pool)
        return data.get("data")
    except Exception as e:
        print(f"    ✗ 异常: {e!r}")
        return None


async def probe_ths_limit_up(client, date: str) -> None:
    """探同花顺涨停池 (备源)。THS 常需 hexin-v cookie, 这里裸探看是否被风控。"""
    url = (f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
           f"?page=1&limit=200&field=199112,10,9001,330323,330324,330325,"
           f"9002,330329,133971,133970,1968584,3475914,9003,9004"
           f"&filter=HS,GEM2STAR&order_field=330324&order_type=0&date={date}")
    print(f"\n[同花顺·涨停池(备源)] data.10jqka.com.cn/dataapi/limit_up/limit_up_pool")
    try:
        resp = await client.get(url, headers=THS_HEADERS)
        print(f"    HTTP {resp.status_code}  bytes={len(resp.content)}")
        if resp.status_code >= 400:
            print(f"    ✗ 失败 (可能需 cookie/token): {resp.text[:200]}")
            return
        data = resp.json()
        info = data.get("data") or {}
        pool = info.get("info") if isinstance(info, dict) else None
        if pool is None:
            print(f"    返回结构 (前 300 字): {json.dumps(data, ensure_ascii=False)[:300]}")
            return
        print(f"    status_code={data.get('status_code')}  池内股票数={len(pool)}")
        _sample_fields(pool)
    except Exception as e:
        print(f"    ✗ 异常: {e!r}")


def _verdict(zt_data, zb_data) -> None:
    """根据探测结果给出设计 5 指标的可得性结论。"""
    print("\n" + "=" * 60)
    print("设计 5 指标可得性结论 (基于东财主源):")
    zt_pool = (zt_data or {}).get("pool") or []
    zb_pool = (zb_data or {}).get("pool") or []
    zt_n, zb_n = len(zt_pool), len(zb_pool)
    if zt_pool:
        sample = zt_pool[0]
        has_lbc = any(k in sample for k in ("lbc", "lbct"))   # 连板数
        has_fund = any(k in sample for k in ("fund", "fbt"))  # 封单/封板时间
        print(f"  涨停数: {zt_n} (✓ 有)")
        print(f"  炸板数: {zb_n} (✓ 有炸板池)" if zb_data is not None else "  炸板数: ✗ 炸板池未取到")
        if zt_n and zb_n is not None:
            seal = zt_n / (zt_n + zb_n) if (zt_n + zb_n) else 0
            print(f"  封板率(估)= {zt_n}/({zt_n}+{zb_n}) = {seal:.0%}")
        print(f"  连板梯队/最高板: {'✓ 有连板字段(lbc)' if has_lbc else '⚠ 未见 lbc, 需确认连板字段名'}")
        print(f"  封单资金/封板时间: {'✓ 有' if has_fund else '⚠ 未见'}")
        print("  昨涨停今日溢价: 不在此接口, 走 sectors.py 已暴露的「昨日涨停」标签 + quotes 现价")
    else:
        print("  东财主源未取到数据 — 检查: (1) date 是否交易日 (2) prod IP 是否被风控")
        print("  → 若主源不稳, 设计§4.1 降级链 (同花顺备 / 全市场行情估算) 价值得到验证")


async def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "今天"
    if date == "今天":
        # 不引入 datetime (脚本保持纯探测), 提示用户显式传日期更可靠
        print("⚠ 未传日期, 将让接口用默认当日。周末/节假日请显式传交易日, 例:")
        print("  python -m backend.scripts.probe_limit_pool 20260529\n")
        date = ""

    print(f"涨停池数据源探测  date={date or '(接口默认当日)'}")
    print("=" * 60)
    async with _client() as client:
        zt = await probe_em_pool(client, "涨停池", "getTopicZTPool", date)
        zb = await probe_em_pool(client, "炸板池", "getTopicZBPool", date)
        await probe_em_pool(client, "跌停池", "getTopicDTPool", date)
        await probe_ths_limit_up(client, date)
        _verdict(zt, zb)
    print("\n探测完成。")


if __name__ == "__main__":
    asyncio.run(main())
