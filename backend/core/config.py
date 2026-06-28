import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config.json")

DEFAULT_CONFIG = {
    "lark_webhook": "",
    "lark_enabled": False,
    # 推送通道关闭时刻(关→开补发错过的关键信号用); 空=当前未处于关闭态
    "lark_disabled_at": "",
    "wxpusher_token": "",
    "wxpusher_uids": [],
    "pushplus_token": "",
    "pushplus_enabled": True,
    "scan_interval_seconds": 6,
    "trading_hours": [
        {"start": "09:15", "end": "11:30"},
        {"start": "13:00", "end": "15:00"},
    ],
    "anthropic_api_key": "",
    "ai_report_enabled": True,
    "sso_enabled": True,
    # 数据库连接的真实凭证放在 config.json(已 gitignore, 不入库); 这里只留空占位。
    # 本地/生产部署请复制 config.example.json 为 config.json 并填入真实值。
    "database": {
        "host": "",
        "port": 3306,
        "user": "",
        "password": "",
        "db": "",
    },
    # 同花顺投资圈博主发帖跟踪。enabled=false 占位, 待抓到真实接口 cURL 后填 request/field_map 再开。
    # 完整字段说明见 backend/fetcher/blog_posts.py 顶部 docstring。
    "blogger_tracking": {
        "enabled": False,
        "bloggers": [],          # [{"fid": "<博主fid>", "name": "全能的野人"}]
        "request": {},           # {url, method, params, headers, body}
        "field_map": {},         # {list, post_id, time, content, url}
    },
}


def load_config() -> dict:
    path = os.path.normpath(CONFIG_PATH)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        merged = {**DEFAULT_CONFIG, **saved}
        return merged
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    path = os.path.normpath(CONFIG_PATH)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Production environment gating (用于本地开发禁推 wechat) ──

_PROD_IPS = {"124.71.75.5"}  # 生产服务器出口 IP，本地 IP 不在此集合则禁推
_outbound_ip_cache: str | None = None   # 仅缓存成功探测到的 IP(进程内不变)
_outbound_ip_retry_at: float = 0.0      # 探测失败后早于此刻不重试(防故障期每次推送都卡3次探测)
_OUTBOUND_FAIL_TTL = 60                  # 探测失败的冷却秒数, 过后自动重探


async def _probe_outbound_ip() -> str | None:
    """实际探测出口 IP(外网视角): 成功返回 IP 串, 三个源全失败返回 None。"""
    import httpx
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                resp = await client.get(url)
                ip = resp.text.strip()
                if ip and len(ip) <= 45 and "." in ip:
                    return ip
        except Exception:
            continue
    return None


async def get_outbound_ip() -> str:
    """获取本机出口 IP（外网视角）。

    成功结果缓存到进程退出; 探测失败**不永久缓存**(避免一次网络抖动后 is_production 永远 False、
    推送静默哑火至下次重启), 仅在 _OUTBOUND_FAIL_TTL 秒内不重探, 之后自动重试——故障恢复无需重启。
    """
    global _outbound_ip_cache, _outbound_ip_retry_at
    if _outbound_ip_cache is not None:
        return _outbound_ip_cache
    import time
    now = time.time()
    if now < _outbound_ip_retry_at:
        return "unknown"            # 仍在失败冷却窗口内, 不重探
    ip = await _probe_outbound_ip()
    if ip is not None:
        _outbound_ip_cache = ip
        return ip
    _outbound_ip_retry_at = now + _OUTBOUND_FAIL_TTL
    return "unknown"


async def is_production() -> bool:
    """出口 IP 在 _PROD_IPS 集合内 = 生产环境，否则禁推。"""
    ip = await get_outbound_ip()
    return ip in _PROD_IPS
