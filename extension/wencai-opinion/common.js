// 问财观点扩展 — 前台(content)/后台(SW)/弹窗(popup)/查看页(viewer) 共用内核。
// content_scripts 里在 content.js 之前加载; background importScripts; popup/viewer 用 <script src>。
(function (root) {
  'use strict';

  function genSessionId() {
    let s = '';
    for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }

  // 版本比较: a>b→1, a<b→-1, 相等→0(按点分段数值比, 缺段补0, 非数字段忽略)。
  function cmpVer(a, b) {
    const pa = String(a || '0').split('.').map((x) => parseInt(x, 10) || 0);
    const pb = String(b || '0').split('.').map((x) => parseInt(x, 10) || 0);
    const n = Math.max(pa.length, pb.length);
    for (let i = 0; i < n; i++) {
      const x = pa[i] || 0, y = pb[i] || 0;
      if (x > y) return 1;
      if (x < y) return -1;
    }
    return 0;
  }

  function buildBody(question, sessionId, userId, deep) {
    const events = [{ event_name: 'auto_agent', event_type: 'user_input' }];
    if (deep) events.push({ event_name: 'ab_test', event_type: 'front_trigger', content: { deep_research: 1 } });
    return {
      version: '3.4.1', session_id: sessionId, user_id: userId || '', source: 'Ths_iwencai_Xuangu',
      input_type: 'typewrite', question: question, deviceType: 'browser',
      add_info: { merge_repeat: true, async_generate_data: true, show_searching: true,
                  urp: { is_lowcode: 1, component_version: '1.1.4' } },
      entity_info: {}, events: events,
    };
  }

  // ---- markdown 轻量渲染(标题/无序列表/编号段/加粗/代码), 流式部分文本也稳 ----
  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function inlineMd(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
  // 去掉问财内嵌图表/模型占位块(```visual{...uuid...}``` 之类, 页面用来渲染图表, 纯文本里是噪音)
  function stripEmbeds(text) {
    if (!text) return text || '';
    let t = String(text);
    t = t.replace(/```\s*visual[\s\S]*?```/gi, '');   // 闭合的 visual 块
    t = t.replace(/```\s*visual[\s\S]*$/i, '');        // 流式中途未闭合的尾块
    t = t.replace(/```[\s\S]*?```/g, (m) => (/"uuid"\s*:/.test(m) ? '' : m));  // 任何含 uuid 的裸块
    t = t.replace(/\n{3,}/g, '\n\n');
    return t.trim();
  }
  // markdown 表格辅助: 判分隔行(|---|:--:|) / 拆单元格
  const isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(s || '');
  const tableCells = (s) => String(s || '').replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((x) => x.trim());
  function mdRender(src) {
    const lines = stripEmbeds(src).split('\n'); let html = '', inList = false, m;
    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/\s+$/, '');
      // 表格: 当前行含 | 且下一行是分隔线 → 吃掉整块渲染成 <table>
      if (line.indexOf('|') >= 0 && isTableSep(lines[i + 1])) {
        closeList();
        const head = tableCells(line);
        let rows = '';
        i += 2;
        for (; i < lines.length; i++) {
          if (lines[i].indexOf('|') < 0 || !lines[i].trim()) { i--; break; }
          const cs = tableCells(lines[i]);
          rows += '<tr>' + cs.map((c) => '<td>' + inlineMd(c) + '</td>').join('') + '</tr>';
        }
        html += '<table class="md-table"><thead><tr>' + head.map((h) => '<th>' + inlineMd(h) + '</th>').join('') + '</tr></thead><tbody>' + rows + '</tbody></table>';
        continue;
      }
      if (!line.trim()) { closeList(); continue; }
      if (/^```/.test(line)) { closeList(); continue; }                         // 残留的 ``` 标记行丢弃
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.replace(/\s/g, ''))) { closeList(); html += '<hr>'; continue; }  // 分隔线
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeList(); const lv = m[1].length; html += '<h' + lv + '>' + inlineMd(m[2]) + '</h' + lv + '>'; }
      else if ((m = line.match(/^\s*[-•·]\s+(.*)$/))) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inlineMd(m[1]) + '</li>'; }
      else if ((m = line.match(/^\s*(\d{1,2})[.、]\s+(.*)$/))) { closeList(); html += '<p class="num"><strong>' + m[1] + '. </strong>' + inlineMd(m[2]) + '</p>'; }
      else { closeList(); html += '<p>' + inlineMd(line) + '</p>'; }
    }
    closeList(); return html;
  }

  // 结构化结论: 主用「让问财按固定格式输出→解析【】标记」, 没按格式则正则启发式兜底。
  // 给问句自动追加的格式要求(只发给问财, 不显示/不存)。
  const FORMAT_SUFFIX = '\n\n请在回答的最末尾，另起一行，用严格固定格式补一条结论小结（某项没有就填「-」，不要额外解释、不要加粗）：\n【标的】x【买点】x【止盈】x【止损】x【周期】x【逻辑】x【风险】x';
  const MK = { 标的: 'stock', 买点: 'buy', 止盈: 'takeProfit', 止损: 'stopLoss', 周期: 'period', 逻辑: 'logic', 风险: 'risk' };

  function parseMarkers(text) {
    if (!text || text.indexOf('【标的】') < 0) return null;
    const out = {}; let hit = 0;
    for (const k of Object.keys(MK)) {
      const m = text.match(new RegExp('【' + k + '】\\s*([\\s\\S]*?)(?=【[标买止周逻风]|\\n\\n|$)'));
      if (m) { const v = m[1].replace(/[*`]/g, '').trim(); out[MK[k]] = (v === '-' || v === '—' || v === '无') ? '' : v.slice(0, 80); hit++; }
    }
    return hit >= 3 ? out : null;
  }
  // 从 markdown 表格里抽结论: 首列是「标的/买点/止盈/止损/周期/逻辑/风险」标签、次列是值。
  // 问财常用「| 项目 | 执行建议 |」表输出结论, 直接读表得到的值最干净(无竖线/标签残留)。
  function parseTableConc(text) {
    const out = {}; let hit = 0;
    for (const ln of String(text || '').split('\n')) {
      if (ln.indexOf('|') < 0) continue;
      const cs = tableCells(ln).map((x) => x.replace(/[*`]/g, '').trim());
      if (cs.length < 2) continue;
      const key = MK[cs[0]];
      const val = cs[1];
      if (key && val && !/^[-—]+$/.test(val) && out[key] === undefined) { out[key] = val.slice(0, 120); hit++; }
    }
    return hit >= 3 ? out : null;
  }

  // 从一句结论里抽出价位: 优先带「元」锚点的区间/单值, 再退到区间, 再退到像价格的单数字。
  // 返回 {lo, hi}(单值 lo===hi)或 null(抽不到→上层退回只显示文字)。
  function extractPrice(text) {
    const t = String(text || '').replace(/[,，]/g, '');
    let m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~\-–]\s*(\d{2,5}(?:\.\d+)?)\s*元/);
    if (m) return { lo: +m[1], hi: +m[2] };
    m = t.match(/(\d{2,5}(?:\.\d+)?)\s*元/);
    if (m) return { lo: +m[1], hi: +m[1] };
    m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~–]\s*(\d{2,5}(?:\.\d+)?)/);
    if (m) return { lo: +m[1], hi: +m[2] };
    const re = /(\d{2,5}(?:\.\d+)?)(?!\s*(?:%|％|天|日|周|个?月|年|倍|万|亿|手))/g;
    let mm;
    while ((mm = re.exec(t))) { const v = +mm[1]; if (v >= 2 && v <= 100000) return { lo: v, hi: v }; }
    return null;
  }

  // 结论值清洗(展示前): 去首尾竖线、去开头重复标签(如 "止盈 | 一周...")、中间竖线→空格。
  function cleanConcVal(s, label) {
    let v = String(s || '').replace(/[*`]/g, '').trim();
    v = v.replace(/^\|+/, '').replace(/\|+$/, '').trim();
    if (label) v = v.replace(new RegExp('^' + label + '\\s*[|｜:：]?\\s*'), '');
    v = v.replace(/\s*\|\s*/g, ' ').replace(/\s+/g, ' ').trim();
    return v;
  }

  // 把结论小结那行从话术里去掉(卡片已单独展示, 正文不重复)
  function stripMarkerLine(text) { return (text || '').replace(/\n*【标的】[\s\S]*$/, '').trim(); }

  function clipC(s) { s = (s || '').replace(/\*\*/g, '').replace(/^[\s\-•·]+/, '').replace(/\s+/g, ' ').trim(); return s.length > 64 ? s.slice(0, 64) + '…' : s; }
  function sentenceOf(text, kw) {
    // 排除 | 作句界: 避免匹配跨到 markdown 表格单元格、把竖线也吞进来
    const re = new RegExp('[^。\\n;；！|]*(?:' + kw + ')[^。\\n;；！|]*', 'g');
    const arr = text.match(re);
    if (!arr) return '';
    arr.sort((a, b) => (/[\d]/.test(b) ? 1 : 0) - (/[\d]/.test(a) ? 1 : 0));
    return arr[0];
  }
  function extractConclusion(answer, stockItems) {
    const stockStr = (stockItems && stockItems[0]) ? (stockItems[0].name + (stockItems[0].code ? ' (' + stockItems[0].code + ')' : '')) : '';
    const marked = parseMarkers(answer);
    if (marked) { if (!marked.stock) marked.stock = stockStr; return marked; }
    const tbl = parseTableConc(answer);            // 表格源(问财结论表)最干净
    if (tbl) { if (!tbl.stock) tbl.stock = stockStr; return tbl; }
    // 正则兜底
    const t = stripEmbeds(answer || '');
    const period = (t.match(/(?:持股|持有|周期)[^。\n]{0,12}?(一周|半个?月|\d+\s*(?:天|日|周|个月))/) || [])[0] || '';
    return {
      stock: stockStr,
      buy: clipC(sentenceOf(t, '买点|买入价|买入区间|建仓|回踩[^。\\n]{0,8}买|低吸')),
      takeProfit: clipC(sentenceOf(t, '止盈|目标价|目标位|目标先?看')),
      stopLoss: clipC(sentenceOf(t, '止损|撤退价?|防守位?|跌破[^。\\n]{0,8}(?:清|走|撤|止)')),
      period: clipC(period),
      logic: clipC(sentenceOf(t, '一句话策略|核心逻辑|逻辑[:：]|之所以|受益于|催化')),
      risk: clipC(sentenceOf(t, '风险提示|需(?:要)?警惕|风险[:：]|若跌破|利空|不及预期')),
    };
  }

  // 独立可读页(新标签打开 / 历史重看 共用)
  function buildStandaloneHtml(question, answerMd, stocks, meta) {
    const chips = (stocks && stocks.length) ? '<div class="stocks">识别个股：' + stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(typeof s === 'string' ? s : s.name) + (i === 0 ? ' ·主推' : '') + '</span>').join('') + '</div>' : '';
    const sub = meta ? '<div class="sub">' + esc(meta) + '</div>' : '';
    const css = 'body{margin:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1f2937}.page{max-width:820px;margin:28px auto;background:#fff;border-radius:16px;box-shadow:0 6px 24px rgba(0,0,0,.08);overflow:hidden}.hd{background:linear-gradient(135deg,#1f2937,#111827);color:#fff;padding:16px 24px;font-weight:600}.q{padding:14px 24px;background:#f8fafc;border-bottom:1px solid #eef2f7;color:#334155;font-weight:600}.sub{padding:4px 24px 0;color:#94a3b8;font-size:12px}.ans{padding:18px 24px;font-size:15px;line-height:1.85}.ans h1,.ans h2,.ans h3,.ans h4{margin:18px 0 8px;color:#0f172a}.ans h2{font-size:17px}.ans p{margin:8px 0}.ans p.num{margin:12px 0 4px;color:#0f172a}.ans ul{padding-left:22px}.ans li{margin:4px 0}.ans strong{color:#0f172a}.ans code{background:#f1f5f9;padding:1px 5px;border-radius:4px}.ans .md-table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13.5px;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden}.ans .md-table th{background:#eef4ff;color:#0f172a;font-weight:700;text-align:left;padding:9px 12px;border-bottom:1px solid #e2e8f0}.ans .md-table td{padding:9px 12px;border-bottom:1px solid #eef2f7;vertical-align:top}.ans .md-table tr:last-child td{border-bottom:none}.ans .md-table td:first-child{font-weight:700;color:#475569;white-space:nowrap}.stocks{padding:14px 24px;border-top:1px solid #eef2f7;background:#fafbfc}.chip{display:inline-block;padding:4px 12px;margin:3px 6px 3px 0;border-radius:8px;font-size:13px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}.chip.hot{background:#dcfce7;color:#15803d;border-color:#86efac;font-weight:600}@media(prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}.page{background:#1e293b}.q{background:#172033;color:#cbd5e1;border-color:#334155}.ans h1,.ans h2,.ans h3,.ans h4,.ans strong{color:#f1f5f9}.ans code{background:#334155}.ans .md-table{border-color:#334155}.ans .md-table th{background:#16233f;color:#f1f5f9;border-color:#334155}.ans .md-table td{border-color:#26324a}.ans .md-table td:first-child{color:#a3b0c8}.stocks{background:#172033;border-color:#334155}.chip{background:#334155;color:#cbd5e1;border-color:#475569}}';
    return '<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>问财观点 · ' + esc(question).slice(0, 24) + '</title><style>' + css + '</style></head><body><div class="page"><div class="hd">💡 问财观点参考</div><div class="q">' + esc(question) + '</div>' + sub + '<div class="ans">' + mdRender(answerMd) + '</div>' + chips + '</div></body></html>';
  }

  async function readAimeSSE(resp, onUpdate) {
    if (!resp.ok) throw new Error('问财 HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '', answer = '', reasoning = '', traceId = '', agentMode = '', phase = 'connecting';
    const sources = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line.startsWith('data:')) continue;
        let f;
        try { f = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
        const bi = f.base_info;
        if (bi) { traceId = bi.trace_id || traceId; agentMode = bi.agent_mode || agentMode; }
        const ap = f.answer_path, sec = f.section;
        if (ap === 'progress/searching_for' && sec && sec.status === 'reasoning_summary_text.delta') {
          if (phase !== 'answering') phase = 'thinking';
          reasoning += (sec.info_texts || []).join('');
        } else if (ap === 'progress/searching_for' && phase !== 'answering') { phase = 'thinking'; }
        if (ap === 'other/openAnswer' && sec) {
          phase = 'answering';
          answer += sec.rich_text != null ? sec.rich_text : (sec.text_answer || '');
        }
        if (ap === 'extraInfo/sourceLink' && f.extra && f.extra.link_ids) {
          for (const s of f.extra.link_ids) if (s && sources.indexOf(s) < 0) sources.push(s);
        }
        if (onUpdate) onUpdate(phase, { answer, reasoning, sources });
      }
    }
    return { answer, reasoning, sources, traceId, agentMode };
  }

  // 完整跑一次(或续接会话): opts.sessionId 传入则复用(追问, 不再建会话)。返回带 sessionId。
  async function runAimeQuery(question, opts) {
    const { deep, getV, getUserId, onUpdate } = opts;
    const v = await getV();
    if (!v) throw new Error('未登录 iwencai(没取到 v cookie)');
    const userId = (await getUserId()) || '';
    const sessionId = opts.sessionId || genSessionId();
    const H = { 'Content-Type': 'application/json', 'accept': 'text/event-stream', 'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': v };
    if (!opts.sessionId) {
      try {
        await fetch('https://www.iwencai.com/gateway/aime/robotdata/user_session/add', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: question, session_id: sessionId }),
        });
      } catch (e) { /* 建会话失败不致命 */ }
    }
    const resp = await fetch('https://www.iwencai.com/gateway/aime/stream-query', {
      method: 'POST', credentials: 'include', headers: H, body: JSON.stringify(buildBody(opts.askText || question, sessionId, userId, deep)),
    });
    let res = await readAimeSSE(resp, onUpdate);
    if (res.answer.length < 50 && res.traceId) {
      try {
        const resp2 = await fetch('https://www.iwencai.com/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), { method: 'GET', credentials: 'include', headers: H });
        const r2 = await readAimeSSE(resp2, onUpdate);
        if (r2.answer.length > res.answer.length) res = r2;
      } catch (e) { /* 补拉失败用已有 */ }
    }
    res.sessionId = sessionId;
    return res;
  }

  // 查额度: 读 user-info, 返回各策略剩余次数。
  async function fetchQuota(getV) {
    const v = await getV();
    if (!v) return { ok: false, error: '未登录' };
    const r = await fetch('https://www.iwencai.com/gateway/aime/user-info?source=Ths_iwencai_Xuangu', {
      credentials: 'include', headers: { 'hexin-v': v, 'X-Source': 'Ths_iwencai_Xuangu' },
    });
    const j = await r.json();
    const d = (j && j.data) || {};
    const per = {}; (d.query_count_per_day || []).forEach((x) => { per[x.strategy] = x.remains; });
    return { ok: true, level: d.level, normal: per.normal_agent, deepInfer: per.deep_inference, aiData: per.ai_data,
             deepResearch: d.deep_research_query_times, leftTime: d.left_time };
  }

  root.WOP = { genSessionId, cmpVer, buildBody, esc, mdRender, stripEmbeds, buildStandaloneHtml, readAimeSSE, runAimeQuery, fetchQuota, FORMAT_SUFFIX, extractConclusion, cleanConcVal, extractPrice, stripMarkerLine };
})(typeof self !== 'undefined' ? self : this);
