// 问财观点扩展 · 前台内容脚本 (仅 www.iwencai.com)
// 悬浮💡按钮 → 菜单(预置问题/自定义) → 实时浮层面板(思考/流式markdown/个股/新标签打开) → 上报。
(function () {
  'use strict';
  const WOP = self.WOP;

  const DEFAULTS = {
    serverUrl: 'http://124.71.75.5', token: '', uploader: '',
    presets: [
      '给我推荐一只股票,目前处于买入区间,持股一周以内,盈利7%以上',
      '当前有哪些板块在起, 适合低吸跟随?',
      '今日大盘走势与操作建议',
    ],
    deepResearch: false, autoUpload: true, onlyWithStock: false,
    schedule: { enabled: false, times: ['09:35', '13:05'], questions: [] },
  };
  const getSettings = () => new Promise((res) => chrome.storage.sync.get(DEFAULTS, res));
  const getCookie = (name) => { const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)'); return m ? m.pop() : ''; };

  function uploadOpinion(serverUrl, payload) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: 'upload', url: serverUrl + '/api/wencai/opinion', payload }, (resp) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (resp && resp.ok) resolve(resp.data || {});
        else reject(new Error((resp && (resp.error || (resp.data && resp.data.detail) || ('HTTP ' + (resp.status || '?')))) || '上报失败'));
      });
    });
  }

  // ---------- 样式 ----------
  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; right: 20px; bottom: 84px; z-index: 2147483646;
    width: min(468px, 92vw); max-height: 78vh; display: flex; flex-direction: column;
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
  .pill.blue { background:#2563eb; } .pill.amber { background:#b45309; } .pill.green { background:#15803d; } .pill.red { background:#b91c1c; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor; margin-right:5px; animation: blink 1s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
  .btns { display: flex; align-items: center; gap: 4px; }
  .ic { cursor: pointer; width: 24px; height: 24px; display:flex; align-items:center; justify-content:center; border-radius: 7px; color: #9aa4b2; font-size: 16px; transition:.15s; }
  .ic:hover { color:#fff; background: rgba(255,255,255,.12); }
  .q { padding: 9px 16px; font-size: 12.5px; color: #64748b; background: #f8fafc; border-bottom: 1px solid #eef2f7; }
  .q b { color:#334155; }
  .rz { border-bottom: 1px solid #eef2f7; background:#fcfcfd; }
  .rz-h { padding: 8px 16px; font-size: 12.5px; color:#64748b; cursor: pointer; display:flex; align-items:center; gap:6px; user-select:none; }
  .rz-h:hover { color:#334155; } .rz-h .ar { transition: transform .18s; font-size:10px; } .rz.open .rz-h .ar { transform: rotate(90deg); }
  .rz-b { display:none; padding: 0 16px 10px; font-size: 12px; line-height: 1.7; color:#94a3b8; max-height: 170px; overflow-y:auto; white-space: pre-wrap; }
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
  .chip { display:inline-block; padding: 3px 10px; margin: 3px 6px 3px 0; border-radius: 7px; font-size: 12.5px; background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; }
  .chip.hot { background:#dcfce7; color:#15803d; border-color:#86efac; font-weight:600; }
  .src { margin-top: 6px; font-size: 11.5px; color:#94a3b8; }
  .acts { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .btn { display:inline-block; padding: 8px 14px; background:#1f2937; color:#fff; border-radius: 9px; text-decoration:none; font-size: 12.5px; cursor:pointer; transition:.15s; border:none; } .btn:hover { background:#111827; }
  .btn.ghost { background:#fff; color:#1f2937; border:1px solid #d1d9e3; } .btn.ghost:hover { background:#f1f5f9; }
  `;
  const LCSS = `
  :host { all: initial; }
  .lb { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; width: 48px; height: 48px; border-radius: 50%;
    background: linear-gradient(135deg,#2563eb,#1e40af); color:#fff; font-size: 22px; display:flex; align-items:center; justify-content:center;
    cursor: pointer; box-shadow: 0 8px 24px rgba(37,99,235,.4); user-select:none; transition: transform .15s; font-family: sans-serif; }
  .lb:hover { transform: scale(1.08); }
  .menu { position: fixed; right: 20px; bottom: 78px; z-index: 2147483647; display:none; flex-direction: column;
    background:#fff; border-radius: 12px; box-shadow: 0 12px 36px rgba(0,0,0,.24); border:1px solid #e8ebf0; overflow:hidden; min-width: 240px; max-width: 320px;
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; animation: pop .18s ease; }
  @keyframes pop { from { opacity:0; transform: translateY(8px); } to { opacity:1; transform:none; } }
  .menu.show { display:flex; }
  .mi { padding: 10px 14px; font-size: 13px; color:#1f2937; cursor: pointer; border-bottom: 1px solid #f1f5f9; white-space: normal; line-height:1.5; }
  .mi:last-child { border-bottom: none; }
  .mi:hover { background:#f8fafc; }
  .mi.cust { color:#2563eb; font-weight: 500; }
  .mhd { padding: 8px 14px; font-size: 11.5px; color:#94a3b8; background:#f8fafc; border-bottom:1px solid #eef2f7; }
  `;

  // ---------- 面板 ----------
  let P = null;
  function panel() {
    if (P && P.host.isConnected) return P;
    const host = document.createElement('div');
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML = `<style>${CSS}</style>
      <div class="wrap" id="wrap">
        <div class="hd" id="hd"><div class="l">💡 问财观点 <span class="pill" id="pill">准备中</span></div>
          <div class="btns"><div class="ic" id="min" title="最小化">–</div><div class="ic" id="x" title="关闭">×</div></div></div>
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
    let drag = false, sx = 0, sy = 0, ox = 0, oy = 0;
    $('hd').addEventListener('mousedown', (e) => {
      if (e.target.closest('.btns')) return;
      drag = true; const r = wrap.getBoundingClientRect();
      wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px'; wrap.style.right = 'auto'; wrap.style.bottom = 'auto';
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => { if (!drag) return; wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px'; wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px'; });
    window.addEventListener('mouseup', () => { drag = false; });
    P = { host, wrap, ft: $('ft'), body: $('body'), pill: $('pill'), q: $('q'), rz: $('rz'), rzb: $('rzb') };
    return P;
  }

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
  function setStage(t, cls, live) { const p = panel(); p.pill.className = 'pill ' + (cls || ''); p.pill.innerHTML = (live ? '<span class="dot"></span>' : '') + t; }
  function setQuestion(q) { panel().q.innerHTML = '问：<b>' + esc(q) + '</b>'; }
  function setReasoning(t) { const p = panel(); if (!t) { p.rz.style.display = 'none'; return; } p.rz.style.display = 'block'; p.rzb.textContent = t; }
  function setBodyText(t) { panel().body.innerHTML = '<span class="think">' + esc(t) + '</span>'; }
  function setBodyMd(md, cursor) { const p = panel(); const near = p.body.scrollHeight - p.body.scrollTop - p.body.clientHeight < 70; p.body.innerHTML = mdRender(md) + (cursor ? '<span class="cursor"></span>' : ''); if (near) p.body.scrollTop = p.body.scrollHeight; }
  function setFoot(html) { const p = panel(); p.ft.style.display = 'block'; p.ft.innerHTML = html; return p.ft; }

  function openInNewTab(question, answerMd, stocks) {
    const chips = (stocks && stocks.length) ? '<div class="stocks">识别个股：' + stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(s) + (i === 0 ? ' ·主推' : '') + '</span>').join('') + '</div>' : '';
    const css = 'body{margin:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1f2937}.page{max-width:820px;margin:28px auto;background:#fff;border-radius:16px;box-shadow:0 6px 24px rgba(0,0,0,.08);overflow:hidden}.hd{background:linear-gradient(135deg,#1f2937,#111827);color:#fff;padding:16px 24px;font-weight:600}.q{padding:14px 24px;background:#f8fafc;border-bottom:1px solid #eef2f7;color:#334155;font-weight:600}.ans{padding:18px 24px;font-size:15px;line-height:1.85}.ans h1,.ans h2,.ans h3,.ans h4{margin:18px 0 8px;color:#0f172a}.ans h2{font-size:17px}.ans p{margin:8px 0}.ans p.num{margin:12px 0 4px;color:#0f172a}.ans ul{padding-left:22px}.ans li{margin:4px 0}.ans strong{color:#0f172a}.ans code{background:#f1f5f9;padding:1px 5px;border-radius:4px}.stocks{padding:14px 24px;border-top:1px solid #eef2f7;background:#fafbfc}.chip{display:inline-block;padding:4px 12px;margin:3px 6px 3px 0;border-radius:8px;font-size:13px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}.chip.hot{background:#dcfce7;color:#15803d;border-color:#86efac;font-weight:600}';
    const html = '<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>问财观点 · ' + esc(question).slice(0, 24) + '</title><style>' + css + '</style></head><body><div class="page"><div class="hd">💡 问财观点参考</div><div class="q">' + esc(question) + '</div><div class="ans">' + mdRender(answerMd) + '</div>' + chips + '</div></body></html>';
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
    window.open(url, '_blank');
  }

  function onUpdate(phase, s) {
    if (s.reasoning) setReasoning(s.reasoning);
    if (phase === 'answering' && s.answer) { setStage('接收中 · ' + s.answer.length + '字', 'blue', true); setBodyMd(s.answer, true); }
    else if (phase === 'thinking' && !s.answer) { setStage('思考中', 'amber', true); setBodyText('问财正在分析你的需求…（约 10~20 秒，随后逐字给出结论；思考过程可点上方展开）'); }
  }

  async function runAsk(question) {
    const s = await getSettings();
    setQuestion(question); setStage('准备中'); setReasoning(''); setBodyText('…'); panel().ft.style.display = 'none';
    if (!getCookie('v')) { setStage('未登录', 'red'); setBodyText('没取到 v cookie，请确认已登录 iwencai。'); return; }
    if (!s.token) { setStage('未配置', 'red'); setBodyText('请点浏览器右上角扩展图标，在设置里填入上报密钥 token。'); return; }

    setStage('提问中', '', true); setBodyText('正在向问财提交问题…');
    let res;
    try {
      res = await WOP.runAimeQuery(question, {
        deep: s.deepResearch, onUpdate,
        getV: async () => getCookie('v'), getUserId: async () => getCookie('userid'),
      });
    } catch (e) { setStage('失败', 'red'); setBodyText('问财请求失败：' + e.message); return; }
    if (!res.answer.trim()) { setStage('无结果', 'red'); setBodyText('没抓到答案话术（可能被风控，或问题被判为非推荐意图）。'); return; }

    setStage('已完成', 'green'); setBodyMd(res.answer, false);
    const doUpload = async () => {
      setStage('上报中', 'blue', true); setFoot('<span class="lbl">⏳ 正在上报并识别个股…</span>');
      try {
        const r = await uploadOpinion(s.serverUrl, {
          token: s.token, question, answer_text: res.answer, trace_id: res.traceId,
          agent_mode: res.agentMode || (s.deepResearch ? 'deep_research' : 'normal'),
          uploader: s.uploader || '', only_with_stock: !!s.onlyWithStock,
        });
        if (r.skipped) { setStage('未上报', 'amber'); renderFoot([], res.sources, question, res.answer, '按「仅识别出个股才上报」设置，本次没抽出个股，未入库。'); return; }
        setStage('✓ 已存档', 'green'); renderFoot(r.stocks || [], res.sources, question, res.answer, '');
      } catch (e) { setStage('上报失败', 'red'); setFoot('上报失败：' + esc(e.message)); }
    };
    if (s.autoUpload) doUpload();
    else { const ft = setFoot('<span class="lbl">已获取答案（设置为不自动上报）。</span><div class="acts"><button class="btn" id="op-up">上报到股小察</button><button class="btn ghost" id="op-nt">🔎 新标签打开</button></div>'); ft.querySelector('#op-up').onclick = doUpload; ft.querySelector('#op-nt').onclick = () => openInNewTab(question, res.answer, []); }
  }

  function renderFoot(stocks, sources, question, answer, note) {
    const chips = stocks.length ? stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(s) + (i === 0 ? ' ·主推' : '') + '</span>').join('') : '<span style="color:#9ca3af">未识别出具体个股（纯观点/多票对比，见上方原文）</span>';
    const src = (sources && sources.length) ? '<div class="src">来源维度：' + sources.slice(0, 6).map(esc).join(' · ') + '</div>' : '';
    const noteHtml = note ? '<div style="color:#b45309;margin-bottom:6px">' + esc(note) + '</div>' : '';
    getSettings().then((s) => {
      const ft = setFoot(noteHtml + '<span class="lbl">识别个股：</span>' + chips + src
        + '<div class="acts"><a class="btn" href="' + s.serverUrl + '/wencai-opinion" target="_blank">去「问财观点」页 →</a>'
        + '<button class="btn ghost" id="op-nt">🔎 新标签打开本次</button></div>');
      ft.querySelector('#op-nt').onclick = () => openInNewTab(question, answer, stocks);
    });
  }

  // ---------- 悬浮启动器 ----------
  async function injectLauncher() {
    if (document.getElementById('gxc-wop-launcher')) return;
    const s = await getSettings();
    const host = document.createElement('div'); host.id = 'gxc-wop-launcher';
    const sh = host.attachShadow({ mode: 'open' });
    const items = (s.presets || []).map((q) => '<div class="mi" data-q="' + esc(q) + '">' + esc(q) + '</div>').join('');
    sh.innerHTML = `<style>${LCSS}</style>
      <div class="lb" id="lb" title="问财观点">💡</div>
      <div class="menu" id="menu"><div class="mhd">问财观点 · 选一个问题问</div>${items}<div class="mi cust" id="cust">✏️ 自定义问题…</div></div>`;
    (document.documentElement || document.body).appendChild(host);
    const menu = sh.getElementById('menu');
    sh.getElementById('lb').onclick = (e) => { e.stopPropagation(); menu.classList.toggle('show'); };
    document.addEventListener('click', () => menu.classList.remove('show'));
    menu.addEventListener('click', (e) => e.stopPropagation());
    sh.querySelectorAll('.mi[data-q]').forEach((el) => { el.onclick = () => { menu.classList.remove('show'); runAsk(el.getAttribute('data-q')); }; });
    sh.getElementById('cust').onclick = () => { menu.classList.remove('show'); const q = prompt('输入要问问财的口语问题:', (s.presets || [''])[0]); if (q && q.trim()) runAsk(q.trim()); };
  }

  // 后台/popup 触发前台面板(当有 iwencai 页开着时, 让用户也能看到过程)
  chrome.runtime.onMessage.addListener((msg) => { if (msg && msg.type === 'askForeground' && msg.question) runAsk(msg.question); });

  injectLauncher();
  // 设置变化(改了预置问题) → 重建启动器菜单
  chrome.storage.onChanged.addListener((c, area) => { if (area === 'sync' && c.presets) { const h = document.getElementById('gxc-wop-launcher'); if (h) h.remove(); injectLauncher(); } });

  console.log('%c[问财观点扩展] 前台已就绪', 'color:#2563eb;font-weight:bold');
})();
