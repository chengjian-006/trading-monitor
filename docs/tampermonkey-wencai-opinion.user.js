// ==UserScript==
// @name         问财观点上报 (chat智能调度)
// @namespace    guxiaocha-wencai-opinion
// @version      1.4
// @description  在 www.iwencai.com 登录态下, 口语问一句(chat 智能调度 aime stream-query SSE), Shadow DOM 浮层实时渲染答案(可折叠思考/真markdown/打字光标/可拖动最小化), 抽个股后上报股小察落「问财观点」。
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

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
    width: min(468px, 92vw); max-height: 82vh; display: flex; flex-direction: column;
    background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 18px 52px rgba(0,0,0,.30);
    border: 1px solid #e8ebf0; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    animation: pop .22s cubic-bezier(.2,.8,.3,1); }
  @keyframes pop { from { opacity: 0; transform: translateY(12px) scale(.98); } to { opacity: 1; transform: none; } }
  .wrap.min { max-height: none; width: auto; }
  .wrap.min .q, .wrap.min .rz, .wrap.min .body, .wrap.min .ft { display: none; }
  .hd { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px;
    background: linear-gradient(135deg,#1f2937,#111827); color: #fff; cursor: grab; user-select: none; }
  .hd:active { cursor: grabbing; }
  .hd .l { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14.5px; white-space: nowrap; }
  .pill { font-size: 11.5px; font-weight: 500; padding: 3px 10px; border-radius: 20px; background: #374151; color:#fff; white-space: nowrap; }
  .pill.blue { background:#2563eb; } .pill.amber { background:#b45309; }
  .pill.green { background:#15803d; } .pill.red { background:#b91c1c; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor; margin-right:5px; animation: blink 1s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
  .btns { display: flex; align-items: center; gap: 4px; }
  .ic { cursor: pointer; width: 24px; height: 24px; display:flex; align-items:center; justify-content:center;
    border-radius: 7px; color: #9aa4b2; font-size: 16px; line-height: 1; transition:.15s; }
  .ic:hover { color:#fff; background: rgba(255,255,255,.12); }
  .q { padding: 9px 16px; font-size: 12.5px; color: #64748b; background: #f8fafc; border-bottom: 1px solid #eef2f7; }
  .q b { color:#334155; }
  .rz { border-bottom: 1px solid #eef2f7; background:#fcfcfd; }
  .rz-h { padding: 8px 16px; font-size: 12.5px; color:#64748b; cursor: pointer; display:flex; align-items:center; gap:6px; user-select:none; }
  .rz-h:hover { color:#334155; }
  .rz-h .ar { transition: transform .18s; font-size:10px; }
  .rz.open .rz-h .ar { transform: rotate(90deg); }
  .rz-b { display:none; padding: 0 16px 10px; font-size: 12px; line-height: 1.7; color:#94a3b8; max-height: 180px; overflow-y:auto; white-space: pre-wrap; }
  .rz.open .rz-b { display:block; }
  .body { flex: 1; overflow-y: auto; padding: 6px 16px 14px; font-size: 14px; line-height: 1.8; color: #1f2937; word-break: break-word; }
  .body::-webkit-scrollbar, .rz-b::-webkit-scrollbar { width: 8px; }
  .body::-webkit-scrollbar-thumb, .rz-b::-webkit-scrollbar-thumb { background:#d1d9e3; border-radius: 8px; }
  .body h1,.body h2,.body h3,.body h4 { margin: 14px 0 6px; line-height:1.4; color:#0f172a; }
  .body h1 { font-size: 17px; } .body h2 { font-size: 15.5px; } .body h3,.body h4 { font-size: 14px; }
  .body p { margin: 6px 0; } .body p.num { margin: 10px 0 4px; color:#0f172a; }
  .body ul { margin: 4px 0; padding-left: 20px; } .body li { margin: 3px 0; }
  .body strong { color:#0f172a; font-weight: 700; }
  .body code { background:#f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
  .body .think { color:#94a3b8; }
  .cursor { display:inline-block; width:7px; height:15px; background:#2563eb; margin-left:2px; vertical-align:-2px; animation: blink .8s steps(1) infinite; border-radius:1px; }
  .ft { padding: 11px 16px; border-top: 1px solid #eef2f7; font-size: 12.5px; color: #64748b; background: #fafbfc; }
  .ft .lbl { color:#94a3b8; }
  .chip { display:inline-block; padding: 3px 10px; margin: 3px 6px 3px 0; border-radius: 7px; font-size: 12.5px;
    background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; cursor:default; }
  .chip.hot { background:#dcfce7; color:#15803d; border-color:#86efac; font-weight:600; }
  .src { margin-top: 6px; font-size: 11.5px; color:#94a3b8; }
  .btn { display:inline-block; margin-top:10px; padding: 8px 16px; background:#1f2937; color:#fff; border-radius: 9px;
    text-decoration:none; font-size: 12.5px; transition:.15s; } .btn:hover { background:#111827; }
  `;

  let P = null;
  function panel() {
    if (P && P.host.isConnected) return P;
    const host = document.createElement('div');
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML = `<style>${CSS}</style>
      <div class="wrap" id="wrap">
        <div class="hd" id="hd">
          <div class="l">💡 问财观点 <span class="pill" id="pill">准备中</span></div>
          <div class="btns"><div class="ic" id="min" title="最小化">–</div><div class="ic" id="x" title="关闭">×</div></div>
        </div>
        <div class="q" id="q"></div>
        <div class="rz" id="rz" style="display:none"><div class="rz-h" id="rzh"><span class="ar">▸</span>思考过程</div><div class="rz-b" id="rzb"></div></div>
        <div class="body" id="body">…</div>
        <div class="ft" id="ft" style="display:none"></div>
      </div>`;
    (document.documentElement || document.body).appendChild(host);
    const $ = (id) => sh.getElementById(id);
    const wrap = $('wrap');
    $('x').onclick = () => host.remove();
    $('min').onclick = (e) => { e.stopPropagation(); wrap.classList.toggle('min'); $('min').textContent = wrap.classList.contains('min') ? '▢' : '–'; };
    $('rzh').onclick = () => $('rz').classList.toggle('open');
    // 拖动(表头为把手, 首次拖动把 right/bottom 定位转成 left/top)
    let drag = false, sx = 0, sy = 0, ox = 0, oy = 0;
    $('hd').addEventListener('mousedown', (e) => {
      if (e.target.closest('.btns')) return;
      drag = true; const r = wrap.getBoundingClientRect();
      wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px';
      wrap.style.right = 'auto'; wrap.style.bottom = 'auto';
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (!drag) return;
      wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px';
      wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px';
    });
    window.addEventListener('mouseup', () => { drag = false; });
    P = { host, wrap, pill: $('pill'), q: $('q'), rz: $('rz'), rzb: $('rzb'), body: $('body'), ft: $('ft') };
    return P;
  }

  // 轻量 markdown 渲染(标题/无序列表/编号段/加粗/代码), 流式部分文本也能稳渲染。
  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function inlineMd(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
  function mdRender(src) {
    const lines = (src || '').split('\n');
    let html = '', inList = false, m;
    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      if (!line.trim()) { closeList(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeList(); const lv = m[1].length; html += '<h' + lv + '>' + inlineMd(m[2]) + '</h' + lv + '>'; }
      else if ((m = line.match(/^\s*[-•·]\s+(.*)$/))) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inlineMd(m[1]) + '</li>'; }
      else if ((m = line.match(/^\s*(\d{1,2})[.、]\s+(.*)$/))) { closeList(); html += '<p class="num"><strong>' + m[1] + '. </strong>' + inlineMd(m[2]) + '</p>'; }
      else { closeList(); html += '<p>' + inlineMd(line) + '</p>'; }
    }
    closeList();
    return html;
  }

  function setStage(text, cls, live) { const p = panel(); p.pill.className = 'pill ' + (cls || ''); p.pill.innerHTML = (live ? '<span class="dot"></span>' : '') + text; }
  function setQuestion(q) { panel().q.innerHTML = '问：<b>' + esc(q) + '</b>'; }
  function setReasoning(text) {
    const p = panel();
    if (!text) { p.rz.style.display = 'none'; return; }
    p.rz.style.display = 'block'; p.rzb.textContent = text;
  }
  function setBodyText(t) { const p = panel(); p.body.innerHTML = '<span class="think">' + esc(t) + '</span>'; }
  function setBodyMd(md, cursor) {
    const p = panel(); const near = p.body.scrollHeight - p.body.scrollTop - p.body.clientHeight < 70;
    p.body.innerHTML = mdRender(md) + (cursor ? '<span class="cursor"></span>' : '');
    if (near) p.body.scrollTop = p.body.scrollHeight;
  }
  function setFoot(html) { const p = panel(); p.ft.style.display = 'block'; p.ft.innerHTML = html; }

  function getCookie(name) { const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)'); return m ? m.pop() : ''; }
  function genSessionId() { let s = ''; for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16); return s; }

  function gmPost(path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: SERVER_URL + path, headers: { 'Content-Type': 'application/json' },
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

  // 读 aime SSE 流: 累积 结论话术 + 思考过程 + 来源; onUpdate(phase, state) 实时回报。
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

  const V_HEADERS = () => ({
    'Content-Type': 'application/json', 'accept': 'text/event-stream',
    'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': getCookie('v'),
  });

  function onUpdate(phase, s) {
    if (s.reasoning) setReasoning(s.reasoning);
    if (phase === 'answering' && s.answer) {
      setStage('接收中 · ' + s.answer.length + '字', 'blue', true);
      setBodyMd(s.answer, true);
    } else if (phase === 'thinking' && !s.answer) {
      setStage('思考中', 'amber', true);
      setBodyText('问财正在分析你的需求…（智能调度深度推理，约 10~20 秒，随后逐字给出结论；思考过程可点上方展开）');
    }
  }

  async function runAsk(question) {
    setQuestion(question); setStage('准备中'); setReasoning(''); setBodyText('…'); panel().ft.style.display = 'none';
    if (!getCookie('v')) { setStage('未登录', 'red'); setBodyText('没取到 v cookie，请确认已登录 iwencai。'); return; }
    if (INGEST_TOKEN === 'PUT_YOUR_TOKEN_HERE') { setStage('未配置', 'red'); setBodyText('请先在脚本里填 INGEST_TOKEN。'); return; }
    const userId = getCookie('userid') || '';
    const sessionId = genSessionId();

    setStage('建会话', '', true); setBodyText('正在建立会话…');
    try {
      await fetch('/gateway/aime/robotdata/user_session/add', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: question, session_id: sessionId }),
      });
    } catch (e) { /* 建会话失败不致命 */ }

    setStage('提问中', '', true); setBodyText('正在把问题提交给问财…');
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
    } catch (e) { setStage('失败', 'red'); setBodyText('问财 stream-query 失败：' + e.message); return; }

    if (res.answer.length < 50 && res.traceId) {
      setStage('补拉', '', true);
      try {
        const resp2 = await fetch('/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), {
          method: 'GET', credentials: 'include', headers: V_HEADERS(),
        });
        const r2 = await readAimeSSE(resp2, onUpdate);
        if (r2.answer.length > res.answer.length) res = r2;
      } catch (e) { /* 补全失败就用已有的 */ }
    }

    if (!res.answer.trim()) { setStage('无结果', 'red'); setBodyText('没抓到答案话术（可能被风控，或问题被判为非推荐意图）。'); return; }

    setStage('上报中', 'blue', true); setBodyMd(res.answer, false);
    setFoot('<span class="lbl">⏳ 正在上报并识别个股…</span>');
    try {
      const r = await gmPost('/api/wencai/opinion', {
        token: INGEST_TOKEN, question: question, answer_text: res.answer,
        trace_id: res.traceId, agent_mode: res.agentMode || (DEEP_RESEARCH ? 'deep_research' : 'normal'),
      });
      setStage('✓ 已存档', 'green');
      const stocks = r.stocks || [];
      const chips = stocks.length
        ? stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(s) + (i === 0 ? ' ·主推' : '') + '</span>').join('')
        : '<span style="color:#9ca3af">未识别出具体个股（纯观点/多票对比，见上方原文）</span>';
      const src = (res.sources && res.sources.length)
        ? '<div class="src">来源维度：' + res.sources.slice(0, 6).map(esc).join(' · ') + '</div>' : '';
      setFoot('<span class="lbl">识别个股：</span>' + chips + src
        + '<br><a class="btn" href="' + SERVER_URL + '/wencai-opinion" target="_blank">去「问财观点」页查看 →</a>');
    } catch (e) { setStage('上报失败', 'red'); setFoot('上报失败：' + e.message); }
  }

  GM_registerMenuCommand('① 问一句 → 上报观点', () => {
    const q = prompt('输入要问问财的口语问题:', DEFAULT_Q);
    if (q && q.trim()) runAsk(q.trim());
  });
  GM_registerMenuCommand('② 用预置问题直接上报', () => runAsk(DEFAULT_Q));

  console.log('%c[问财观点] 已加载 v1.4 (Perplexity式浮层). 菜单手动问一句并上报。deep_research=' + DEEP_RESEARCH, 'color:blue;font-weight:bold');
})();
