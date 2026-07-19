"""统一 LLM 叙述封装 —— 两个 AI 顾问功能唯一碰模型的地方(方案A: LLM只写人话)。
provider 从 config.ai_advisor_provider 选(deepseek/claude); 失败/空/未配 → None, 上层降级。"""
import asyncio
import json
import logging

from backend.core.config import load_config

logger = logging.getLogger(__name__)


def _call_provider(provider: str, model: str, system_prompt: str, user_content: str,
                   max_tokens: int) -> str:
    """同步调具体 provider, 返回文本。deepseek 走 OpenAI 兼容, claude 走 anthropic。仅本模块内部用。"""
    cfg = load_config()
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.get("anthropic_api_key", ""),
                                     base_url=cfg.get("ai_base_url") or None)
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    # deepseek / OpenAI 兼容(复用 ai_analyst 现有做法)
    from openai import OpenAI
    client = OpenAI(api_key=cfg.get("anthropic_api_key", ""),
                    base_url=cfg.get("ai_base_url", "https://api.deepseek.com/v1"))
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_content}],
    )
    return resp.choices[0].message.content or ""


async def narrate(system_prompt: str, fact_sheet: dict, *, max_tokens: int = 4096) -> str | None:
    """把事实清单(dict)交给 LLM 写成中文叙述。返回文本; 失败/空/过短 → None(上层只缺叙述不瘫)。"""
    cfg = load_config()
    if not cfg.get("ai_advisor_enabled", False):
        return None
    provider = cfg.get("ai_advisor_provider", "deepseek")
    model = cfg.get("ai_model", "deepseek-chat")
    user_content = json.dumps(fact_sheet, ensure_ascii=False, default=str)
    try:
        text = await asyncio.to_thread(
            _call_provider, provider, model, system_prompt, user_content, max_tokens)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ai_advisor] narrate 调用失败({provider}): {e}")
        return None
    text = (text or "").strip()
    if len(text) < 100:
        logger.warning(f"[ai_advisor] narrate 返回过短({len(text)}字), 视作失败")
        return None
    return text
