// ==UserScript==
// @name         问财观点上报 (chat智能调度)
// @namespace    guxiaocha-wencai-opinion
// @version      1.2
// @description  在 www.iwencai.com 登录态下, 用口语问一句(走 chat 智能调度 aime stream-query SSE), 右下角浮层实时显示答案话术全文, 抽出个股后上报股小察落「问财观点」。
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

  const st = (o) => Object.entries(o).map(([k, v]) => k + ':' + v).join(';');

  // ---- 右下角浮层面板(实时看答案话术) ----
  let P = null;
  function panel() {
    if (P && document.body.contains(P.root)) return P;
    const root = document.createElement('div');
    root.style.cssText = st({
      position: 'fixed', right: '16px', bottom: '16px', 'z-index': '2147483647',
      width: 'min(440px,92vw)', 'max-height': '78vh', display: 'flex', 'flex-direction': 'column',
      background: '#ffffff', color: '#1f2937', 'border-radius': '14px', overflow: 'hidden',
      'box-shadow': '0 12px 40px rgba(0,0,0,.28)', border: '1px solid #e5e7eb',
      'font-family': '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif',
    });

    const header = document.createElement('div');
    header.style.cssText = st({
      display: 'flex', 'align-items': 'center', 'justify-content': 'space-between',
      padding: '10px 14px', background: '#1f2937', color: '#fff', 'flex-shrink': '0',
    });
    const title = document.createElement('div');
    title.style.cssText = st({ 'font-weight': '600', 'font-size': '14px' });
    title.textContent = '问财观点';
    const stage = document.createElement('span');
    stage.style.cssText = st({ 'font-size': '12px', padding: '2px 10px', 'border-radius': '20px', background: '#374151', 'margin-left': '8px', 'font-weight': '400' });
    stage.textContent = '准备中';
    const left = document.createElement('div');
    left.style.cssText = st({ display: 'flex', 'align-items': 'center' });
    left.append(title, stage);
    const close = document.createElement('div');
    close.textContent = '×';
    close.style.cssText = st({ cursor: 'pointer', 'font-size': '20px', 'line-height': '1', padding: '0 4px', color: '#cbd5e1' });
    close.onclick = () => root.remove();
    header.append(left, close);

    const qEl = document.createElement('div');
    qEl.style.cssText = st({ padding: '8px 14px', 'font-size': '12px', color: '#6b7280', background: '#f9fafb', 'border-bottom': '1px solid #eef2f7', 'flex-shrink': '0' });

    const body = document.createElement('div');
    body.style.cssText = st({ padding: '12px 14px', 'font-size': '13.5px', 'line-height': '1.75', color: '#1f2937', 'overflow-y': 'auto', flex: '1', 'white-space': 'normal', 'word-break': 'break-word', 'min-height': '80px' });
    body.textContent = '…';

    const foot = document.createElement('div');
    foot.style.cssText = st({ padding: '10px 14px', 'border-top': '1px solid #eef2f7', 'font-size': '12px', color: '#6b7280', background: '#fafbfc', 'flex-shrink': '0' });

    root.append(header, qEl, body, foot);
    (document.documentElement || document.body).appendChild(root);
    P = { root, stage, qEl, body, foot };
    return P;
  }

  function mdLite(t) {
    const esc = (t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
  }
  function setStage(text, bg) { const p = panel(); p.stage.textContent = text; p.stage.style.background = bg || '#374151'; }
  function setQuestion(q) { panel().qEl.textContent = '问: ' + q; }
  function setBody(html, isText) {
    const p = panel(); const atBottom = p.body.scrollHeight - p.body.scrollTop - p.body.clientHeight < 40;
    if (isText) p.body.textContent = html; else p.body.innerHTML = html;
    if (atBottom) p.body.scrollTop = p.body.scrollHeight;
  }
  function setFoot(html) { panel().foot.innerHTML = html; }

  function getCookie(name) { const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)'); return m ? m.pop() : ''; }
  function genSessionId() { let s = ''; for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16); return s; }

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

  // 读 aime SSE 流: 累积 openAnswer 话术 + 抓 base_info; onUpdate(phase, answer) 实时回报。
  async function readAimeSSE(resp, onUpdate) {
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
        if (onUpdate) onUpdate(phase, answer);
      }
    }
    return { answer: answer, traceId: traceId, agentMode: agentMode };
  }

  const V_HEADERS = () => ({
    'Content-Type': 'application/json', 'accept': 'text/event-stream',
    'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': getCookie('v'),
  });

  function onUpdate(phase, answer) {
    if (phase === 'thinking' && !answer) {
      setStage('思考中', '#b45309');
      setBody('🤔 问财正在分析…（智能调度深度推理，约 10~20 秒，稍候会逐字吐出结论）', true);
    } else if (phase === 'answering') {
      setStage('接收中 ' + answer.length + '字', '#2563eb');
      setBody(mdLite(answer));
    }
  }

  async function runAsk(question) {
    setQuestion(question); setStage('准备中', '#374151'); setBody('…', true); setFoot('');
    if (!getCookie('v')) { setStage('未登录', '#b91c1c'); setBody('✗ 没取到 v cookie，请确认已登录 iwencai。', true); return; }
    if (INGEST_TOKEN === 'PUT_YOUR_TOKEN_HERE') { setStage('未配置', '#b91c1c'); setBody('✗ 请先在脚本里填 INGEST_TOKEN。', true); return; }
    const userId = getCookie('userid') || '';
    const sessionId = genSessionId();

    setStage('建会话', '#374151'); setBody('正在建立会话…', true);
    try {
      await fetch('/gateway/aime/robotdata/user_session/add', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: question, session_id: sessionId }),
      });
    } catch (e) { /* 建会话失败不致命 */ }

    setStage('提问中', '#374151'); setBody('正在提交问题给问财…', true);
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
      res = await readAimeSSE(resp, onUpdate);
    } catch (e) { setStage('失败', '#b91c1c'); setBody('✗ 问财 stream-query 失败：' + e.message, true); return; }

    if (res.answer.length < 50 && res.traceId) {
      setStage('补拉', '#374151');
      try {
        const resp2 = await fetch('/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), {
          method: 'GET', credentials: 'include', headers: V_HEADERS(),
        });
        const res2 = await readAimeSSE(resp2, onUpdate);
        if (res2.answer.length > res.answer.length) { res.answer = res2.answer; res.traceId = res2.traceId || res.traceId; res.agentMode = res.agentMode || res2.agentMode; }
      } catch (e) { /* 补全失败就用已有的 */ }
    }

    if (!res.answer.trim()) { setStage('无结果', '#b91c1c'); setBody('✗ 没抓到答案话术（可能被风控，或问题被判为非推荐意图）。', true); return; }

    setStage('上报中', '#2563eb');
    setFoot('⏳ 正在上报股小察并识别个股…');
    try {
      const r = await gmPost('/api/wencai/opinion', {
        token: INGEST_TOKEN, question: question, answer_text: res.answer,
        trace_id: res.traceId, agent_mode: res.agentMode || (DEEP_RESEARCH ? 'deep_research' : 'normal'),
      });
      setStage('✓ 已存档', '#15803d');
      setBody(mdLite(res.answer));
      const stocks = r.stocks || [];
      const chips = stocks.length
        ? stocks.map((s, i) => '<span style="' + st({ display: 'inline-block', padding: '2px 8px', margin: '2px 4px 2px 0', 'border-radius': '6px', 'font-size': '12px', background: i === 0 ? '#dcfce7' : '#f1f5f9', color: i === 0 ? '#15803d' : '#475569', border: '1px solid ' + (i === 0 ? '#86efac' : '#e2e8f0') }) + '">' + s + (i === 0 ? ' ·主推' : '') + '</span>').join('')
        : '<span style="color:#9ca3af">未识别出具体个股（纯观点/多票对比，见上方原文）</span>';
      setFoot('识别个股：' + chips
        + '<div style="margin-top:8px"><a href="' + SERVER_URL + '/wencai-opinion" target="_blank" style="'
        + st({ display: 'inline-block', padding: '6px 14px', background: '#1f2937', color: '#fff', 'border-radius': '8px', 'text-decoration': 'none', 'font-size': '12px' })
        + '">去「问财观点」页查看 →</a></div>');
    } catch (e) { setStage('上报失败', '#b91c1c'); setFoot('✗ 上报失败：' + e.message); }
  }

  GM_registerMenuCommand('① 问一句 → 上报观点', () => {
    const q = prompt('输入要问问财的口语问题:', DEFAULT_Q);
    if (q && q.trim()) runAsk(q.trim());
  });
  GM_registerMenuCommand('② 用预置问题直接上报', () => runAsk(DEFAULT_Q));

  console.log('%c[问财观点] 已加载 v1.2, 菜单手动问一句并上报。deep_research=' + DEEP_RESEARCH, 'color:blue;font-weight:bold');
})();
