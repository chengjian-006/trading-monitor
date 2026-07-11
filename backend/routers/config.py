import re
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import require_admin
from backend.core.config import load_config, save_config
from backend.models import repository
from backend.services import notifier

router = APIRouter(prefix="/api/config", tags=["config"])


# ── 敏感字段打码 ──
# GET 不回传明文密钥: 键名命中敏感词的字符串叶子统一打码成固定哨兵;
# PUT 收到哨兵 → 保留服务器现值不覆盖(递归合并), 前端"GET→原样PUT回去"不破坏任何密钥。
MASK_SENTINEL = "••••••"
# 词边界匹配, 防误伤 hotkey/monkey 之类; api_?key 兼容 apikey/api_key; 宁可多打码不可漏。
_SENSITIVE_KEY_RE = re.compile(
    r"secret|password|passwd|token|cookie|api_?key|private|dsn|(?:^|[_\-])key(?:[_\-]|$)",
    re.IGNORECASE,
)


def _is_sensitive_key(key) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key)))


def mask_secrets(node, key=None):
    """递归整棵配置树: 敏感键名下的非空字符串叶子 → 哨兵; 其余原样。"""
    if isinstance(node, dict):
        return {k: mask_secrets(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [mask_secrets(v, key) for v in node]
    if isinstance(node, str) and node and key is not None and _is_sensitive_key(key):
        return MASK_SENTINEL
    return node


def _contains_sentinel(node) -> bool:
    if isinstance(node, str):
        return node == MASK_SENTINEL
    if isinstance(node, dict):
        return any(_contains_sentinel(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_sentinel(v) for v in node)
    return False


def restore_sentinels(new, old):
    """PUT 兼容打码: 值为哨兵 → 用服务器现值; dict 递归、list 同长逐项对齐。
    list 长度变了且新值里仍有哨兵(无法对位还原) → 整段保留服务器现值, 宁可丢改动不可写坏密钥。"""
    if new == MASK_SENTINEL:
        return old if old is not None else ""
    if isinstance(new, dict):
        old_d = old if isinstance(old, dict) else {}
        return {k: restore_sentinels(v, old_d.get(k)) for k, v in new.items()}
    if isinstance(new, list):
        old_l = old if isinstance(old, list) else []
        if len(new) == len(old_l):
            return [restore_sentinels(nv, ov) for nv, ov in zip(new, old_l)]
        return old if _contains_sentinel(new) else new
    return new


@router.get("")
async def get_config(_: Annotated[dict, Depends(require_admin)]):
    return mask_secrets(load_config())


@router.post("")
async def update_config(data: dict, admin: Annotated[dict, Depends(require_admin)]):
    existing = load_config()
    data = restore_sentinels(data, existing)   # 哨兵字段还原为服务器现值, 不覆盖真实密钥
    changed_old = {}
    changed_new = {}
    for k, v in data.items():
        if k == "database":
            continue
        old_v = existing.get(k)
        if old_v != v:
            changed_old[k] = old_v
            changed_new[k] = v
    existing.update(data)

    # 推送开关翻转: 关→记关闭时刻; 开→记下窗口待补发, 并清空关闭时刻
    from datetime import datetime
    from backend.services.push_backfill import STAMP_KEY, FMT
    to_backfill: list[tuple[str, str]] = []  # (通道, 关闭时刻)
    for ch, flag_key in (("lark", "lark_enabled"),):
        if flag_key not in changed_new:                 # 该开关本次未变化
            continue
        stamp_key = STAMP_KEY[ch]
        old_on = bool(changed_old.get(flag_key))
        new_on = bool(changed_new.get(flag_key))
        if old_on and not new_on:                       # 关闭: 盖时间戳(若尚未记)
            if not existing.get(stamp_key):
                existing[stamp_key] = datetime.now().strftime(FMT)
        elif (not old_on) and new_on:                   # 打开: 捞窗口补发, 清空戳
            disabled_at = existing.get(stamp_key) or ""
            existing[stamp_key] = ""
            if disabled_at:
                to_backfill.append((ch, disabled_at))

    save_config(existing)
    if changed_old or changed_new:
        await repository.add_log(admin["id"], admin["username"], "update_config", "",
                                 old_value=changed_old, new_value=changed_new)

    # 配置已落盘(通道现为开启态)后再补发, 失败不影响保存结果
    for ch, disabled_at in to_backfill:
        try:
            from backend.services import push_backfill
            await push_backfill.backfill_channel(ch, disabled_at)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[config] {ch} 补发失败: {e}")
    return {"ok": True}


@router.post("/test-pushplus")
async def test_pushplus(_: Annotated[dict, Depends(require_admin)]):
    """PushPlus(个人微信)连接测试, token 取全局配置(请先保存 token)。"""
    ok, msg = await notifier.send_pushplus_test()
    return {"ok": ok, "msg": msg}


@router.post("/test-lark")
async def test_lark(data: dict, _: Annotated[dict, Depends(require_admin)]):
    webhook = data.get("webhook", "")
    if not webhook:
        return {"ok": False, "msg": "Webhook地址为空"}
    from backend.services.lark_notifier import send_lark_test
    ok, msg = await send_lark_test(webhook)
    return {"ok": ok, "msg": msg}


@router.post("/test-signal-card")
async def test_signal_card(user: Annotated[dict, Depends(require_admin)]):
    """发样例买点信号卡(走真实 send_wechat_signal 通道), 在飞书里预览完整卡片含快捷动作行。

    用当前管理员的个人飞书 webhook 发送; 仅生产环境(出口IP白名单)能真正推出。
    """
    ok = await notifier.send_wechat_signal(
        code="002407", name="多氟多",
        signal_name="回踩10MA缩量后突破昨高",
        direction="buy", price=32.50,
        detail="【测试数据】缩量回踩MA10(昨量是均量的0.62倍) → 放量突破昨高2.5% | 当日量≥10日均量×1.5",
        user_id=user["id"],
        strategy="突破即报, 站稳MA10持有, 跌破MA10×0.98卖剩半",
        pct_change=2.09,
        signal_id="BUY_RALLY_MA10",
    )
    return {"ok": ok,
            "msg": "已发送测试信号卡, 看你的飞书(卡片底部应有快捷动作行)" if ok
                   else "发送失败: 检查个人飞书开关/Webhook是否已配, 或当前非生产环境"}
