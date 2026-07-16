// 问财观点扩展 — 前台(content)/后台(SW)/弹窗(popup)/查看页(viewer) 共用内核。
// content_scripts 里在 content.js 之前加载; background importScripts; popup/viewer 用 <script src>。
(function (root) {
  'use strict';

  function genSessionId() {
    let s = '';
    for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
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
  function mdRender(src) {
    const lines = (src || '').split('\n'); let html = '', inList = false, m;
    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      if (!line.trim()) { closeList(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeList(); const lv = m[1].length; html += '<h' + lv + '>' + inlineMd(m[2]) + '</h' + lv + '>'; }
      else if ((m = line.match(/^\s*[-•·]\s+(.*)$/))) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inlineMd(m[1]) + '</li>'; }
      else if ((m = line.match(/^\s*(\d{1,2})[.、]\s+(.*)$/))) { closeList(); html += '<p class="num"><strong>' + m[1] + '. </strong>' + inlineMd(m[2]) + '</p>'; }
      else { closeList(); html += '<p>' + inlineMd(line) + '</p>'; }
    }
    closeList(); return html;
  }

  // 独立可读页(新标签打开 / 历史重看 共用)
  function buildStandaloneHtml(question, answerMd, stocks, meta) {
    const chips = (stocks && stocks.length) ? '<div class="stocks">识别个股：' + stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(typeof s === 'string' ? s : s.name) + (i === 0 ? ' ·主推' : '') + '</span>').join('') + '</div>' : '';
    const sub = meta ? '<div class="sub">' + esc(meta) + '</div>' : '';
    const css = 'body{margin:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1f2937}.page{max-width:820px;margin:28px auto;background:#fff;border-radius:16px;box-shadow:0 6px 24px rgba(0,0,0,.08);overflow:hidden}.hd{background:linear-gradient(135deg,#1f2937,#111827);color:#fff;padding:16px 24px;font-weight:600}.q{padding:14px 24px;background:#f8fafc;border-bottom:1px solid #eef2f7;color:#334155;font-weight:600}.sub{padding:4px 24px 0;color:#94a3b8;font-size:12px}.ans{padding:18px 24px;font-size:15px;line-height:1.85}.ans h1,.ans h2,.ans h3,.ans h4{margin:18px 0 8px;color:#0f172a}.ans h2{font-size:17px}.ans p{margin:8px 0}.ans p.num{margin:12px 0 4px;color:#0f172a}.ans ul{padding-left:22px}.ans li{margin:4px 0}.ans strong{color:#0f172a}.ans code{background:#f1f5f9;padding:1px 5px;border-radius:4px}.stocks{padding:14px 24px;border-top:1px solid #eef2f7;background:#fafbfc}.chip{display:inline-block;padding:4px 12px;margin:3px 6px 3px 0;border-radius:8px;font-size:13px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}.chip.hot{background:#dcfce7;color:#15803d;border-color:#86efac;font-weight:600}@media(prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}.page{background:#1e293b}.q{background:#172033;color:#cbd5e1;border-color:#334155}.ans h1,.ans h2,.ans h3,.ans h4,.ans strong{color:#f1f5f9}.ans code{background:#334155}.stocks{background:#172033;border-color:#334155}.chip{background:#334155;color:#cbd5e1;border-color:#475569}}';
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
      method: 'POST', credentials: 'include', headers: H, body: JSON.stringify(buildBody(question, sessionId, userId, deep)),
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

  root.WOP = { genSessionId, buildBody, esc, mdRender, buildStandaloneHtml, readAimeSSE, runAimeQuery, fetchQuota };
})(typeof self !== 'undefined' ? self : this);
