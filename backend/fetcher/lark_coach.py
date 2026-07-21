# -*- coding: utf-8 -*-
"""飞书群「藏龙岛观点」采集 — 只抓群主(藏龙岛)发的消息.

走法: shell 调服务器上已授权的 lark-cli(im +chat-messages-list), 解析 JSON, 只留
sender.id == 配置里的 sender_open_id 的消息。token 生命周期由 lark-cli 自己管(过期自动刷新);
刷新链断了(长期没跑/撤权/改密)→ 拉取失败, 由 lark_coach_scanner 计数告警提醒重新授权。

不在群里的机器人读不到外部群, 故必须用 user 身份(--as user), 即 lark-cli 里授权的那个人。
"""
import asyncio
import json
import logging
import re
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

# systemd 单元的 PATH 往往不含 /usr/local/bin(npm -g 装的 lark-cli 就在那),
# 服务进程里 which 会失败 —— 实测 v1.7.741 上线后拉取报「未安装或不在 PATH」。
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin"


def _resolve_cli(exe: str) -> str:
    """解析 lark-cli 可执行路径: 先按进程 PATH 找, 找不到再搜常见安装位置。"""
    return shutil.which(exe) or shutil.which(exe, path=_FALLBACK_PATH) or exe


def _build_env(base: dict | None = None) -> dict:
    """子进程环境: 静音 lark-cli 的更新提示; 缺 HOME 时补上(systemd 单元不设 HOME,
    而 lark-cli 靠 $HOME 定位 ~/.lark-cli 授权配置, 缺了报 not_configured)。"""
    import os

    env = {**(os.environ if base is None else base),
           "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
           "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
    if "HOME" not in env:
        try:
            import pwd
            env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
        except Exception:  # Windows 无 pwd 模块等 — 保持原样
            pass
    return env


class LarkCoachFetchError(Exception):
    """拉取失败(lark-cli 非零退出 / ok:false / token 过期等)。"""


def _parse_time(s: str):
    """lark-cli 返回的 create_time 形如 '2026-07-21 10:50'(无秒)。解析失败返回 None。"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _strip_name_prefix(content: str, name: str) -> str:
    """lark-cli 把文本消息格式化成 '藏龙岛:正文'。已知是藏龙岛发的, 去掉冗余的名字前缀。"""
    c = (content or "").lstrip()
    for sep in (f"{name}:", f"{name}："):
        if c.startswith(sep):
            return c[len(sep):].lstrip()
    return c


def parse_payload(payload: dict, cfg: dict) -> list[dict]:
    """把 lark-cli 的 JSON 输出解析成归一化消息列表, 只留藏龙岛(sender_open_id)发的。

    纯函数(无 I/O), 便于单测。ok=false 抛 LarkCoachFetchError。
    """
    if not payload.get("ok", False):
        raise LarkCoachFetchError(f"lark-cli ok=false: {str(payload.get('error'))[:300]}")

    sender = cfg.get("sender_open_id", "")
    name = cfg.get("coach_name", "藏龙岛")
    chat_id = cfg.get("chat_id", "")
    messages = (payload.get("data") or {}).get("messages") or []
    results: list[dict] = []
    for m in messages:
        snd = m.get("sender") or {}
        if snd.get("id") != sender:
            continue                       # 只留藏龙岛发的, 学员提问不入库
        mid = m.get("message_id")
        if not mid:
            continue
        results.append({
            "message_id": mid,
            "chat_id": m.get("chat_id") or chat_id,
            "sender_open_id": sender,
            "coach_name": name,
            "posted_at": _parse_time(m.get("create_time", "")),
            "content": _strip_name_prefix(m.get("content", ""), name),
            "msg_type": m.get("msg_type", "text"),
        })
    return results


async def _run_cli(cfg: dict, cli_args: list[str], timeout: int = 45, cwd: str | None = None) -> dict:
    """跑一次 lark-cli 子命令并解析 JSON 输出。失败(非零退出/超时/ok=false)抛 LarkCoachFetchError。"""
    exe = cfg.get("lark_cli", "lark-cli")
    args = [_resolve_cli(exe), *cli_args]
    env = _build_env()

    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=cwd,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except FileNotFoundError as e:
        raise LarkCoachFetchError(f"lark-cli 未安装或不在 PATH: {e}") from e
    except asyncio.TimeoutError as e:
        raise LarkCoachFetchError(f"lark-cli 超时({timeout}s)") from e

    if proc.returncode != 0:
        raise LarkCoachFetchError(f"lark-cli 退出码 {proc.returncode}: "
                                  f"{(out or err or b'').decode('utf-8', 'ignore')[:300]}")
    try:
        payload = json.loads(out.decode("utf-8", "ignore"))
    except json.JSONDecodeError as e:
        raise LarkCoachFetchError(f"解析 lark-cli 输出失败: {e}") from e
    if not payload.get("ok", False):
        raise LarkCoachFetchError(f"lark-cli ok=false: {str(payload.get('error'))[:300]}")
    return payload


async def fetch_coach_messages(cfg: dict) -> list[dict]:
    """拉群最近一页消息, 过滤出藏龙岛(sender_open_id)发的, 归一化返回(按原顺序=时间倒序)。

    cfg 取自 config.json 的 lark_coach_tracking 段:
      chat_id / sender_open_id / coach_name / page_size / lark_cli(可执行名或路径)
    失败抛 LarkCoachFetchError。
    """
    chat_id = cfg.get("chat_id", "")
    sender = cfg.get("sender_open_id", "")
    page_size = int(cfg.get("page_size", 30))
    if not chat_id or not sender:
        raise LarkCoachFetchError("chat_id / sender_open_id 未配置")

    payload = await _run_cli(cfg, ["im", "+chat-messages-list",
                                   "--chat-id", chat_id, "--as", "user",
                                   "--sort", "desc", "--page-size", str(page_size)])
    return parse_payload(payload, cfg)


# ── 图片消息: content 形如 "[Image: img_v3_xxx]", 取 file_key 供下载/重发 ──
_IMG_KEY_RE = re.compile(r"\[Image: (img_[\w.\-]+)\]")


def extract_image_key(content: str) -> str | None:
    m = _IMG_KEY_RE.search(content or "")
    return m.group(1) if m else None


async def download_message_image(cfg: dict, message_id: str, file_key: str,
                                 dest_dir: str, filename: str) -> str:
    """下载图片消息的资源到 dest_dir/filename, 返回落盘绝对路径。

    lark-cli --output 只收相对路径(拒绝绝对/..), 故切 cwd 到 dest_dir。
    """
    import os

    os.makedirs(dest_dir, exist_ok=True)
    payload = await _run_cli(cfg, ["im", "+messages-resources-download",
                                   "--message-id", message_id, "--file-key", file_key,
                                   "--type", "image", "--as", "user",
                                   "--output", filename],
                             timeout=60, cwd=dest_dir)
    saved = (payload.get("data") or {}).get("saved_path") or os.path.join(dest_dir, filename)
    return saved


async def send_chat_text(cfg: dict, chat_id: str, text: str) -> None:
    """以 user 身份发一条文本消息到目标群。失败抛 LarkCoachFetchError。"""
    await _run_cli(cfg, ["im", "+messages-send", "--chat-id", chat_id,
                         "--as", "user", "--text", text])


async def send_chat_image(cfg: dict, chat_id: str, image_key: str) -> None:
    """以 user 身份把图片(按原 image_key)发到目标群。失败抛 LarkCoachFetchError。"""
    await _run_cli(cfg, ["im", "+messages-send", "--chat-id", chat_id,
                         "--as", "user", "--image", image_key])
