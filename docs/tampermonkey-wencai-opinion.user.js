// ==UserScript==
// @name         问财观点上报 (chat智能调度)
// @namespace    guxiaocha-wencai-opinion
// @version      1.0
// @description  在 www.iwencai.com 登录态下, 用口语问一句(走 chat 智能调度 aime stream-query SSE), 把整段投顾话术上报股小察落「问财观点」。菜单手动触发。
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

  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  }

  // 32 位十六进制 session_id(不依赖 crypto, 兼容性优先)
  function genSessionId() {
    let s = '';
    for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }

  // 跨域 POST 到股小察服务器(GM_xmlhttpRequest 绕同源/CSP)
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

  // 读一条 aime SSE 流: 累积 openAnswer 话术 + 抓 base_info(trace_id / agent_mode)。
  async function readAimeSSE(resp) {
    if (!resp.ok) throw new Error('问财 HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '', answer = '', traceId = '', agentMode = '';
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
        // 最终答案话术: answer_path=other/openAnswer, 逐 token 的 rich_text/text_answer 增量
        if (f.answer_path === 'other/openAnswer' && f.section) {
          const t = f.section.rich_text != null ? f.section.rich_text : (f.section.text_answer || '');
          answer += t;
        }
      }
    }
    return { answer: answer, traceId: traceId, agentMode: agentMode };
  }

  const V_HEADERS = () => ({
    'Content-Type': 'application/json', 'accept': 'text/event-stream',
    'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': getCookie('v'),
  });

  async function runAsk(question) {
    if (!getCookie('v')) { alert('⚠️ 没取到 v cookie, 请确认已登录 iwencai'); return; }
    if (INGEST_TOKEN === 'PUT_YOUR_TOKEN_HERE') { alert('请先在脚本里填 INGEST_TOKEN'); return; }
    const userId = getCookie('userid') || '';
    const sessionId = genSessionId();

    // 1. 建会话
    try {
      await fetch('/gateway/aime/robotdata/user_session/add', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: question, session_id: sessionId }),
      });
    } catch (e) { /* 建会话失败不致命, 继续 */ }

    // 2. 提交问题(stream-query, SSE)
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
      res = await readAimeSSE(resp);
    } catch (e) { alert('问财 stream-query 失败: ' + e.message); return; }

    // 3. 话术太短(答案可能走 async 生成) → 用 trace_id 拉 stream-query2 补全
    if (res.answer.length < 50 && res.traceId) {
      try {
        const resp2 = await fetch('/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), {
          method: 'GET', credentials: 'include', headers: V_HEADERS(),
        });
        const res2 = await readAimeSSE(resp2);
        if (res2.answer.length > res.answer.length) { res.answer = res2.answer; res.traceId = res2.traceId || res.traceId; res.agentMode = res.agentMode || res2.agentMode; }
      } catch (e) { /* 补全失败就用已有的 */ }
    }

    if (!res.answer.trim()) { alert('没抓到答案话术(可能被风控或问题被判为非推荐意图)。'); return; }

    // 4. 上报股小察
    try {
      const r = await gmPost('/api/wencai/opinion', {
        token: INGEST_TOKEN, question: question, answer_text: res.answer,
        trace_id: res.traceId, agent_mode: res.agentMode || (DEEP_RESEARCH ? 'deep_research' : 'normal'),
      });
      const stks = (r.stocks || []).join('、') || '(未识别出个股)';
      alert('✓ 已上报问财观点!\n话术 ' + res.answer.length + ' 字\n识别个股: ' + stks + '\n\n去股小察「问财观点」页查看。');
    } catch (e) { alert('上报失败: ' + e.message); }
  }

  GM_registerMenuCommand('① 问一句 → 上报观点', () => {
    const q = prompt('输入要问问财的口语问题:', DEFAULT_Q);
    if (q && q.trim()) runAsk(q.trim());
  });
  GM_registerMenuCommand('② 用预置问题直接上报', () => runAsk(DEFAULT_Q));

  console.log('%c[问财观点] 已加载, 菜单可手动问一句并上报。deep_research=' + DEEP_RESEARCH, 'color:blue;font-weight:bold');
})();
