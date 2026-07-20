// 问财观点扩展 · 上手引导页逻辑
// 三步全部自动检测, 不要用户手动点"我做完了"(手动确认只会得到一堆假的勾)。三个探针:
//   ① 固定工具栏 = chrome.action.getUserSettings().isOnToolbar —— 没有事件可监听, 只能轮询
//   ② 登录问财   = iwencai 的 v cookie 是否存在(与 common.js/background.js 判定登录同一口径)
//   ③ 问过一句   = storage.local.history 非空 —— content.js(前台浮层)和 background.js(后台跑)
//                  两条问答链路都往这里写, 是唯一可靠的汇合点, 不用另加标记位
// MV3 的 CSP 禁内联 script, 所以本文件必须独立存在, 不能塞回 welcome.html。

const $ = (id) => document.getElementById(id);

// 三步各自的完成态。s1/s2 由探针刷新, s3 由 history 决定。
const done = { s1: false, s2: false, s3: false };

// ---------- 进度与单步外观 ----------
function paint() {
  const n = (done.s1 ? 1 : 0) + (done.s2 ? 1 : 0) + (done.s3 ? 1 : 0);
  $('progFill').style.width = (n / 3 * 100) + '%';
  $('progTxt').textContent = n + ' / 3';
  ['s1', 's2', 's3'].forEach((k) => $(k).classList.toggle('done', done[k]));
  // 没登录就问不了, 第三步压灰并禁用按钮 —— 让用户点了才报"未登录"是更差的体验
  $('s3').classList.toggle('locked', !done.s2 && !done.s3);
  $('s3Go').disabled = !done.s2 || asking;
  $('doneBar').classList.toggle('show', n === 3);
}

function setState(id, cls, txt) {
  const el = $(id);
  el.className = 'state ' + cls;
  el.textContent = txt;
}

// ---------- ① 固定到工具栏 ----------
// getUserSettings 是 Chrome 91+/Edge 同版起才有; 拿不到就退成手动确认按钮, 别把这步卡死。
async function checkPinned() {
  if (!chrome.action || !chrome.action.getUserSettings) {
    const ack = await new Promise((res) => chrome.storage.local.get({ welcomePinAck: false }, (o) => res(o.welcomePinAck)));
    done.s1 = !!ack;
    setState('s1State', ack ? 'ok' : 'no', ack ? '已确认固定' : '这个浏览器版本查不到固定状态, 固定好了点下面的按钮');
    $('s1Manual').hidden = ack;
    paint();
    return;
  }
  let pinned = false;
  try { pinned = !!(await chrome.action.getUserSettings()).isOnToolbar; } catch (e) { /* 查不到当没固定 */ }
  done.s1 = pinned;
  setState('s1State', pinned ? 'ok' : 'wait', pinned ? '已固定到工具栏 ✓' : '还没固定 — 按上面三步点一下, 这里会自动打勾');
  $('s1Manual').hidden = true;
  paint();
}

$('s1Manual').onclick = () => {
  chrome.storage.local.set({ welcomePinAck: true });
  done.s1 = true;
  $('s1Manual').hidden = true;
  setState('s1State', 'ok', '已确认固定');
  paint();
};

// ---------- ② 登录问财 ----------
const getV = () => new Promise((res) => chrome.cookies.get({ url: 'https://www.iwencai.com', name: 'v' }, (c) => res(c ? c.value : '')));

async function checkLogin() {
  const v = await getV();
  done.s2 = !!v;
  setState('s2State', v ? 'ok' : 'wait', v ? '已登录问财 ✓' : '还没登录 — 登录完这页会自动打勾, 不用回来点');
  $('s2Go').textContent = v ? '换个账号登录' : '去登录问财 →';
  paint();
}

$('s2Go').onclick = () => chrome.tabs.create({ url: 'https://www.iwencai.com/' });

// background.js 在 v cookie 变动时会广播 loginRefreshed, 用户在另一个标签页登录完这里当场变勾。
// 注意别把 checkLogin() 的 Promise 当返回值交出去 —— onMessage 监听器返回非 false 的值会被当成
// "我要异步回复", 而这里根本不回复, 结果是控制台刷 message channel closed 报错。
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'loginRefreshed') { checkLogin(); }
});

