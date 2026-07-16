// 问财观点扩展 — 前台(content)与后台(service worker)共用的 aime SSE 内核。
// content_scripts 里在 content.js 之前加载; background 里用 importScripts('common.js') 引入。
(function (root) {
  'use strict';

  function genSessionId() {
    let s = '';
    for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }

  // 构造 stream-query 请求体; deep=true 时挂 deep_research 事件(否则走普通 agent 省额度)。
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

  // 读一条 aime SSE 流: 累积 结论话术 + 思考过程 + 来源; onUpdate(phase, {answer,reasoning,sources}) 可选。
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

  // 完整跑一次: 建会话 → stream-query(SSE) → 必要时 stream-query2 补拉。
  // getV/getUserId 由各上下文提供(前台读 document.cookie, 后台读 chrome.cookies)。
  async function runAimeQuery(question, opts) {
    const { deep, getV, getUserId, onUpdate } = opts;
    const v = await getV();
    if (!v) throw new Error('未登录 iwencai(没取到 v cookie)');
    const userId = (await getUserId()) || '';
    const sessionId = genSessionId();
    const H = { 'Content-Type': 'application/json', 'accept': 'text/event-stream',
                'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': v };
    try {
      await fetch('https://www.iwencai.com/gateway/aime/robotdata/user_session/add', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: question, session_id: sessionId }),
      });
    } catch (e) { /* 建会话失败不致命 */ }

    const resp = await fetch('https://www.iwencai.com/gateway/aime/stream-query', {
      method: 'POST', credentials: 'include', headers: H,
      body: JSON.stringify(buildBody(question, sessionId, userId, deep)),
    });
    let res = await readAimeSSE(resp, onUpdate);

    if (res.answer.length < 50 && res.traceId) {
      try {
        const resp2 = await fetch('https://www.iwencai.com/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), {
          method: 'GET', credentials: 'include', headers: H,
        });
        const r2 = await readAimeSSE(resp2, onUpdate);
        if (r2.answer.length > res.answer.length) res = r2;
      } catch (e) { /* 补拉失败用已有 */ }
    }
    return res;
  }

  root.WOP = { genSessionId, buildBody, readAimeSSE, runAimeQuery };
})(typeof self !== 'undefined' ? self : this);
