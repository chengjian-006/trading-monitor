"""推送卡统一构造器 card_kit (基线 v1.1, 权威规范 docs/push-design-baseline.md)。

新推送一律走本模块构卡, 不再手拼卡片 JSON。分三层:
  1. 图形词汇表: 纯函数, 输出 md 字符串或 schema2.0 元素(强度条/触发清单/热图/温度计/
     多空条/灯串/KPI三栏/chart 折线柱状), 硬规则(格数上限/压扁/media锁定)在函数内兜死
  2. Card 数据类: (title, elements, fallback) 三元组 + 信封字段(family色/摘要/副标题/标签),
     经 notifier.send_card() 发送(飞书结构卡 + PushPlus 用 fallback 文本, 风险横幅发送层自动注入)
  3. 标准卡型: 聚合卡(风暴合并)/解除卡(状态型预警闭环)

所有图形元素均经手机端真机验证(三轮, 2026-07-16); 弃用清单(横排步进灯/字符走势/
字符柱状/column_set对齐表/原生table)禁止再引入。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.lark_notifier import collapsible_element, md_element

# ── 五大家族 header 色(基线第一节): 每张卡归且只归一个家族 ──
FAMILY_TEMPLATE = {
    "opportunity": "red",      # 机会: 买点/预增榜
    "exit": "green",           # 离场: 卖出/止损/尾盘破位
    "risk": "orange",          # 风险(谨慎档): 大盘风控/板块共振
    "risk_hot": "red",         # 风险(空仓·急跌档) + 纪律升级卡(唯一允许绿越级红)
    "intel": "blue",           # 情报: 竞价/复盘/日报/轮动/晚报
    "system": "grey",          # 系统: EOD复核/数据源/任务失败; 解除卡也用灰(中性收尾)
}


def family_template(family: str) -> str:
    """家族名 → header 颜色; 未知家族回 blue(与旧默认一致)。"""
    return FAMILY_TEMPLATE.get(family, "blue")


@dataclass
class Card:
    """构造层产物; 发送走 notifier.send_card(card)。fallback = PushPlus/回退纯文本(同源内容)。"""
    title: str
    elements: list
    fallback: str
    family: str = "intel"
    summary: str = ""                       # 锁屏横幅摘要: 标的+事件+关键数字(基线v1.1标配)
    subtitle: str = ""                      # header 副标题(单行务必短)
    tags: list = field(default_factory=list)  # [(文本, 颜色)] ≤3
    link_url: str = ""
    link_text: str = ""

    @property
    def template(self) -> str:
        return family_template(self.family)


# ── 数字/文本小件 ──

def pct_md(pct: float, bold: bool = True) -> str:
    """涨跌幅带色带号: 红 +2.7% / 绿 -1.8% / 平灰 0.0%。"""
    sign = f"{pct:+.1f}%" if pct else "0.0%"
    color = "red" if pct > 0 else ("green" if pct < 0 else "grey")
    inner = f"**{sign}**" if bold else sign
    return f"<font color='{color}'>{inner}</font>"


def summary_text(*parts) -> str:
    """自定义摘要: 非空片段用空格拼(如 名称/代码/事件/关键数)。"""
    return " ".join(str(p) for p in parts if p not in (None, ""))[:120]


def advice(text: str) -> dict:
    """行动建议区: 👉 加粗大白话(≤20字, 动词开头由调用方保证)。"""
    return md_element(f"👉 **{text}**")


def fold(summary_md: str, detail_md: str, expanded: bool = False) -> dict:
    """折叠详情区(触发明细/方法论/长名单/长值下沉)。"""
    return collapsible_element(summary_md, detail_md, expanded)


# ── 图形词汇表(标配 8 件, 硬规则在函数内兜死) ──

def strength_bar(ratio: float, label: str = "", color: str = "red", slots: int = 8) -> str:
    """强度条: ≤8 格, 一行一组。ratio 0~1。如 ▰▰▰▰▰▱▱▱ **62%**。"""
    slots = min(max(slots, 1), 8)
    filled = round(min(max(ratio, 0.0), 1.0) * slots)
    parts = []
    if filled:
        parts.append(f"<font color='{color}'>{'▰' * filled}</font>")
    if slots - filled:
        parts.append(f"<font color='grey'>{'▱' * (slots - filled)}</font>")
    return "".join(parts) + (f" **{label}**" if label else "")


def checklist(rows: list) -> str:
    """✅ 触发清单(竖排标准形态): rows = [(条件, 实测值, 门槛), ...], 门槛可空。
    输出每行: ✅ 条件 **实测值**（要求 门槛）"""
    lines = []
    for row in rows:
        cond, actual, req = (list(row) + ["", ""])[:3]
        line = f"✅ {cond} **{actual}**"
        if req:
            line += f"（要求 {req}）"
        lines.append(line)
    return "\n".join(lines)


def heat_strip(results: list, cap: int = 10) -> str:
    """战绩热图: results = [True赢/False亏/None无], ≤10 格, 自带 N胜N负 计数。"""
    shown = list(results)[:min(cap, 10)]
    icons = "".join("🟩" if r is True else ("🟥" if r is False else "⬜") for r in shown)
    wins = sum(1 for r in results if r is True)
    losses = sum(1 for r in results if r is False)
    return f"{icons} {wins}胜{losses}负"


def thermometer(segments: list, label: str = "", total: int = 10) -> str:
    """温度计: segments = [(格数, 颜色), ...] 分段变色, 总 ≤10 格, 不足补灰空格。"""
    total = min(max(total, 1), 10)
    parts, used = [], 0
    for n, color in segments:
        n = min(int(n), total - used)
        if n <= 0:
            continue
        parts.append(f"<font color='{color}'>{'▰' * n}</font>")
        used += n
    if total - used:
        parts.append(f"<font color='grey'>{'▱' * (total - used)}</font>")
    return "".join(parts) + (f" **{label}**" if label else "")


def long_short_bar(down_n: int, up_n: int, down_label: str = "跌", up_label: str = "涨") -> str:
    """多空条: 每边 ≤3 格保一行。如 跌33 ◀▰▰▰｜▰▰▰▶ 涨40。"""
    total = max(down_n + up_n, 1)
    dg = min(3, max(1, round(down_n / total * 6))) if down_n else 0
    ug = min(3, max(1, round(up_n / total * 6))) if up_n else 0
    left = f"<font color='green'>{down_label}{down_n} ◀{'▰' * dg}</font>" if down_n else f"<font color='grey'>{down_label}0</font>"
    right = f"<font color='red'>{'▰' * ug}▶ {up_label}{up_n}</font>" if up_n else f"<font color='grey'>{up_label}0</font>"
    return f"{left}｜{right}"


def light_string(lights: list) -> str:
    """灯串: lights = [(状态'ok'/'warn'/'bad', 说明), ...]。≤8 盏排灯+计数, 多了自动改纯计数式。"""
    icon = {"ok": "🟢", "warn": "🟡", "bad": "🔴"}
    ok = sum(1 for s, _ in lights if s == "ok")
    warn = sum(1 for s, _ in lights if s == "warn")
    bad = sum(1 for s, _ in lights if s == "bad")
    tail_parts = [f"{n}{t}" for n, t in ((ok, "正常"), (warn, "要看"), (bad, "故障")) if n]
    tail = " ".join(tail_parts)
    if len(lights) <= 8:
        return "".join(icon.get(s, "🟡") for s, _ in lights) + (f" {tail}" if tail else "")
    return " ".join(f"{icon[k]}{n}" for k, n in (("ok", ok), ("warn", warn), ("bad", bad)) if n)


def kpi_row(items: list) -> dict:
    """KPI 三栏大字(结论区标准形态): items = [(标签, 值, 颜色可选), ...] 恰好取前 3 个。
    数字层 heading 大字、标签层普通灰(基线硬规则)。"""
    cols = []
    for item in list(items)[:3]:
        label, value, color = (list(item) + [None])[:3]
        value_md = f"**{value}**" if not color else f"<font color='{color}'>**{value}**</font>"
        cols.append({
            "tag": "column", "width": "weighted", "weight": 1,
            "elements": [
                {"tag": "markdown", "content": value_md, "text_size": "heading",
                 "text_align": "center"},
                {"tag": "markdown", "content": f"<font color='grey'>{label}</font>",
                 "text_align": "center"},
            ],
        })
    return {"tag": "column_set", "flex_mode": "none", "columns": cols}


def heading_md(text: str) -> dict:
    """heading 大字行: 单行核心结论/合计数。"""
    return {"tag": "markdown", "content": (text or "")[:200], "text_size": "heading"}


def chart(kind: str, points: list, color: str = "#D83931",
          y_zero: bool = False, show_points: bool = True) -> dict:
    """原生图表(大杀器): kind='line'|'bar', points=[(x, y), ...]。
    硬规则在此兜死: aspect_ratio 2:1 压扁 + chart_spec 内 media:[] 锁定
    (第三轮真机: 不带 media 手机端被平台默认拉回 1:1 大方图; media 放元素层 API 拒收 200621);
    折线 y 轴默认 zero=False(波动更明显)。每卡 ≤2 个图由调用方遵守。"""
    spec: dict = {
        "type": "bar" if kind == "bar" else "line",
        "media": [],
        "data": [{"id": "d", "values": [{"x": str(x), "y": y} for x, y in points]}],
        "xField": "x", "yField": "y",
        "color": [color],
        "padding": {"top": 2, "bottom": 0, "left": 4, "right": 8},
    }
    if kind != "bar":
        spec["point"] = {"visible": bool(show_points)}
        spec["axes"] = [{"orient": "left", "zero": bool(y_zero)}]
    return {"tag": "chart", "aspect_ratio": "2:1", "chart_spec": spec}


def short_table(headers: list, rows: list) -> dict:
    """全短列 md_table(基线 v1.1 表格铁律): 只用于每格都是短值的场景, ≤3 列;
    列序 = 重要列前置、末列放可牺牲值; 任何长值(价格/长文本)下沉折叠区, 别塞进来。"""
    headers = list(headers)[:3]
    n = len(headers)
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join(["---"] * n) + " |"]
    for r in rows:
        cells = [str(c if c is not None else "").replace("|", "／").replace("\n", " ")
                 for c in list(r)[:n]]
        lines.append("| " + " | ".join(cells) + " |")
    return md_element("\n".join(lines))


# ── 标准卡型(基线第五节) ──

def aggregate_card(event: str, rows: list, *, cause_md: str, advice_text: str,
                   window: str = "", tag: str = "", family: str = "risk",
                   table_headers: list | None = None,
                   fold_summary: str = "", fold_detail: str = "") -> Card:
    """聚合卡(风暴合并, 真机定稿 D6 形态): 同族信号短窗口凑够 ≥3 条合并一张, 普跌日防轰炸。
    rows = [(股票名, 关键值md, 短信号名), ...]; 长值(价格等)放 fold_detail。"""
    n = len(rows)
    title = f"🚨 集中触发 · 自选{n}只 {event}"
    elements: list = [md_element(f"**归因**：{cause_md}")]
    elements.append(short_table(table_headers or ["股票", "涨跌", "信号"], rows))
    elements.append(advice(advice_text))
    if fold_detail:
        elements.append(fold(fold_summary or "触发明细与口径", fold_detail))
    fb_lines = [f"🚨 集中触发 · 自选{n}只 {event}", f"归因：{cause_md}"]
    fb_lines += [f"{r[0]} {r[1]} {r[2]}" for r in rows]
    fb_lines.append(f"👉 {advice_text}")
    return Card(
        title=title, elements=elements, fallback="\n".join(fb_lines), family=family,
        summary=summary_text(f"{n}只自选集中触发", event, tag),
        subtitle=f"{window} 合并推送" if window else "",
        tags=[(tag, "orange")] if tag else [],
    )


def dismiss_card(alert_name: str, *, issued_str: str, days_active: int,
                 condition_md: str, period_md: str = "", advice_text: str = "") -> Card:
    """解除卡(状态型预警闭环, 真机定稿 E 形态): 灰 header 中性收尾, 写明解除的是哪个条件。
    防抖由调用方保证(解除判定需超缓冲带或持续 N 周期); 一次性事件卡(买卖点)不发解除。"""
    title = f"✅ 预警解除 · {alert_name}"
    subtitle = f"{issued_str} 发布 → 今日解除，生效 {days_active} 个交易日"
    elements: list = [md_element(f"✅ 解除条件：{condition_md}")]
    if period_md:
        elements.append(md_element(f"**生效期间**：{period_md}"))
    if advice_text:
        elements.append(advice(advice_text))
    fb_lines = [title, subtitle, f"解除条件：{condition_md}"]
    if period_md:
        fb_lines.append(f"生效期间：{period_md}")
    if advice_text:
        fb_lines.append(f"👉 {advice_text}")
    return Card(
        title=title, elements=elements, fallback="\n".join(fb_lines), family="system",
        summary=summary_text(alert_name, "解除", f"生效{days_active}个交易日"),
        subtitle=subtitle, tags=[("已解除", "grey")],
    )