// ---------- ③ 试问一句 ----------
let asking = false;

function renderTry(rec, err) {
  const box = $('s3Box');
  box.hidden = false;
  box.innerHTML = '';
  if (err) {
    const p = document.createElement('div');
    p.className = 'out err';
    p.textContent = '没问成: ' + err;
    box.appendChild(p);
    return;
  }
  const q = document.createElement('div');
  q.className = 'q';
  q.innerHTML = '问的是: <b></b>';
  q.querySelector('b').textContent = rec.q || '';
  box.appendChild(q);

  const out = document.createElement('div');
  out.className = 'out';
  // 有结论就只放结论(引导页要的是"看见它真能出东西", 不是读全文), 没有才退回答正文截断
  const text = (rec.conclusion || rec.answer || '').trim();
  out.textContent = text.length > 260 ? text.slice(0, 260) + '…' : (text || '(没有正文)');
  box.appendChild(out);

  const items = rec.stocks || [];
  if (items.length) {
    const wrap = document.createElement('div');
    wrap.className = 'stocks';
    items.slice(0, 8).forEach((s) => {
      const t = document.createElement('span');
      t.className = 'tag';
      t.textContent = typeof s === 'string' ? s : (s.name || '');
      wrap.appendChild(t);
    });
    box.appendChild(wrap);
  }
}

// history 是两条问答链路的汇合点: 有记录 = 这一步走通过。顺手把最近一条显示出来。
function checkAsked() {
  return new Promise((res) => {
    chrome.storage.local.get({ history: [] }, (o) => {
      const h = o.history || [];
      done.s3 = h.length > 0;
      if (h.length) {
        setState('s3State', 'ok', '已经问过并存进股小察 ✓');
        if (!asking) renderTry(h[0]);
      } else {
        setState('s3State', 'wait', '还没问过');
      }
      paint();
      res(h[0] || null);
    });
  });
}

// 前台浮层问完也会写 history, 那边问过这页同样自动打勾。
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === 'local' && ch.history && !asking) checkAsked();
});

$('s3Go').onclick = async () => {
  if (asking) return;
  asking = true;
  paint();
  setState('s3State', 'wait', '问财思考中… 大概 10～40 秒');
  $('s3Box').hidden = false;
  $('s3Box').innerHTML = '<div class="out"><span class="spin"></span> 正在问, 别关这个页面…</div>';

  // 用第一条预置问题, 与工具栏 💡 的第一个快捷按钮完全一致 —— 引导里试的就是以后天天用的那条
  const s = await new Promise((res) => chrome.storage.sync.get({ presets: [] }, res));
  const question = (s.presets && s.presets[0]) || '给我推荐一只股票,目前处于买入区间,持股一周以内,盈利7%以上';

  try {
    const r = await chrome.runtime.sendMessage({ type: 'runBg', question });
    if (!r || !r.ok) throw new Error((r && r.error) || '后台没有响应');
    asking = false;
    // runBg 成功后 history 里已有这条, checkAsked 会连带把它渲染出来
    const rec = await checkAsked();
    if (!rec) renderTry({ q: question, conclusion: '问成了, 但没抓到正文。' });
  } catch (e) {
    asking = false;
    setState('s3State', 'no', '这次没问成, 可以再试一次');
    renderTry(null, String((e && e.message) || e));
  }
  paint();
};

// ---------- 启动与轮询 ----------
// 固定状态没有事件可监听, 只能轮询; 登录靠广播兜一层轮询防 SW 休眠时漏播。
// 页面切走时停掉, 别让一个开着不管的标签页一直空转。
let timer = null;
function startPoll() {
  stopPoll();
  timer = setInterval(async () => {
    await checkPinned();
    await checkLogin();
    // 前两步都勾上就没什么可轮询的了(第三步靠 storage.onChanged 推), 停掉别空转
    if (done.s1 && done.s2) stopPoll();
  }, 2000);
}
function stopPoll() {
  if (timer) { clearInterval(timer); timer = null; }
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopPoll();
  else { checkPinned(); checkLogin(); checkAsked(); startPoll(); }
});

checkPinned();
checkLogin();
checkAsked();
startPoll();
