// 问财观点 · 历史重看页: 从 chrome.storage.local 读第 i 条历史, 渲染。
(function () {
  const WOP = window.WOP;
  const i = Number(new URLSearchParams(location.search).get('i') || 0);
  chrome.storage.local.get({ history: [] }, (o) => {
    const item = (o.history || [])[i];
    const $ = (id) => document.getElementById(id);
    if (!item) { $('ans').textContent = '记录不存在或已被清除。'; $('stocks').style.display = 'none'; return; }
    document.title = '问财观点 · ' + (item.q || '').slice(0, 20);
    $('q').textContent = '问：' + (item.q || '');
    try { $('sub').textContent = new Date(item.ts).toLocaleString(); } catch (e) {}
    const c = item.conclusion;
    let cardHtml = '';
    if (c && (c.stock || c.buy || c.stopLoss || c.logic)) {
      const rows = [['📌', '主推', c.stock], ['🟢', '买点', c.buy], ['🎯', '止盈', c.takeProfit], ['🛑', '止损', c.stopLoss], ['⏳', '周期', c.period], ['💡', '逻辑', c.logic], ['⚠️', '风险', c.risk]]
        .filter((r) => r[2]).map((r) => '<div style="display:flex;gap:10px;padding:3px 0"><span>' + r[0] + '</span><span style="width:40px;color:#64748b">' + r[1] + '</span><span style="flex:1">' + WOP.esc(r[2]) + '</span></div>').join('');
      cardHtml = '<div style="border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px;margin-bottom:14px;background:#f8fafc"><div style="font-weight:700;margin-bottom:6px">🎯 结论速览</div>' + rows + '</div>';
    }
    $('ans').innerHTML = cardHtml + WOP.mdRender(item.answer || '');
    const st = $('stocks');
    const stocks = item.stocks || [];
    if (stocks.length) {
      st.innerHTML = '识别个股：' + stocks.map((s, idx) => '<span class="chip' + (idx === 0 ? ' hot' : '') + '" data-code="' + (s.code || '') + '">' + WOP.esc(s.name || '') + (idx === 0 ? ' ·主推' : '') + '</span>').join('');
      st.querySelectorAll('.chip[data-code]').forEach((el) => { const c = el.getAttribute('data-code'); if (c) { el.style.cursor = 'pointer'; el.onclick = () => window.open('https://stockpage.10jqka.com.cn/' + c + '/', '_blank'); } });
    } else { st.style.display = 'none'; }
  });
})();
