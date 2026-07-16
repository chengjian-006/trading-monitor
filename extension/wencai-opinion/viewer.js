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
    $('ans').innerHTML = WOP.mdRender(item.answer || '');
    const st = $('stocks');
    const stocks = item.stocks || [];
    if (stocks.length) {
      st.innerHTML = '识别个股：' + stocks.map((s, idx) => '<span class="chip' + (idx === 0 ? ' hot' : '') + '" data-code="' + (s.code || '') + '">' + WOP.esc(s.name || '') + (idx === 0 ? ' ·主推' : '') + '</span>').join('');
      st.querySelectorAll('.chip[data-code]').forEach((el) => { const c = el.getAttribute('data-code'); if (c) { el.style.cursor = 'pointer'; el.onclick = () => window.open('https://stockpage.10jqka.com.cn/' + c + '/', '_blank'); } });
    } else { st.style.display = 'none'; }
  });
})();
