// 问财观点 · 历史重看页(Porcelain Pro): 决策卡(主推+价位磁贴+距现价%)+完整分析。
(function () {
  const WOP = window.WOP;
  const $ = (id) => document.getElementById(id);
  const esc = WOP.esc;
  const i = Number(new URLSearchParams(location.search).get('i') || 0);
  const DEFAULT_SERVER = 'http://124.71.75.5';

  const fmtN = (v) => Number.isInteger(v) ? String(v) : String(+v.toFixed(2));
  const signPct = (p) => (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(1) + '%';

  chrome.storage.sync.get({ serverUrl: DEFAULT_SERVER }, (cfg) => {
    const server = cfg.serverUrl || DEFAULT_SERVER;
    chrome.storage.local.get({ history: [] }, (o) => {
      const item = (o.history || [])[i];
      if (!item) { $('ans').textContent = '记录不存在或已被清除。'; $('decision').style.display = 'none'; $('stocks').style.display = 'none'; return; }

      document.title = '问财观点 · ' + (item.q || '').slice(0, 20);
      $('q').innerHTML = '<span class="qm">「</span>' + esc(item.q || '') + '<span class="qm">」</span>';
      try { $('sub').textContent = new Date(item.ts).toLocaleString('zh-CN'); } catch (e) {}

      const fresh = WOP.extractConclusion(item.answer || '', item.stocks || []) || {};
      const stored = item.conclusion || {};
      const val = (k, label) => WOP.cleanConcVal(fresh[k] || stored[k] || '', label);
      const c = {
        buy: val('buy', '买点'), takeProfit: val('takeProfit', '止盈'), stopLoss: val('stopLoss', '止损'),
        period: val('period', '周期'), logic: val('logic', '逻辑'), risk: val('risk', '风险'),
      };
      const top = (item.stocks || [])[0] || {};
      const name = top.name || WOP.cleanConcVal(fresh.stock || stored.stock || '', '标的').replace(/\s*\(.*$/, '');
      const code = top.code || ((String(fresh.stock || stored.stock || '').match(/(\d{6})/) || [])[1] || '');

      $('ans').innerHTML = WOP.mdRender(item.answer || '');
      renderStocks(item.stocks || []);

      // 先用无现价渲染(拿不到行情也不空), 再异步取现价补上百分比
      renderDecision(name, code, c, null);
      if (code) {
        fetch(server + '/api/wencai/quote?code=' + code, { cache: 'no-store' })
          .then((r) => r.ok ? r.json() : null)
          .then((q) => { if (q && q.price) renderDecision(name, code, c, q); })
          .catch(() => {});
      }
    });
  });

  function tileHtml(cls, cn, en, valText, cur) {
    const pr = WOP.extractPrice(valText);
    const k = '<div class="t-k">' + cn + ' <span>' + en + '</span></div>';
    if (!pr) return '<div class="tile ' + cls + '">' + k + '<div class="t-only">' + esc(valText || '—') + '</div></div>';
    const num = pr.lo === pr.hi ? fmtN(pr.lo) : fmtN(pr.lo) + '–' + fmtN(pr.hi);
    let delta = '', bar = '';
    if (cur) {
      const loP = (pr.lo - cur) / cur * 100, hiP = (pr.hi - cur) / cur * 100, mid = (loP + hiP) / 2;
      const dir = mid >= 0 ? 'up' : 'down', arrow = mid >= 0 ? '↑' : '↓';
      const dtext = pr.lo === pr.hi ? signPct(loP) : signPct(loP) + '~' + signPct(hiP);
      delta = '<span class="t-d ' + dir + '">' + arrow + ' ' + dtext + '</span>';
      const w = Math.max(6, Math.min(100, Math.abs(mid) / 10 * 100));
      bar = '<div class="t-bar"><i style="width:' + w.toFixed(0) + '%"></i></div>';
    }
    return '<div class="tile ' + cls + '">' + k + '<div class="t-num">' + num + delta + '</div>' + bar + '<div class="t-cap">' + esc(valText) + '</div></div>';
  }

  function renderDecision(name, code, c, quote) {
    const box = $('decision');
    if (!(name || c.buy || c.takeProfit || c.stopLoss || c.logic || c.risk)) { box.style.display = 'none'; return; }
    const cur = quote && quote.price ? +quote.price : null;

    let html = '<div class="d-top"><div>';
    html += '<div class="d-eye">主推标的 · Top Pick</div>';
    html += '<div class="d-name">' + esc(name || '—') + (code ? '<span class="d-code">' + esc(code) + '</span>' : '') + '</div>';
    html += '<div class="d-tags">' + (c.period ? '<span class="pill">持有 ' + esc(c.period) + '</span>' : '') + 'LLM 投顾观点</div>';
    html += '</div>';
    if (code) html += '<a class="d-quote" href="https://stockpage.10jqka.com.cn/' + esc(code) + '/" target="_blank">看行情 →</a>';
    html += '</div>';

    if (cur) {
      const p = quote.pct_change, dir = p > 0 ? 'up' : p < 0 ? 'down' : '';
      const chg = (p != null && p !== 0) ? '<span class="chg ' + dir + '">' + (p > 0 ? '+' : '') + p + '%</span>' : '';
      html += '<div class="d-cur">现价 <b>' + fmtN(cur) + '</b> ' + chg + '<span style="color:var(--ink-3)">· 距离下列价位</span></div>';
    }

    const tiles = tileHtml('buy', '买入', 'BUY', c.buy, cur) + tileHtml('tp', '止盈', 'TARGET', c.takeProfit, cur) + tileHtml('sl', '止损', 'STOP', c.stopLoss, cur);
    html += '<div class="tiles">' + tiles + '</div>';

    let th = '';
    if (c.logic) th += '<div class="th logic"><span class="th-k">逻辑</span><span class="th-v">' + esc(c.logic) + '</span></div>';
    if (c.risk) th += '<div class="th risk"><span class="th-k">风险</span><span class="th-v">' + esc(c.risk) + '</span></div>';
    if (th) html += '<div class="thesis">' + th + '</div>';

    box.style.display = '';
    box.innerHTML = html;
  }

  function renderStocks(stocks) {
    const st = $('stocks');
    if (!stocks.length) { st.style.display = 'none'; return; }
    st.innerHTML = '识别个股：' + stocks.map((s, idx) =>
      '<span class="chip' + (idx === 0 ? ' hot' : '') + '"' + (s.code ? ' data-code="' + esc(s.code) + '"' : '') + '>'
      + esc(s.name || '') + (idx === 0 ? ' ·主推' : '') + '</span>').join('');
    st.querySelectorAll('.chip[data-code]').forEach((el) => {
      const code = el.getAttribute('data-code');
      el.onclick = () => window.open('https://stockpage.10jqka.com.cn/' + code + '/', '_blank');
    });
  }
})();
