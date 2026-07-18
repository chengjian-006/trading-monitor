// 问财观点 · 历史重看页: 从 chrome.storage.local 读第 i 条历史, 渲染「决策速览卡 + 完整分析」。
(function () {
  const WOP = window.WOP;
  const $ = (id) => document.getElementById(id);
  const esc = WOP.esc;
  const i = Number(new URLSearchParams(location.search).get('i') || 0);

  chrome.storage.local.get({ history: [] }, (o) => {
    const item = (o.history || [])[i];
    if (!item) { $('ans').textContent = '记录不存在或已被清除。'; $('stocks').style.display = 'none'; $('decision').style.display = 'none'; return; }

    document.title = '问财观点 · ' + (item.q || '').slice(0, 20);
    $('q').innerHTML = '<span class="lbl">问</span>' + esc(item.q || '');
    try { $('sub').textContent = new Date(item.ts).toLocaleString('zh-CN'); } catch (e) {}

    // 结论: 优先从原文重抽(新表格解析更干净), 逐字段回退到落库时存的, 再统一清洗竖线/标签残留
    const fresh = WOP.extractConclusion(item.answer || '', item.stocks || []) || {};
    const stored = item.conclusion || {};
    const val = (k, label) => WOP.cleanConcVal(fresh[k] || stored[k] || '', label);
    const c = {
      buy: val('buy', '买点'), takeProfit: val('takeProfit', '止盈'), stopLoss: val('stopLoss', '止损'),
      period: val('period', '周期'), logic: val('logic', '逻辑'), risk: val('risk', '风险'),
    };

    // 主推标的: 用抽出的个股(带 code 可跳行情), 回退到结论里的标的名
    const top = (item.stocks || [])[0];
    const stockName = top ? top.name : WOP.cleanConcVal(fresh.stock || stored.stock || '', '标的').replace(/\s*\(.*$/, '');
    const stockCode = top ? top.code : ((String(fresh.stock || stored.stock || '').match(/(\d{6})/) || [])[1] || '');

    renderDecision(stockName, stockCode, c);

    $('ans').innerHTML = WOP.mdRender(item.answer || '');
    renderStocks(item.stocks || []);
  });

  function renderDecision(name, code, c) {
    const box = $('decision');
    const hasAny = name || c.buy || c.takeProfit || c.stopLoss || c.logic || c.risk;
    if (!hasAny) { box.style.display = 'none'; return; }

    let html = '<div class="card"><div class="dc">';
    html += '<div class="dc-hd"><span class="dc-title">🎯 决策速览</span>' + (c.period ? '<span class="dc-period">周期 · ' + esc(c.period) + '</span>' : '') + '</div>';

    if (name) {
      const quote = code ? '<a class="dc-quote" href="https://stockpage.10jqka.com.cn/' + esc(code) + '/" target="_blank">看行情 →</a>' : '';
      html += '<div class="dc-stock"><div class="dc-stock-l">'
        + '<span class="dc-stock-tag">主推</span>'
        + '<span class="dc-stock-name">' + esc(name) + '</span>'
        + (code ? '<span class="dc-stock-code">' + esc(code) + '</span>' : '')
        + '</div>' + quote + '</div>';
    }

    const tile = (cls, k, v) => v ? '<div class="tile ' + cls + '"><div class="tile-k">' + k + '</div><div class="tile-v">' + esc(v) + '</div></div>' : '';
    const tiles = tile('buy', '🟢 买点', c.buy) + tile('tp', '🎯 止盈', c.takeProfit) + tile('sl', '🛑 止损', c.stopLoss);
    if (tiles) html += '<div class="dc-tiles">' + tiles + '</div>';

    const row = (cls, k, v) => v ? '<div class="dc-row ' + cls + '"><span class="dc-row-k">' + k + '</span><span class="dc-row-v">' + esc(v) + '</span></div>' : '';
    const rows = row('logic', '💡 逻辑', c.logic) + row('risk', '⚠️ 风险', c.risk);
    if (rows) html += '<div class="dc-rows">' + rows + '</div>';

    html += '</div></div>';
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
