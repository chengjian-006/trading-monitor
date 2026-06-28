from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import require_admin
from backend.core.config import load_config, save_config
from backend.models import repository
from backend.services import notifier

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config(_: Annotated[dict, Depends(require_admin)]):
    return load_config()


@router.post("")
async def update_config(data: dict, admin: Annotated[dict, Depends(require_admin)]):
    existing = load_config()
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
