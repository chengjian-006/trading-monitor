"""AI 真受益核查服务

用 LLM 回答 8 个核心问题,把基本面信息整理成格式化报告,
人来做最终判定(真受益 vs 画饼)。

数据流:
  用户输入 code + theme → 调 LLM → 格式化报告 → 人工打分(0-5星) → 存 DB
"""

import asyncio
import logging
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """你是一位资深 A 股基本面分析师,擅长辨别"真受益题材股"和"蹭概念画饼股"。

任务: 针对用户给出的{股票, 题材},基于公开可查的最新信息(公司公告、财报、研报、产业链数据),
按照固定格式输出分析报告。

严格遵守:
1. 不要做股价预测或买卖建议
2. 数据要客观,引用具体数字
3. 如某项信息无法确认,标注"信息不足"
4. 输出必须严格按下面的 Markdown 格式,不要任何额外文字
5. 中文输出
"""


_OUTPUT_TEMPLATE = """请按下面格式输出(不要修改标题层级、emoji、分隔符):

## 📋 真受益核查报告
**股票**: {name} ({code})
**题材**: {theme}
**核查时间**: {now}

---

### 🎯 1. 主营业务
<一句话描述主营业务>

### 📊 2. 题材关联度
- **关联强度**: ⭐⭐⭐⭐⭐ (5星=核心受益, 1星=蹭概念)
- **理由**: <为什么是这个关联度>
- **题材业务占比**: <% 或"信息不足">

### 💰 3. 核心订单/产品/产能
- <列出具体产品/订单/产能,至少 1 条>
- <数字越具体越好,如"独家供货某客户的X产品">

### 📈 4. 业绩弹性(近 1-2 季)
- **营收**: <最新一季营收 + 同比%>
- **净利润**: <最新一季净利润 + 同比%>
- **毛利率**: <数字% + 同比变化>
- **指引**: <公司是否对未来业绩有明确指引>

### 🏆 5. 行业地位
- **定位**: <龙头/二线/跟随/边缘>
- **市场份额**: <具体数字 或 "信息不足">
- **核心竞争力**: <技术/客户/产能/规模等具体优势>

### ⚖️ 6. 同行对比
- vs <竞品1>: <差异点>
- vs <竞品2>: <差异点>

### ⚠️ 7. 风险点(故事破灭的可能性)
- <风险 1: 客户依赖/技术替代/估值过高/订单波动等>
- <风险 2>

### 💡 8. 核查结论参考(不构成投资建议)
- **数据综合判定**: <核心受益 / 边缘受益 / 蹭概念> ⭐× N
- **关键依据**: <一句话总结最有力的证据>
"""


def _build_prompt(code: str, name: str, theme: str, industry: str = "") -> tuple[str, str]:
    now = datetime.now().strftime("%Y-%m-%d")
    user_content = (
        f"请对以下股票进行【真受益核查】:\n\n"
        f"股票: {name} ({code})\n"
        f"题材: {theme}\n"
        f"所属行业: {industry or '未知'}\n\n"
        + _OUTPUT_TEMPLATE.format(name=name, code=code, theme=theme, now=now)
    )
    return _SYSTEM_PROMPT, user_content


async def analyze_substance(code: str, name: str, theme: str, industry: str = "") -> dict:
    """调 LLM 生成真受益核查报告。

    Returns:
        {
          "ok": bool,
          "report": str | None,      # Markdown 格式的报告
          "model": str,
          "error": str | None,
        }
    """
    if not code or not theme:
        return {"ok": False, "error": "code 和 theme 必填", "report": None, "model": ""}

    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        return {"ok": False, "error": "AI API key 未配置", "report": None, "model": ""}

    base_url = cfg.get("ai_base_url", "https://api.deepseek.com/v1")
    model = cfg.get("ai_substance_model") or cfg.get("ai_model", "deepseek-chat")

    system_prompt, user_content = _build_prompt(code, name, theme, industry)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        # 同步 OpenAI 调用卸到线程, 否则 LLM 最多 60s 会冻住事件循环、拖慢全站所有请求。
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            timeout=60,
        )
        content = response.choices[0].message.content
        if not content or len(content) < 100:
            return {"ok": False, "error": "AI 返回内容过短", "report": content or "", "model": model}
        return {"ok": True, "report": content, "model": model, "error": None}
    except Exception as e:
        logger.exception(f"[substance] AI 调用失败: {e}")
        return {"ok": False, "error": str(e), "report": None, "model": model}


async def save_substance_result(
    code: str,
    user_id: int,
    analysis: str,
    score: int | None = None,
    note: str | None = None,
):
    """保存分析报告(以及可选的人工评分和备注)到 stock_pool 表。

    Args:
        code: 股票代码
        user_id: 用户 ID
        analysis: AI 生成的 Markdown 报告(必须)
        score: 用户人工评分 0-5(可选,首次只存 analysis,后续打分时再传)
        note: 用户备注(可选)
    """
    fields = {"substance_analysis": analysis, "substance_updated_at": datetime.now()}
    if score is not None:
        fields["substance_score"] = max(0, min(5, int(score)))
    if note is not None:
        fields["substance_note"] = note

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [code, user_id]
    sql = f"UPDATE cfzy_biz_stock_pool SET {set_clause} WHERE code = %s AND user_id = %s"

    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, values)


async def update_substance_score(code: str, user_id: int, score: int, note: str = "") -> bool:
    """单独更新用户对某只股票的真受益评分(不重新调 AI)。"""
    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE cfzy_biz_stock_pool SET substance_score = %s, substance_note = %s, "
                "substance_updated_at = %s WHERE code = %s AND user_id = %s",
                (max(0, min(5, int(score))), note, datetime.now(), code, user_id),
            )
    return True
