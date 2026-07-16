// 问财观点扩展 · 前台内容脚本 (仅 www.iwencai.com)
// 悬浮💡 → 菜单 → 实时浮层(思考/流式markdown/个股可点/复制/重新问/追问/新标签) → 上报 + 存历史。
(function () {
  'use strict';
  const WOP = self.WOP;
  const esc = WOP.esc, mdRender = WOP.mdRender;

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

  function pushHistory(rec) { try { chrome.storage.local.get({ history: [] }, (o) => chrome.storage.local.set({ history: [rec, ...(o.history || [])].slice(0, 15) })); } catch (e) {} }

  function uploadOpinion(serverUrl, payload) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: 'upload', url: serverUrl + '/api/wencai/opinion', payload }, (resp) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (resp && resp.ok) resolve(resp.data || {});
        else reject(new Error((resp && (resp.error || (resp.data && resp.data.detail) || ('HTTP ' + (resp.status || '?')))) || '上报失败'));
      });
    });
  }

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; right: 20px; bottom: 84px; z-index: 2147483646; width: min(468px, 92vw); max-height: 78vh;
    display: flex; flex-direction: column; background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 18px 52px rgba(0,0,0,.30);
    border: 1px solid #e8ebf0; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    animation: pop .22s cubic-bezier(.2,.8,.3,1); color:#1f2937; }
  @keyframes pop { from { opacity: 0; transform: translateY(12px) scale(.98); } to { opacity: 1; transform: none; } }
  .wrap.min { max-height: none; width: auto; } .wrap.min .q, .wrap.min .rz, .wrap.min .body, .wrap.min .ft { display: none; }
  .hd { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px;
    background: linear-gradient(135deg,#1f2937,#111827); color: #fff; cursor: grab; user-select: none; } .hd:active { cursor: grabbing; }
  .hd .l { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14.5px; white-space: nowrap; }
  .pill { font-size: 11.5px; font-weight: 500; padding: 3px 10px; border-radius: 20px; background: #374151; color:#fff; white-space: nowrap; }
  .pill.blue { background:#2563eb; } .pill.amber { background:#b45309; } .pill.green { background:#15803d; } .pill.red { background:#b91c1c; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor; margin-right:5px; animation: blink 1s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
  .btns { display: flex; align-items: center; gap: 4px; }
  .ic { cursor: pointer; width: 24px; height: 24px; display:flex; align-items:center; justify-content:center; border-radius: 7px; color: #9aa4b2; font-size: 16px; } .ic:hover { color:#fff; background: rgba(255,255,255,.12); }
  .q { padding: 9px 16px; font-size: 12.5px; color: #64748b; background: #f8fafc; border-bottom: 1px solid #eef2f7; } .q b { color:#334155; }
  .rz { border-bottom: 1px solid #eef2f7; background:#fcfcfd; }
  .rz-h { padding: 8px 16px; font-size: 12.5px; color:#64748b; cursor: pointer; display:flex; align-items:center; gap:6px; user-select:none; } .rz-h:hover { color:#334155; }
  .rz-h .ar { transition: transform .18s; font-size:10px; } .rz.open .rz-h .ar { transform: rotate(90deg); }
  .rz-b { display:none; padding: 0 16px 10px; font-size: 12px; line-height: 1.7; color:#94a3b8; max-height: 170px; overflow-y:auto; white-space: pre-wrap; } .rz.open .rz-b { display:block; }
  .body { flex: 1; overflow-y: auto; padding: 6px 16px 14px; font-size: 14px; line-height: 1.8; color: #1f2937; word-break: break-word; }
  .body::-webkit-scrollbar, .rz-b::-webkit-scrollbar { width: 8px; } .body::-webkit-scrollbar-thumb, .rz-b::-webkit-scrollbar-thumb { background:#d1d9e3; border-radius: 8px; }
  .body h1,.body h2,.body h3,.body h4 { margin: 14px 0 6px; line-height:1.4; color:#0f172a; }
  .body h1 { font-size: 17px; } .body h2 { font-size: 15.5px; } .body h3,.body h4 { font-size: 14px; }
  .body p { margin: 6px 0; } .body p.num { margin: 10px 0 4px; color:#0f172a; }
  .body ul { margin: 4px 0; padding-left: 20px; } .body li { margin: 3px 0; }
  .body strong { color:#0f172a; font-weight: 700; } .body code { background:#f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; } .body .think { color:#94a3b8; }
  .cursor { display:inline-block; width:7px; height:15px; background:#2563eb; margin-left:2px; vertical-align:-2px; animation: blink .8s steps(1) infinite; border-radius:1px; }
  .ft { padding: 11px 16px; border-top: 1px solid #eef2f7; font-size: 12.5px; color: #64748b; background: #fafbfc; } .ft .lbl { color:#94a3b8; }
  .chip { display:inline-block; padding: 3px 10px; margin: 3px 6px 3px 0; border-radius: 7px; font-size: 12.5px; background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; cursor:pointer; } .chip:hover { filter:brightness(.96); }
  .chip.hot { background:#dcfce7; color:#15803d; border-color:#86efac; font-weight:600; }
  .src { margin-top: 6px; font-size: 11.5px; color:#94a3b8; }
  .acts { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .btn { display:inline-block; padding: 8px 14px; background:#1f2937; color:#fff; border-radius: 9px; text-decoration:none; font-size: 12.5px; cursor:pointer; border:none; } .btn:hover { background:#111827; }
  .btn.ghost { background:#fff; color:#1f2937; border:1px solid #d1d9e3; } .btn.ghost:hover { background:#f1f5f9; }
  .fu { display:flex; gap:6px; margin-top:10px; } .fu input { flex:1; padding:7px 10px; border:1px solid #d1d9e3; border-radius:8px; font-size:12.5px; font-family:inherit; }
  .card { border:1px solid #e2e8f0; border-radius:12px; padding:10px 12px; margin-bottom:10px; background:#f8fafc; }
  .cardh { font-weight:700; font-size:13px; color:#0f172a; margin-bottom:6px; }
  .crow { display:flex; gap:8px; align-items:flex-start; padding:3px 0; font-size:13px; line-height:1.5; }
  .crow .ci { flex-shrink:0; } .crow .cl { flex-shrink:0; width:34px; color:#64748b; } .crow .cv { color:#1f2937; flex:1; }
  .full-h { font-size:12.5px; color:#64748b; cursor:pointer; display:flex; align-items:center; gap:6px; user-select:none; padding:4px 0; } .full-h:hover { color:#334155; }
  .full-h .ar { font-size:10px; transition:transform .18s; } .full.open .full-h .ar { transform:rotate(90deg); }
  .full-b { display:none; } .full.open .full-b { display:block; }
  @media (prefers-color-scheme: dark) {
    .wrap { background:#1e293b; color:#e2e8f0; border-color:#334155; }
    .q { background:#172033; color:#cbd5e1; border-color:#334155; } .q b { color:#e2e8f0; }
    .rz { background:#172033; border-color:#334155; } .rz-b { color:#94a3b8; }
    .body { color:#e2e8f0; } .body h1,.body h2,.body h3,.body h4,.body strong,.body p.num { color:#f1f5f9; } .body code { background:#334155; }
    .ft { background:#172033; border-color:#334155; color:#94a3b8; }
    .chip { background:#334155; color:#cbd5e1; border-color:#475569; } .chip.hot { background:#14532d; color:#86efac; border-color:#166534; }
    .btn.ghost { background:#1e293b; color:#e2e8f0; border-color:#475569; } .btn.ghost:hover { background:#334155; }
    .fu input { background:#0f172a; color:#e2e8f0; border-color:#475569; }
    .card { background:#172033; border-color:#334155; } .cardh { color:#f1f5f9; } .crow .cv { color:#e2e8f0; } .crow .cl { color:#94a3b8; }
  }`;
  const LCSS = `
  :host { all: initial; }
  .lb { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; width: 48px; height: 48px; border-radius: 50%;
    background: linear-gradient(135deg,#2563eb,#1e40af); color:#fff; font-size: 22px; display:flex; align-items:center; justify-content:center;
    cursor: pointer; box-shadow: 0 8px 24px rgba(37,99,235,.4); user-select:none; transition: transform .15s; } .lb:hover { transform: scale(1.08); }
  .menu { position: fixed; right: 20px; bottom: 78px; z-index: 2147483647; display:none; flex-direction: column; background:#fff; border-radius: 12px;
    box-shadow: 0 12px 36px rgba(0,0,0,.24); border:1px solid #e8ebf0; overflow:hidden; min-width: 240px; max-width: 320px;
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; animation: pop .18s ease; }
  @keyframes pop { from { opacity:0; transform: translateY(8px); } to { opacity:1; transform:none; } } .menu.show { display:flex; }
  .mi { padding: 10px 14px; font-size: 13px; color:#1f2937; cursor: pointer; border-bottom: 1px solid #f1f5f9; line-height:1.5; } .mi:last-child { border-bottom: none; } .mi:hover { background:#f8fafc; }
  .mi.cust { color:#2563eb; font-weight: 500; } .mhd { padding: 8px 14px; font-size: 11.5px; color:#94a3b8; background:#f8fafc; border-bottom:1px solid #eef2f7; }
  @media (prefers-color-scheme: dark) { .menu { background:#1e293b; border-color:#334155; } .mi { color:#e2e8f0; border-color:#334155; } .mi:hover { background:#334155; } .mhd { background:#172033; color:#94a3b8; border-color:#334155; } }`;

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
    $('hd').addEventListener('mousedown', (e) => { if (e.target.closest('.btns')) return; drag = true; const r = wrap.getBoundingClientRect(); wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px'; wrap.style.right = 'auto'; wrap.style.bottom = 'auto'; sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; e.preventDefault(); });
    window.addEventListener('mousemove', (e) => { if (!drag) return; wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px'; wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px'; });
    window.addEventListener('mouseup', () => { drag = false; });
    P = { host, wrap, ft: $('ft'), body: $('body'), pill: $('pill'), q: $('q'), rz: $('rz'), rzb: $('rzb'), sessionId: '' };
    return P;
  }

  function setStage(t, cls, live) { const p = panel(); p.pill.className = 'pill ' + (cls || ''); p.pill.innerHTML = (live ? '<span class="dot"></span>' : '') + t; }
  function setQuestion(q) { panel().q.innerHTML = '问：<b>' + esc(q) + '</b>'; }
  function setReasoning(t) { const p = panel(); if (!t) { p.rz.style.display = 'none'; return; } p.rz.style.display = 'block'; p.rzb.textContent = t; }
  function setBodyText(t) { panel().body.innerHTML = '<span class="think">' + esc(t) + '</span>'; }
  function setBodyMd(md, cursor) { const p = panel(); const near = p.body.scrollHeight - p.body.scrollTop - p.body.clientHeight < 70; p.body.innerHTML = mdRender(md) + (cursor ? '<span class="cursor"></span>' : ''); if (near) p.body.scrollTop = p.body.scrollHeight; }
  function setFoot(html) { const p = panel(); p.ft.style.display = 'block'; p.ft.innerHTML = html; return p.ft; }
  function concCard(c) {
    const row = (icon, label, val) => val ? '<div class="crow"><span class="ci">' + icon + '</span><span class="cl">' + label + '</span><span class="cv">' + esc(val) + '</span></div>' : '';
    return '<div class="card"><div class="cardh">🎯 结论速览</div>'
      + row('📌', '主推', c.stock) + row('🟢', '买点', c.buy) + row('🎯', '止盈', c.takeProfit)
      + row('🛑', '止损', c.stopLoss) + row('⏳', '周期', c.period) + row('💡', '逻辑', c.logic) + row('⚠️', '风险', c.risk)
      + '</div>';
  }
  function renderResult(conclusion, answerMd) {
    const p = panel();
    const hasAny = conclusion && (conclusion.stock || conclusion.buy || conclusion.takeProfit || conclusion.stopLoss || conclusion.logic || conclusion.risk);
    p.body.innerHTML = (hasAny ? concCard(conclusion) : '')
      + '<div class="full' + (hasAny ? '' : ' open') + '"><div class="full-h" id="fullh"><span class="ar">▸</span>完整分析</div><div class="full-b">' + mdRender(answerMd) + '</div></div>';
    const fh = p.body.querySelector('#fullh'); if (fh) fh.onclick = () => p.body.querySelector('.full').classList.toggle('open');
    if (p.body.scrollTop !== undefined) p.body.scrollTop = 0;
  }

  function openInNewTab(question, answerMd, stockItems) { const url = URL.createObjectURL(new Blob([WOP.buildStandaloneHtml(question, answerMd, stockItems)], { type: 'text/html' })); window.open(url, '_blank'); }
  function copyText(t) { try { navigator.clipboard.writeText(t); } catch (e) { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); } }

  function onUpdate(phase, s) {
    if (s.reasoning) setReasoning(s.reasoning);
    if (phase === 'answering' && s.answer) { setStage('接收中 · ' + s.answer.length + '字', 'blue', true); setBodyMd(s.answer, true); }
    else if (phase === 'thinking' && !s.answer) { setStage('思考中', 'amber', true); setBodyText('问财正在分析你的需求…（约 10~20 秒，随后逐字给出结论；思考过程可点上方展开）'); }
  }

  async function runAsk(question, sessionId) {
    const s = await getSettings();
    setQuestion(question); setStage('准备中'); setReasoning(''); setBodyText('…'); panel().ft.style.display = 'none';
    if (!getCookie('v')) { setStage('未登录', 'red'); setBodyText('没取到问财登录 cookie。'); setFoot('<div class="acts"><a class="btn" href="https://www.iwencai.com/" target="_blank">去登录问财 →</a></div>'); return; }

    setStage('提问中', '', true); setBodyText('正在向问财提交问题…');
    let res;
    try {
      res = await WOP.runAimeQuery(question, { deep: s.deepResearch, onUpdate, sessionId: sessionId || undefined, askText: question + WOP.FORMAT_SUFFIX, getV: async () => getCookie('v'), getUserId: async () => getCookie('userid') });
    } catch (e) { setStage('失败', 'red'); setBodyText('问财请求失败：' + e.message); return; }
    if (!res.answer.trim()) { setStage('无结果', 'red'); setBodyText('没抓到答案话术（可能被风控，或问题被判为非推荐意图）。'); return; }
    const rawAnswer = res.answer;                                        // 含【】结论标记, 供抽取
    res.answer = WOP.stripMarkerLine(WOP.stripEmbeds(rawAnswer));        // 展示/存库: 去图表占位块+结论标记行
    panel().sessionId = res.sessionId || '';
    setStage('已完成', 'green');
    renderResult(WOP.extractConclusion(rawAnswer, []), res.answer);      // 先按标记出结论卡(主推名来自标记)

    const doUpload = async () => {
      setStage('上报中', 'blue', true);
      try {
        const conc = WOP.extractConclusion(rawAnswer, []);
        const r = await uploadOpinion(s.serverUrl, { token: s.token, question, answer_text: res.answer, reasoning: res.reasoning || '', conclusion: conc, trace_id: res.traceId, agent_mode: res.agentMode || (s.deepResearch ? 'deep_research' : 'normal'), uploader: s.uploader || getCookie('userid') || '', only_with_stock: !!s.onlyWithStock });
        const items = r.stock_items || (r.stocks || []).map((n) => ({ name: n }));
        const c2 = WOP.extractConclusion(rawAnswer, items);              // 有代码后重算(主推可点)
        renderResult(c2, res.answer);
        pushHistory({ q: question, answer: res.answer, stocks: items, conclusion: c2, ts: Date.now() });
        if (r.skipped) { setStage('未上报', 'amber'); renderFoot([], res.sources, question, res.answer, '按「仅识别出个股才上报」，本次没抽出个股，未入库。'); }
        else { setStage('✓ 已存档', 'green'); renderFoot(items, res.sources, question, res.answer, ''); }
      } catch (e) { setStage('上报失败', 'red'); setFoot('上报失败：' + esc(e.message)); }
    };
    if (s.autoUpload) doUpload();
    else { pushHistory({ q: question, answer: res.answer, stocks: [], conclusion: WOP.extractConclusion(rawAnswer, []), ts: Date.now() }); const ft = setFoot('<span class="lbl">已获取答案（设置为不自动上报）。</span>' + actsHtml(question, res.answer, [], true)); wireActs(ft, question, res.answer, []); ft.querySelector('#op-up').onclick = doUpload; }
  }

  function actsHtml(question, answer, items, withUpload) {
    return '<div class="acts">'
      + (withUpload ? '<button class="btn" id="op-up">上报到股小察</button>' : '<a class="btn" id="op-page" href="#" target="_blank">去「问财观点」页 →</a>')
      + '<button class="btn ghost" id="op-nt">🔎 新标签</button>'
      + '<button class="btn ghost" id="op-cp">复制全文</button>'
      + '<button class="btn ghost" id="op-re">重新问</button>'
      + '<button class="btn ghost" id="op-fu">追问</button></div>'
      + '<div class="fu" id="op-fubox" style="display:none"><input id="op-fuin" type="text" placeholder="接着问…（同一会话，问财记得上文）"><button class="btn" id="op-fugo">问</button></div>';
  }
  function wireActs(ft, question, answer, items) {
    const q = (id) => ft.querySelector(id);
    if (q('#op-nt')) q('#op-nt').onclick = () => openInNewTab(question, answer, items);
    if (q('#op-cp')) q('#op-cp').onclick = () => { copyText(answer); q('#op-cp').textContent = '已复制'; setTimeout(() => { if (q('#op-cp')) q('#op-cp').textContent = '复制全文'; }, 1500); };
    if (q('#op-re')) q('#op-re').onclick = () => runAsk(question);
    if (q('#op-fu')) q('#op-fu').onclick = () => { const box = q('#op-fubox'); box.style.display = box.style.display === 'none' ? 'flex' : 'none'; const inp = q('#op-fuin'); if (inp) inp.focus(); };
    const go = () => { const inp = q('#op-fuin'); const t = inp && inp.value.trim(); if (t) runAsk(t, panel().sessionId); };
    if (q('#op-fugo')) q('#op-fugo').onclick = go;
    if (q('#op-fuin')) q('#op-fuin').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  }
  function renderFoot(items, sources, question, answer, note) {
    const chips = items.length ? items.map((it, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '" data-code="' + (it.code || '') + '">' + esc(it.name) + (i === 0 ? ' ·主推' : '') + '</span>').join('') : '<span style="color:#9ca3af">未识别出具体个股（纯观点/多票对比，见上方原文）</span>';
    const src = (sources && sources.length) ? '<div class="src">来源维度：' + sources.slice(0, 6).map(esc).join(' · ') + '</div>' : '';
    const noteHtml = note ? '<div style="color:#b45309;margin-bottom:6px">' + esc(note) + '</div>' : '';
    getSettings().then((s) => {
      const ft = setFoot(noteHtml + '<span class="lbl">识别个股：</span>' + chips + src + actsHtml(question, answer, items, false));
      wireActs(ft, question, answer, items);
      const pg = ft.querySelector('#op-page'); if (pg) pg.href = s.serverUrl + '/wencai-opinion';
      ft.querySelectorAll('.chip[data-code]').forEach((el) => { const code = el.getAttribute('data-code'); if (code) el.onclick = () => window.open('https://stockpage.10jqka.com.cn/' + code + '/', '_blank'); else el.style.cursor = 'default'; });
    });
  }

  async function injectLauncher() {
    if (document.getElementById('gxc-wop-launcher')) return;
    const s = await getSettings();
    const host = document.createElement('div'); host.id = 'gxc-wop-launcher';
    const sh = host.attachShadow({ mode: 'open' });
    const items = (s.presets || []).map((q) => '<div class="mi" data-q="' + esc(q) + '">' + esc(q) + '</div>').join('');
    sh.innerHTML = `<style>${LCSS}</style><div class="lb" id="lb" title="问财观点">💡</div>
      <div class="menu" id="menu"><div class="mhd">问财观点 · 选一个问题问</div>${items}<div class="mi cust" id="cust">✏️ 自定义问题…</div></div>`;
    (document.documentElement || document.body).appendChild(host);
    const menu = sh.getElementById('menu');
    sh.getElementById('lb').onclick = (e) => { e.stopPropagation(); menu.classList.toggle('show'); };
    document.addEventListener('click', () => menu.classList.remove('show'));
    menu.addEventListener('click', (e) => e.stopPropagation());
    sh.querySelectorAll('.mi[data-q]').forEach((el) => { el.onclick = () => { menu.classList.remove('show'); runAsk(el.getAttribute('data-q')); }; });
    sh.getElementById('cust').onclick = () => { menu.classList.remove('show'); const q = prompt('输入要问问财的口语问题:', (s.presets || [''])[0]); if (q && q.trim()) runAsk(q.trim()); };
  }

  chrome.runtime.onMessage.addListener((msg) => { if (msg && msg.type === 'askForeground' && msg.question) runAsk(msg.question); });
  chrome.storage.onChanged.addListener((c, area) => { if (area === 'sync' && c.presets) { const h = document.getElementById('gxc-wop-launcher'); if (h) h.remove(); injectLauncher(); } });
  injectLauncher();
  console.log('%c[问财观点扩展] 前台就绪 v1.1', 'color:#2563eb;font-weight:bold');
})();
