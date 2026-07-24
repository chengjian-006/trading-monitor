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
    """去掉正文里冗余的名字前缀, 两种形态:
      1. lark-cli 把本人号的文本消息格式化成 '藏龙岛:正文';
      2. 播报机器人发的是 '🔴 藏龙岛\\n正文'(装饰符 + 名字的标题行, 正文在下一行)。
    形态 2 要求名字前必须有装饰符, 免得把「藏龙岛今天说…」这类正常正文的开头砍掉。
    """
    c = (content or "").lstrip()
    for sep in (f"{name}:", f"{name}："):
        if c.startswith(sep):
            return c[len(sep):].lstrip()
    m = re.match(r"[^\w一-鿿]+" + re.escape(name) + r"\s*", c)
    if m:
        return c[m.end():].lstrip()
    return c


def _coach_senders(cfg: dict) -> set[str]:
    """认作「藏龙岛本人观点」的发送者集合: 本人 open_id + 播报机器人 app_id 等。

    v1.7.792: 盘中实时点评改由群内播报机器人(sender_type=app)发, 只认单个 open_id
    会把这些整批漏掉 —— 故改白名单。sender_open_ids 缺省时只认 sender_open_id。
    """
    ids = {str(cfg.get("sender_open_id", "") or "")}
    ids.update(str(x) for x in (cfg.get("sender_open_ids") or []))
    return {i for i in ids if i}


def parse_payload(payload: dict, cfg: dict) -> list[dict]:
    """把 lark-cli 的 JSON 输出解析成归一化消息列表, 只留藏龙岛(发送者白名单内)发的。

    纯函数(无 I/O), 便于单测。ok=false 抛 LarkCoachFetchError。
    已撤回的消息(deleted=true, 正文是 '[Invalid text JSON]' 之类的占位)直接丢弃,
    否则会把占位符当观点入库并转发到用户群。
    """
    if not payload.get("ok", False):
        raise LarkCoachFetchError(f"lark-cli ok=false: {str(payload.get('error'))[:300]}")

    senders = _coach_senders(cfg)
    name = cfg.get("coach_name", "藏龙岛")
    chat_id = cfg.get("chat_id", "")
    messages = (payload.get("data") or {}).get("messages") or []
    results: list[dict] = []
    for m in messages:
        snd = m.get("sender") or {}
        sid = str(snd.get("id", "") or "")
        if sid not in senders:
            continue                       # 只留藏龙岛发的, 学员提问不入库
        if m.get("deleted"):
            continue                       # 已撤回, 正文是占位符
        mid = m.get("message_id")
        if not mid:
            continue
        results.append({
            "message_id": mid,
            "chat_id": m.get("chat_id") or chat_id,
            "sender_open_id": sid,
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


def _relay_send_args(cfg: dict) -> list[str]:
    """转发发送的身份参数: 独立授权档案(个人号应用, relay_profile) + 发送身份(relay_send_as)。

    公司租户的 im:message.send_as_user 需管理员审批批不下来, 故发送走个人账号的应用档案;
    身份可选 user(以本人名义, 需 send_as_user scope)或 bot(机器人进群即可发)。读消息仍用默认档案。
    """
    args = ["--as", cfg.get("relay_send_as", "user")]
    profile = cfg.get("relay_profile", "")
    if profile:
        args += ["--profile", profile]
    return args


async def send_chat_text(cfg: dict, chat_id: str, text: str) -> None:
    """发一条文本消息到目标群。失败抛 LarkCoachFetchError。"""
    await _run_cli(cfg, ["im", "+messages-send", "--chat-id", chat_id,
                         "--text", text, *_relay_send_args(cfg)])


# ── 群自定义机器人 Webhook 通道(定稿方案): 不受外部群/应用权限限制, 文本直发;
#    图片先经应用(coachbot 档案)上传拿 image_key 再发 ──

async def send_webhook_message(url: str, payload: dict) -> None:
    """向群自定义机器人 webhook POST 一条消息。失败抛 LarkCoachFetchError。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError as e:
        raise LarkCoachFetchError(f"webhook 请求失败: {e}") from e
    if resp.status_code != 200:
        raise LarkCoachFetchError(f"webhook HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json() if resp.content else {}
    if body.get("code", body.get("StatusCode", -1)) != 0:
        raise LarkCoachFetchError(f"webhook 返回异常: {str(body)[:200]}")


async def upload_relay_image(cfg: dict, file_dir: str, filename: str) -> str:
    """把本地图片经发送档案的应用上传, 返回 image_key(供 webhook 发图)。

    im images create 仅支持 bot 身份; --file 只收 cwd 相对路径, 切 cwd 到文件目录。
    """
    profile = cfg.get("relay_profile", "")
    args = ["im", "images", "create", "--as", "bot",
            "--data", '{"image_type":"message"}', "--file", f"image={filename}"]
    if profile:
        args += ["--profile", profile]
    payload = await _run_cli(cfg, args, timeout=60, cwd=file_dir)
    key = (payload.get("data") or {}).get("image_key", "")
    if not key:
        raise LarkCoachFetchError(f"上传图片未返回 image_key: {str(payload)[:200]}")
    return key


async def send_chat_image_file(cfg: dict, chat_id: str, file_dir: str, filename: str) -> None:
    """把本地图片文件发到目标群(CLI 自动经发送档案的应用上传)。

    image_key 是应用维度的, 跨应用(读=默认档案/发=个人档案)不认, 故按本地文件发。
    --image 只收 cwd 相对路径, 切 cwd 到文件目录。失败抛 LarkCoachFetchError。
    """
    await _run_cli(cfg, ["im", "+messages-send", "--chat-id", chat_id,
                         "--image", filename, *_relay_send_args(cfg)],
                   timeout=60, cwd=file_dir)
