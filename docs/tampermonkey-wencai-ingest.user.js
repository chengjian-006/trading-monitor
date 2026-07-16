// ==UserScript==
// @name         问财候选代跑上报
// @namespace    guxiaocha-wencai-ingest
// @version      1.0
// @description  在 www.iwencai.com 登录态下跑「服务器下发的选股语句清单」, 归一化后 POST 回股小察服务器落候选榜。语句可在系统里自定义, 脚本不写死。
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

  // ============ 需要你填的两处 ============
  // 服务器地址(股小察后端 nginx 入口): 本地后端填 http://localhost:8000, 生产填 http://124.71.75.5
  // 改这里的 host 时, 上面的 @connect 也要同步有对应条目, 否则 GM_xmlhttpRequest 会被拦。
  const SERVER_URL = 'http://124.71.75.5';
  // 共享密钥: 必须和服务器 config.json 的 wencai_screening.ingest_token 完全一致。
  const INGEST_TOKEN = 'PUT_YOUR_TOKEN_HERE';
  // ======================================

  const QUERY_INTERVAL_MS = 2500;  // 语句间隔(降同花顺 IP 级风控触发概率)

  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  }

  // 跨域 POST 到自己的服务器(GM_xmlhttpRequest 绕过同源/CSP), 返回解析后的 JSON。
  function gmPost(path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: SERVER_URL + path,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify(payload),
        timeout: 20000,
        onload: (r) => {
          try {
            const j = JSON.parse(r.responseText);
            if (r.status >= 200 && r.status < 300) resolve(j);
            else reject(new Error('HTTP ' + r.status + ': ' + (j.detail || r.responseText)));
          } catch (e) { reject(new Error('HTTP ' + r.status + ' 非JSON: ' + r.responseText.slice(0, 200))); }
        },
        onerror: () => reject(new Error('网络错误(检查 SERVER_URL / @connect)')),
        ontimeout: () => reject(new Error('超时')),
      });
    });
  }

  // 同源 fetch 问财 get-robot-data(页面在 iwencai, 同源不撞 CORS)。
  async function fetchWencai(query) {
    const v = getCookie('v');
    const body = {
      question: query, perpage: 100, page: 1,
      log_info: JSON.stringify({ input_type: 'typewrite' }),
      source: 'Ths_iwencai_Xuangu', version: '2.0', query_area: '', block_list: '',
      add_info: JSON.stringify({ urp: { scene: 1, company: 1, business: 1 }, contentType: 'json', searchInfo: true }),
    };
    const resp = await fetch('/customized/chart/get-robot-data', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'hexin-v': v },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error('问财 HTTP ' + resp.status);
    const json = await resp.json();
    return json && json.data ? json.data.answer : null;
  }

  // 递归找出含 datas 数组的节点(不硬编码路径, 防 components 顺序变), 取第一个命中的股票列表。
  function findDatas(node, depth) {
    if (depth > 8 || node === null || typeof node !== 'object') return null;
    if (Array.isArray(node.datas) && node.datas.length && typeof node.datas[0] === 'object') return node.datas;
    for (const k of Object.keys(node)) {
      const hit = findDatas(node[k], depth + 1);
      if (hit) return hit;
    }
    return null;
  }

  const stripDate = (col) => col.replace(/\[[0-9\-]+\]$/, '').trim();
  const toNum = (v) => { const n = parseFloat(v); return isNaN(n) ? null : Math.round(n * 1e4) / 1e4; };

  // datas 原始行 → [{code, name, price, pct_change, extra}] (JS 侧 normalize, 四字段容错+去日期后缀)
  const EXTRA_MAP = {
    '技术形态': 'tech_pattern', '买入信号inter': 'buy_signal', '所属概念': 'concepts',
    '所属同花顺行业': 'industry', '换手率': 'turnover', '成交额': 'amount', 'a股市值(不含限售股)': 'free_cap',
  };
  function normalize(datas) {
    const out = [];
    for (const raw of datas) {
      const base = {};   // 去日期后缀的列名 → 原始列名
      for (const col of Object.keys(raw)) if (!(stripDate(col) in base)) base[stripDate(col)] = col;
      const pick = (...names) => { for (const n of names) if (base[n] != null) return raw[base[n]]; return undefined; };

      let code = String(raw.code || '').trim();
      if (!/^\d{6}$/.test(code)) {
        const m = String(pick('股票代码') || '').match(/(\d{6})/);
        code = m ? m[1] : '';
      }
      if (!/^\d{6}$/.test(code)) continue;

      const extra = {};
      for (const [k, key] of Object.entries(EXTRA_MAP)) {
        if (base[k] == null) continue;
        const v = raw[base[k]];
        if (v == null || v === '') continue;
        if (['turnover', 'amount', 'free_cap'].includes(key)) { const n = toNum(v); if (n != null) extra[key] = n; }
        else extra[key] = String(v).slice(0, 120);
      }
      out.push({
        code,
        name: String(pick('股票简称') || '').trim(),
        price: toNum(pick('最新价', '收盘价:前复权')),
        pct_change: toNum(pick('最新涨跌幅', '涨跌幅:前复权')),
        extra,
      });
    }
    return out;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // 跑指定的一批语句(每条: fetch问财→normalize→上报), 汇总弹窗。
  async function runList(queries, silent) {
    const log = [];
    if (!getCookie('v')) { const m = '⚠️ 没取到 v cookie, 请确认已登录 iwencai'; if (!silent) alert(m); return; }
    if (!queries.length) { if (!silent) alert('没有可跑的语句'); return; }
    for (let i = 0; i < queries.length; i++) {
      const q = queries[i];
      if (i > 0) await sleep(QUERY_INTERVAL_MS);
      try {
        const answer = await fetchWencai(q.query);
        const datas = findDatas(answer, 0);
        if (!datas) { log.push('· ' + q.name + ': 无结果(语句非筛选条件, 或风控/无匹配)'); continue; }
        const items = normalize(datas);
        const res = await gmPost('/api/wencai/ingest', {
          token: INGEST_TOKEN, strategy_id: q.strategy_id, strategy_name: q.name,
          query_text: q.query, items,
        });
        log.push('✓ ' + q.name + ': ' + (res.stock_count || 0) + ' 只');
      } catch (e) {
        log.push('✗ ' + q.name + ': ' + e.message);
        console.error('[wencai]', q.name, e);
      }
    }
    const summary = '问财代跑完成:\n\n' + log.join('\n');
    console.log('[wencai]', summary);
    if (!silent) alert(summary);
  }

  // 页面加载时拉一次语句清单, 为「全部 / 只跑自定义 / 每条单独」各注册一个油猴菜单命令,
  // 这样想跑哪条点哪条, 不必每次全跑一遍(降风控 + 省时)。语句在系统里增删改, 刷新菜单即同步。
  async function loadMenu() {
    if (INGEST_TOKEN === 'PUT_YOUR_TOKEN_HERE') {
      GM_registerMenuCommand('⚠️ 请先在脚本里填 INGEST_TOKEN', () => {});
      return;
    }
    let queries = [];
    try {
      const r = await gmPost('/api/wencai/ingest/queries', { token: INGEST_TOKEN });
      queries = r.queries || [];
    } catch (e) {
      GM_registerMenuCommand('↻ 拉语句清单失败, 点此重试', () => location.reload());
      console.error('[wencai] 拉清单失败', e);
      return;
    }
    GM_registerMenuCommand('▶ 跑全部语句 (' + queries.length + '条)', () => runList(queries, false));
    const custom = queries.filter((q) => /^u\d+_q/.test(q.strategy_id));
    if (custom.length) {
      GM_registerMenuCommand('▶ 只跑我的自定义 (' + custom.length + '条)', () => runList(custom, false));
    }
    for (const q of queries) {
      GM_registerMenuCommand('　└ 只跑: ' + q.name, () => runList([q], false));
    }
    GM_registerMenuCommand('↻ 刷新语句菜单(改了语句后点)', () => location.reload());
  }

  loadMenu();
})();
