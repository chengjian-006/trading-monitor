// ==UserScript==
// @name         问财观点上报 (chat智能调度)
// @namespace    guxiaocha-wencai-opinion
// @version      1.1
// @description  在 www.iwencai.com 登录态下, 用口语问一句(走 chat 智能调度 aime stream-query SSE), 把整段投顾话术上报股小察落「问财观点」。菜单手动触发, 右上角实时状态条显示进展。
// @match        https://www.iwencai.com/*
// @match        http://www.iwencai.com/*
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      124.71.75.5
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ============ 需要你填/可调 ============
  const SERVER_URL = 'http://124.71.75.5';   // 股小察后端(本地调试填 http://localhost:8000)
  const INGEST_TOKEN = 'PUT_YOUR_TOKEN_HERE';   // 同 ingest 共享密钥(填 config.wencai_screening.ingest_token, 与 ingest 脚本一致)
  const DEEP_RESEARCH = false;   // false=普通agent(每天188次) / true=深度研究(每天约10次, 更深但卡额度)
  const DEFAULT_Q = '给我推荐一只股票,目前处于买入区间,持股一周以内,盈利7%以上';
  // ======================================

  // ---- 右上角实时状态条(单个 div + 强内联样式, 不与页面 CSS 纠缠) ----
  function statusEl() {
    let el = document.getElementById('gxc-op-status');
    if (!el) {
      el = document.createElement('div');
      el.id = 'gxc-op-status';
      el.style.cssText = [
        'position:fixed', 'top:16px', 'right:16px', 'z-index:2147483647',
        'max-width:380px', 'min-width:220px', 'background:#1f2937', 'color:#fff',
        'padding:12px 16px', 'border-radius:10px', 'font-size:13px', 'line-height:1.7',
        'box-shadow:0 8px 28px rgba(0,0,0,.4)', 'white-space:pre-line', 'cursor:pointer',
        'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      ].join(';');
      el.title = '点击关闭';
      el.addEventListener('click', () => el.remove());
      (document.documentElement || document.body).appendChild(el);
    }
    return el;
  }
  function setStatus(text, bg) {
    const el = statusEl();
    el.textContent = text;
    el.style.background = bg || '#1f2937';
    console.log('%c[问财观点] ' + text.replace(/\n/g, ' '), 'color:#2563eb');
  }

  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  }

  function genSessionId() {
    let s = '';
    for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }

  function gmPost(path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: SERVER_URL + path,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify(payload), timeout: 20000,
        onload: (r) => {
          try {
            const j = JSON.parse(r.responseText);
            if (r.status >= 200 && r.status < 300) resolve(j);
            else reject(new Error('HTTP ' + r.status + ': ' + (j.detail || r.responseText)));
          } catch (e) { reject(new Error('HTTP ' + r.status + ' 非JSON: ' + r.responseText.slice(0, 200))); }
        },
        onerror: () => reject(new Error('网络错误(检查 SERVER_URL / @connect)')),
        ontimeout: () => reject(new Error('上报超时')),
      });
    });
  }

  // 读 aime SSE 流: 累积 openAnswer 话术 + 抓 base_info; onProgress(phase, chars) 回报进展。
  async function readAimeSSE(resp, onProgress) {
    if (!resp.ok) throw new Error('问财 HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '', answer = '', traceId = '', agentMode = '', phase = 'connecting';
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
        if (f.answer_path === 'progress/searching_for' && phase !== 'answering') phase = 'thinking';
        if (f.answer_path === 'other/openAnswer' && f.section) {
          phase = 'answering';
          const t = f.section.rich_text != null ? f.section.rich_text : (f.section.text_answer || '');
          answer += t;
        }
        if (onProgress) onProgress(phase, answer.length);
      }
    }
    return { answer: answer, traceId: traceId, agentMode: agentMode };
  }

  const V_HEADERS = () => ({
    'Content-Type': 'application/json', 'accept': 'text/event-stream',
    'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': getCookie('v'),
  });

  function onProgress(phase, n) {
    if (phase === 'thinking') setStatus('②/④ 问财思考中…\n(智能调度深度推理, 约 10~20 秒)');
    else if (phase === 'answering') setStatus('③/④ 正在接收答案… ' + n + ' 字');
  }

  async function runAsk(question) {
    setStatus('准备中…\n' + question);
    if (!getCookie('v')) { setStatus('✗ 没取到 v cookie\n请确认已登录 iwencai', '#b91c1c'); return; }
    if (INGEST_TOKEN === 'PUT_YOUR_TOKEN_HERE') { setStatus('✗ 请先在脚本里填 INGEST_TOKEN', '#b91c1c'); return; }
    const userId = getCookie('userid') || '';
    const sessionId = genSessionId();

    // 1. 建会话
    setStatus('①/④ 建会话中…');
    try {
      await fetch('/gateway/aime/robotdata/user_session/add', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: question, session_id: sessionId }),
      });
    } catch (e) { /* 建会话失败不致命 */ }

    // 2. 提交问题(stream-query, SSE)
    setStatus('②/④ 提交问题中…');
    const events = [{ event_name: 'auto_agent', event_type: 'user_input' }];
    if (DEEP_RESEARCH) events.push({ event_name: 'ab_test', event_type: 'front_trigger', content: { deep_research: 1 } });
    const body = {
      version: '3.4.1', session_id: sessionId, user_id: userId, source: 'Ths_iwencai_Xuangu',
      input_type: 'typewrite', question: question, deviceType: 'browser',
      add_info: { merge_repeat: true, async_generate_data: true, show_searching: true,
                  urp: { is_lowcode: 1, component_version: '1.1.4' } },
      entity_info: {}, events: events,
    };
    let res;
    try {
      const resp = await fetch('/gateway/aime/stream-query', {
        method: 'POST', credentials: 'include', headers: V_HEADERS(), body: JSON.stringify(body),
      });
      res = await readAimeSSE(resp, onProgress);
    } catch (e) { setStatus('✗ 问财 stream-query 失败:\n' + e.message, '#b91c1c'); return; }

    // 3. 话术太短 → 用 trace_id 拉 stream-query2 补全
    if (res.answer.length < 50 && res.traceId) {
      setStatus('③/④ 答案较短, 补拉完整流…');
      try {
        const resp2 = await fetch('/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), {
          method: 'GET', credentials: 'include', headers: V_HEADERS(),
        });
        const res2 = await readAimeSSE(resp2, onProgress);
        if (res2.answer.length > res.answer.length) { res.answer = res2.answer; res.traceId = res2.traceId || res.traceId; res.agentMode = res.agentMode || res2.agentMode; }
      } catch (e) { /* 补全失败就用已有的 */ }
    }

    if (!res.answer.trim()) { setStatus('✗ 没抓到答案话术\n(可能被风控或问题被判为非推荐意图)', '#b91c1c'); return; }

    // 4. 上报股小察
    setStatus('④/④ 上报股小察中… (话术 ' + res.answer.length + ' 字)');
    try {
      const r = await gmPost('/api/wencai/opinion', {
        token: INGEST_TOKEN, question: question, answer_text: res.answer,
        trace_id: res.traceId, agent_mode: res.agentMode || (DEEP_RESEARCH ? 'deep_research' : 'normal'),
      });
      const stks = (r.stocks || []).join('、') || '(未识别出具体个股)';
      setStatus('✓ 已上报问财观点!\n话术 ' + res.answer.length + ' 字 · 识别个股: ' + stks
        + '\n\n去股小察「问财观点」页查看\n(点此关闭)', '#15803d');
    } catch (e) { setStatus('✗ 上报失败:\n' + e.message, '#b91c1c'); }
  }

  GM_registerMenuCommand('① 问一句 → 上报观点', () => {
    const q = prompt('输入要问问财的口语问题:', DEFAULT_Q);
    if (q && q.trim()) runAsk(q.trim());
  });
  GM_registerMenuCommand('② 用预置问题直接上报', () => runAsk(DEFAULT_Q));

  console.log('%c[问财观点] 已加载, 菜单可手动问一句并上报。deep_research=' + DEEP_RESEARCH, 'color:blue;font-weight:bold');
})();
