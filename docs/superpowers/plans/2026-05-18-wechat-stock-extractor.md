# 微信聊天记录股票提取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从本机微信 PC 端群聊消息中自动识别股票代码，展示提取结果并支持一键加入股票池。

**Architecture:** 后端新增 `wechat_reader` 服务封装 PyWxDump（密钥提取、数据库解密、消息读取），`wechat` 路由提供 REST API。前端新增 `WechatView.vue` 页面，左侧群聊列表 + 右侧消息展示和股票提取结果。复用现有 `stocks.ts` API 实现加入股票池。

**Tech Stack:** Python PyWxDump, FastAPI, Vue 3 + Naive UI + TypeScript

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/services/wechat_reader.py` | PyWxDump 封装：密钥提取、DB 解密、消息读取、股票识别 |
| Create | `backend/routers/wechat.py` | REST API：status / connect / contacts / messages / extract-stocks |
| Create | `frontend/src/api/wechat.ts` | 前端 API 调用封装 |
| Create | `frontend/src/views/WechatView.vue` | 微信提取页面 |
| Modify | `backend/main.py:19` | 注册 wechat router |
| Modify | `frontend/src/router/index.ts:11` | 添加 /wechat 路由 |
| Modify | `frontend/src/components/layout/AppSidebar.vue:37-42` | 添加「微信提取」菜单项 |
| Modify | `frontend/src/data/changelog.ts:13` | 添加版本记录 |

---

### Task 1: 安装 PyWxDump 依赖

**Files:**
- Modify: `trading-monitor/` (pip install)

- [ ] **Step 1: 安装 pywxdump**

```bash
cd D:\财务管理\交易系统\trading-monitor
pip install pywxdump
```

- [ ] **Step 2: 验证安装**

```bash
python -c "from pywxdump import wx_core; print('OK')"
```

Expected: `OK`

---

### Task 2: 后端服务 — wechat_reader.py

**Files:**
- Create: `backend/services/wechat_reader.py`

- [ ] **Step 1: 创建 wechat_reader.py**

```python
import logging
import os
import re
import sqlite3
import shutil
import tempfile
import time
from datetime import datetime, timedelta

from backend import data_fetcher

logger = logging.getLogger(__name__)

_wx_key: str | None = None
_wx_dir: str | None = None
_decrypted_dir: str | None = None

STOCK_CODE_RE = re.compile(
    r'(?<!\d)'
    r'(60[0-9]{4}|00[0-9]{4}|30[0-9]{4}|68[0-9]{4})'
    r'(?!\d)'
)

DATE_RE = re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}')
PHONE_RE = re.compile(r'1[3-9]\d{9}')
AMOUNT_RE = re.compile(r'\d{6,}[元万亿%]')


def get_status() -> dict:
    try:
        from pywxdump import wx_core
        info_list = wx_core.get_wx_info()
        if not info_list:
            return {"wechat_running": False, "key_extracted": False, "data_dir": "", "db_count": 0}
        return {
            "wechat_running": True,
            "key_extracted": _wx_key is not None,
            "data_dir": _wx_dir or info_list[0].get("wx_dir", ""),
            "db_count": _count_msg_dbs(info_list[0].get("wx_dir", "")),
        }
    except Exception as e:
        logger.error(f"get_status failed: {e}")
        return {"wechat_running": False, "key_extracted": False, "data_dir": "", "db_count": 0}


def _count_msg_dbs(wx_dir: str) -> int:
    if not wx_dir or not os.path.isdir(wx_dir):
        return 0
    msg_dir = os.path.join(wx_dir, "Msg")
    if not os.path.isdir(msg_dir):
        return 0
    return len([f for f in os.listdir(msg_dir) if f.startswith("MSG") and f.endswith(".db")])


def connect() -> dict:
    global _wx_key, _wx_dir, _decrypted_dir
    try:
        from pywxdump import wx_core
        info_list = wx_core.get_wx_info()
        if not info_list:
            return {"ok": False, "msg": "未检测到微信进程，请确保微信已登录"}

        info = info_list[0]
        _wx_key = info.get("key", "")
        _wx_dir = info.get("wx_dir", "")

        if not _wx_key:
            return {"ok": False, "msg": "无法提取密钥，请确保微信已登录且未锁定"}

        _decrypted_dir = tempfile.mkdtemp(prefix="wx_decrypt_")
        db_count = _decrypt_databases()

        return {"ok": True, "msg": f"密钥提取成功，已解密 {db_count} 个数据库"}
    except Exception as e:
        logger.error(f"connect failed: {e}")
        return {"ok": False, "msg": f"连接失败: {e}"}


def _decrypt_databases() -> int:
    if not _wx_key or not _wx_dir or not _decrypted_dir:
        return 0

    from pywxdump.wx_core import decrypt as wx_decrypt

    msg_dir = os.path.join(_wx_dir, "Msg")
    count = 0

    micro_msg = os.path.join(msg_dir, "MicroMsg.db")
    if os.path.isfile(micro_msg):
        out = os.path.join(_decrypted_dir, "MicroMsg.db")
        try:
            wx_decrypt.decrypt(_wx_key, micro_msg, out)
            count += 1
        except Exception as e:
            logger.error(f"Decrypt MicroMsg.db failed: {e}")

    for f in os.listdir(msg_dir):
        if f.startswith("MSG") and f.endswith(".db"):
            src = os.path.join(msg_dir, f)
            out = os.path.join(_decrypted_dir, f)
            try:
                wx_decrypt.decrypt(_wx_key, src, out)
                count += 1
            except Exception as e:
                logger.error(f"Decrypt {f} failed: {e}")

    return count


def get_contacts(keyword: str = "") -> list[dict]:
    if not _decrypted_dir:
        return []

    micro_db = os.path.join(_decrypted_dir, "MicroMsg.db")
    if not os.path.isfile(micro_db):
        return []

    contacts = []
    try:
        conn = sqlite3.connect(micro_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT UserName, NickName, Remark, Type FROM Contact "
            "WHERE UserName LIKE '%@chatroom' ORDER BY NickName"
        )
        for row in cur.fetchall():
            wxid = row["UserName"]
            nickname = row["Remark"] or row["NickName"] or wxid
            if keyword and keyword not in nickname:
                continue
            msg_count = _count_messages(wxid)
            contacts.append({
                "wxid": wxid,
                "nickname": nickname,
                "type": "group",
                "msg_count": msg_count,
            })
        conn.close()
    except Exception as e:
        logger.error(f"get_contacts failed: {e}")

    contacts.sort(key=lambda c: c["msg_count"], reverse=True)
    return contacts


def _count_messages(wxid: str) -> int:
    if not _decrypted_dir:
        return 0
    total = 0
    for f in os.listdir(_decrypted_dir):
        if f.startswith("MSG") and f.endswith(".db"):
            try:
                conn = sqlite3.connect(os.path.join(_decrypted_dir, f))
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM MSG WHERE StrTalker=? AND Type=1", (wxid,))
                total += cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
    return total


def get_messages(wxid: str, page: int = 1, size: int = 50) -> dict:
    if not _decrypted_dir:
        return {"total": 0, "items": []}

    all_msgs = []
    for f in os.listdir(_decrypted_dir):
        if f.startswith("MSG") and f.endswith(".db"):
            db_path = os.path.join(_decrypted_dir, f)
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT localId, StrContent, IsSender, CreateTime, "
                    "StrTalker FROM MSG WHERE StrTalker=? AND Type=1 "
                    "ORDER BY CreateTime DESC",
                    (wxid,),
                )
                for row in cur.fetchall():
                    ts = row["CreateTime"]
                    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    all_msgs.append({
                        "id": row["localId"],
                        "sender": "我" if row["IsSender"] else "",
                        "content": row["StrContent"],
                        "timestamp": dt,
                        "type": "text",
                    })
                conn.close()
            except Exception as e:
                logger.error(f"Read {f} failed: {e}")

    all_msgs.sort(key=lambda m: m["timestamp"], reverse=True)
    total = len(all_msgs)
    start = (page - 1) * size
    items = all_msgs[start:start + size]
    return {"total": total, "items": items}


def extract_stocks(wxid: str, days: int = 7) -> dict:
    if not _decrypted_dir:
        return {"stocks": [], "total": 0}

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp())

    all_msgs = []
    for f in os.listdir(_decrypted_dir):
        if f.startswith("MSG") and f.endswith(".db"):
            db_path = os.path.join(_decrypted_dir, f)
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT StrContent, CreateTime FROM MSG "
                    "WHERE StrTalker=? AND Type=1 AND CreateTime>=? "
                    "ORDER BY CreateTime DESC",
                    (wxid, cutoff_ts),
                )
                all_msgs.extend(cur.fetchall())
                conn.close()
            except Exception:
                pass

    stock_map: dict[str, dict] = {}

    for msg in all_msgs:
        content = msg["StrContent"]
        ts = msg["CreateTime"]
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        if _is_false_positive_line(content):
            continue

        codes = STOCK_CODE_RE.findall(content)
        for code in codes:
            if _is_false_positive_code(code, content):
                continue
            if code not in stock_map:
                stock_map[code] = {
                    "code": code,
                    "name": "",
                    "mention_count": 0,
                    "latest_mention": dt,
                    "sample_messages": [],
                }
            stock_map[code]["mention_count"] += 1
            if len(stock_map[code]["sample_messages"]) < 3:
                snippet = content[:80]
                if snippet not in stock_map[code]["sample_messages"]:
                    stock_map[code]["sample_messages"].append(snippet)

    for code, info in stock_map.items():
        results = data_fetcher.search_stock(code)
        if results and results[0]["code"] == code:
            info["name"] = results[0]["name"]
        else:
            info["_invalid"] = True

    stocks = [s for s in stock_map.values() if not s.get("_invalid")]
    for s in stocks:
        s.pop("_invalid", None)

    stocks.sort(key=lambda s: s["mention_count"], reverse=True)
    return {"stocks": stocks, "total": len(stocks)}


def _is_false_positive_line(content: str) -> bool:
    if DATE_RE.search(content):
        cleaned = DATE_RE.sub("", content)
        if not STOCK_CODE_RE.search(cleaned):
            return True
    return False


def _is_false_positive_code(code: str, content: str) -> bool:
    idx = content.find(code)
    if idx < 0:
        return False

    before = content[max(0, idx - 10):idx]
    after = content[idx + 6:idx + 16]

    if re.search(r'[年月日/\-]$', before):
        return True
    if re.search(r'^[元万亿%]', after):
        return True
    if PHONE_RE.search(content[max(0, idx - 5):idx + 11]):
        return True

    return False
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd D:\财务管理\交易系统\trading-monitor
python -c "from backend.services import wechat_reader; print('OK')"
```

Expected: `OK`

---

### Task 3: 后端路由 — wechat.py

**Files:**
- Create: `backend/routers/wechat.py`

- [ ] **Step 1: 创建 wechat.py**

```python
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.services import wechat_reader

router = APIRouter(prefix="/api/wechat", tags=["wechat"])


@router.get("/status")
async def wechat_status(user: Annotated[dict, Depends(get_current_user)]):
    return wechat_reader.get_status()


@router.post("/connect")
async def wechat_connect(user: Annotated[dict, Depends(get_current_user)]):
    return wechat_reader.connect()


@router.get("/contacts")
async def wechat_contacts(
    user: Annotated[dict, Depends(get_current_user)],
    keyword: str = "",
):
    return wechat_reader.get_contacts(keyword)


@router.get("/messages")
async def wechat_messages(
    user: Annotated[dict, Depends(get_current_user)],
    wxid: str = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    return wechat_reader.get_messages(wxid, page, size)


class ExtractRequest(BaseModel):
    wxid: str
    days: int = 7


@router.post("/extract-stocks")
async def wechat_extract_stocks(
    req: ExtractRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    return wechat_reader.extract_stocks(req.wxid, req.days)
```

- [ ] **Step 2: 注册路由到 main.py**

在 `backend/main.py` 第 19 行的 import 行尾追加 `, wechat`：

```python
from backend.routers import stocks, signals, kline, search, config, scan, ws, ths, auth, users, logs, signal_config, popularity, wechat
```

在第 62 行 `app.include_router(popularity.router)` 之后追加：

```python
app.include_router(wechat.router)
```

- [ ] **Step 3: 验证后端启动**

```bash
cd D:\财务管理\交易系统\trading-monitor
python -c "from backend.routers import wechat; print('router prefix:', wechat.router.prefix)"
```

Expected: `router prefix: /api/wechat`

---

### Task 4: 前端 API 层 — wechat.ts

**Files:**
- Create: `frontend/src/api/wechat.ts`

- [ ] **Step 1: 创建 wechat.ts**

```typescript
import client from './client'

export interface WechatStatus {
  wechat_running: boolean
  key_extracted: boolean
  data_dir: string
  db_count: number
}

export interface WechatContact {
  wxid: string
  nickname: string
  type: string
  msg_count: number
}

export interface WechatMessage {
  id: number
  sender: string
  content: string
  timestamp: string
  type: string
}

export interface WechatMessagesResult {
  total: number
  items: WechatMessage[]
}

export interface ExtractedStock {
  code: string
  name: string
  mention_count: number
  latest_mention: string
  sample_messages: string[]
}

export interface ExtractStocksResult {
  stocks: ExtractedStock[]
  total: number
}

export async function fetchWechatStatus(): Promise<WechatStatus> {
  const { data } = await client.get('/api/wechat/status')
  return data
}

export async function connectWechat(): Promise<{ ok: boolean; msg: string }> {
  const { data } = await client.post('/api/wechat/connect')
  return data
}

export async function fetchWechatContacts(keyword = ''): Promise<WechatContact[]> {
  const { data } = await client.get('/api/wechat/contacts', { params: { keyword } })
  return data
}

export async function fetchWechatMessages(
  wxid: string,
  page = 1,
  size = 50,
): Promise<WechatMessagesResult> {
  const { data } = await client.get('/api/wechat/messages', { params: { wxid, page, size } })
  return data
}

export async function extractStocks(
  wxid: string,
  days = 7,
): Promise<ExtractStocksResult> {
  const { data } = await client.post('/api/wechat/extract-stocks', { wxid, days })
  return data
}
```

---

### Task 5: 前端页面 — WechatView.vue

**Files:**
- Create: `frontend/src/views/WechatView.vue`

- [ ] **Step 1: 创建 WechatView.vue**

```vue
<script setup lang="ts">
import { ref, computed, h } from 'vue'
import {
  NButton, NIcon, NInput, NDataTable, NTag, NSkeleton,
  NEmpty, NSelect, useMessage,
} from 'naive-ui'
import {
  SearchOutline, CloudDownloadOutline, RefreshOutline,
  ChatbubbleEllipsesOutline, AddOutline,
} from '@vicons/ionicons5'
import {
  fetchWechatStatus, connectWechat, fetchWechatContacts,
  fetchWechatMessages, extractStocks,
  type WechatStatus, type WechatContact, type WechatMessage, type ExtractedStock,
} from '../api/wechat'
import { addStock } from '../api/stocks'

const message = useMessage()

const status = ref<WechatStatus | null>(null)
const connected = computed(() => status.value?.key_extracted ?? false)
const connecting = ref(false)

const contacts = ref<WechatContact[]>([])
const contactKeyword = ref('')
const contactsLoading = ref(false)
const selectedContact = ref<WechatContact | null>(null)

const messages = ref<WechatMessage[]>([])
const msgTotal = ref(0)
const msgPage = ref(1)
const msgLoading = ref(false)

const extractedStocks = ref<ExtractedStock[]>([])
const extractLoading = ref(false)
const extractDays = ref(7)
const addingCode = ref<string | null>(null)
const addedCodes = ref<Set<string>>(new Set())

async function handleConnect() {
  connecting.value = true
  try {
    const result = await connectWechat()
    if (result.ok) {
      message.success(result.msg)
      status.value = await fetchWechatStatus()
      await loadContacts()
    } else {
      message.error(result.msg)
    }
  } catch {
    message.error('连接微信失败')
  } finally {
    connecting.value = false
  }
}

async function loadContacts() {
  contactsLoading.value = true
  try {
    contacts.value = await fetchWechatContacts(contactKeyword.value)
  } catch {
    message.error('加载群聊列表失败')
  } finally {
    contactsLoading.value = false
  }
}

async function selectContact(contact: WechatContact) {
  selectedContact.value = contact
  msgPage.value = 1
  extractedStocks.value = []
  addedCodes.value = new Set()
  await loadMessages()
  await handleExtract()
}

async function loadMessages() {
  if (!selectedContact.value) return
  msgLoading.value = true
  try {
    const result = await fetchWechatMessages(selectedContact.value.wxid, msgPage.value)
    messages.value = result.items
    msgTotal.value = result.total
  } catch {
    message.error('加载消息失败')
  } finally {
    msgLoading.value = false
  }
}

async function handleExtract() {
  if (!selectedContact.value) return
  extractLoading.value = true
  try {
    const result = await extractStocks(selectedContact.value.wxid, extractDays.value)
    extractedStocks.value = result.stocks
  } catch {
    message.error('提取股票失败')
  } finally {
    extractLoading.value = false
  }
}

async function handleAddStock(code: string, name: string) {
  addingCode.value = code
  try {
    await addStock({ code, name })
    addedCodes.value.add(code)
    message.success(`${code} ${name} 已加入股票池`)
  } catch {
    message.error(`添加 ${code} 失败`)
  } finally {
    addingCode.value = null
  }
}

async function handleAddAll() {
  for (const s of extractedStocks.value) {
    if (!addedCodes.value.has(s.code)) {
      await handleAddStock(s.code, s.name)
    }
  }
}

function highlightStockCodes(content: string): string {
  return content.replace(
    /(?<!\d)(60\d{4}|00\d{4}|30\d{4}|68\d{4})(?!\d)/g,
    '<span class="stock-code-hl">$1</span>',
  )
}

const stockColumns = [
  {
    title: '代码',
    key: 'code',
    width: 80,
    render: (row: ExtractedStock) =>
      h('span', { style: { fontFamily: 'monospace', color: 'var(--primary)' } }, row.code),
  },
  { title: '名称', key: 'name', width: 90 },
  { title: '提及次数', key: 'mention_count', width: 80 },
  { title: '最近提及', key: 'latest_mention', width: 150 },
  {
    title: '操作',
    key: 'action',
    width: 100,
    render: (row: ExtractedStock) =>
      h(
        NButton,
        {
          size: 'small',
          type: 'primary',
          disabled: addedCodes.value.has(row.code),
          loading: addingCode.value === row.code,
          onClick: () => handleAddStock(row.code, row.name),
        },
        {
          icon: () => h(NIcon, null, { default: () => h(AddOutline) }),
          default: () => (addedCodes.value.has(row.code) ? '已添加' : '加自选'),
        },
      ),
  },
]
</script>

<template>
  <div>
    <div class="top-bar">
      <div class="status-row">
        <span :class="['status-dot', { online: connected }]" />
        <span class="status-text">{{ connected ? '微信已连接' : '微信未连接' }}</span>
      </div>
      <NButton
        type="primary"
        size="small"
        :loading="connecting"
        @click="handleConnect"
      >
        <template #icon><NIcon><CloudDownloadOutline /></NIcon></template>
        {{ connected ? '刷新数据' : '连接微信' }}
      </NButton>
    </div>

    <div v-if="connected" class="main-layout">
      <div class="contact-panel">
        <div class="panel-title">群聊列表</div>
        <NInput
          v-model:value="contactKeyword"
          placeholder="搜索群聊..."
          size="small"
          clearable
          @update:value="loadContacts"
          style="margin-bottom: 8px"
        />
        <NSkeleton v-if="contactsLoading" :repeat="5" text />
        <div v-else-if="contacts.length === 0" class="empty-hint">无群聊数据</div>
        <div v-else class="contact-list">
          <div
            v-for="c in contacts"
            :key="c.wxid"
            :class="['contact-item', { active: selectedContact?.wxid === c.wxid }]"
            @click="selectContact(c)"
          >
            <NIcon :component="ChatbubbleEllipsesOutline" :size="14" class="contact-icon" />
            <span class="contact-name">{{ c.nickname }}</span>
            <NTag size="tiny" :bordered="false">{{ c.msg_count }}条</NTag>
          </div>
        </div>
      </div>

      <div class="content-panel">
        <template v-if="selectedContact">
          <div class="msg-section">
            <div class="section-header">
              <span>{{ selectedContact.nickname }} 的消息</span>
              <div style="display: flex; gap: 8px; align-items: center">
                <span class="page-info">第 {{ msgPage }} 页 / 共 {{ msgTotal }} 条</span>
                <NButton size="small" type="primary" :disabled="msgPage <= 1" @click="msgPage--; loadMessages()">上一页</NButton>
                <NButton size="small" type="primary" :disabled="msgPage * 50 >= msgTotal" @click="msgPage++; loadMessages()">下一页</NButton>
              </div>
            </div>
            <NSkeleton v-if="msgLoading" :repeat="5" text />
            <div v-else class="msg-list">
              <div v-for="msg in messages" :key="msg.id" class="msg-item">
                <span class="msg-time">{{ msg.timestamp }}</span>
                <span v-if="msg.sender" class="msg-sender">{{ msg.sender }}</span>
                <span class="msg-content" v-html="highlightStockCodes(msg.content)" />
              </div>
            </div>
          </div>

          <div class="stock-section">
            <div class="section-header">
              <span>提取到的股票</span>
              <div style="display: flex; gap: 8px; align-items: center">
                <NSelect
                  v-model:value="extractDays"
                  :options="[
                    { label: '最近3天', value: 3 },
                    { label: '最近7天', value: 7 },
                    { label: '最近30天', value: 30 },
                  ]"
                  size="small"
                  style="width: 120px"
                />
                <NButton size="small" type="primary" :loading="extractLoading" @click="handleExtract">
                  <template #icon><NIcon><SearchOutline /></NIcon></template>
                  提取
                </NButton>
              </div>
            </div>

            <NSkeleton v-if="extractLoading" :repeat="3" text />
            <template v-else-if="extractedStocks.length">
              <div class="table-summary">
                共提取 {{ extractedStocks.length }} 只股票
                <NButton
                  size="small"
                  type="primary"
                  style="margin-left: 12px"
                  @click="handleAddAll"
                >
                  全部加入股票池
                </NButton>
              </div>
              <NDataTable
                :columns="stockColumns"
                :data="extractedStocks"
                :bordered="false"
                size="small"
                :row-key="(row: ExtractedStock) => row.code"
              />
            </template>
            <NEmpty v-else description="暂无提取结果" />
          </div>
        </template>
        <NEmpty v-else description="请从左侧选择一个群聊" style="margin-top: 80px" />
      </div>
    </div>

    <NEmpty v-else-if="!connecting" description="请先连接微信" style="margin-top: 80px" />
  </div>
</template>

<style scoped>
.top-bar {
  background: var(--surface);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 12px 20px;
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #ccc;
}
.status-dot.online {
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
}
.status-text {
  font-size: 14px;
  font-weight: 600;
}
.main-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  align-items: start;
}
.contact-panel {
  background: var(--surface);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 12px;
  position: sticky;
  top: 0;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}
.panel-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text1);
}
.contact-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.contact-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.15s;
}
.contact-item:hover {
  background: rgba(46, 158, 255, 0.06);
}
.contact-item.active {
  background: rgba(46, 158, 255, 0.12);
  color: var(--primary);
  font-weight: 600;
}
.contact-icon {
  flex-shrink: 0;
  color: var(--text2);
}
.contact-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.content-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.msg-section,
.stock-section {
  background: var(--surface);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 16px 20px;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--text1);
  margin-bottom: 12px;
}
.page-info {
  font-size: 12px;
  color: var(--text2);
  font-weight: 400;
}
.msg-list {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.msg-item {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
  padding: 4px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}
.msg-time {
  color: var(--text2);
  font-size: 12px;
  flex-shrink: 0;
  width: 130px;
}
.msg-sender {
  color: var(--primary);
  font-weight: 600;
  flex-shrink: 0;
  min-width: 30px;
}
.msg-content {
  flex: 1;
  word-break: break-all;
}
:deep(.stock-code-hl) {
  color: var(--red);
  font-weight: 700;
  font-family: monospace;
}
.table-summary {
  text-align: right;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
.empty-hint {
  text-align: center;
  padding: 20px;
  color: var(--text2);
  font-size: 13px;
}
</style>
```

---

### Task 6: 注册路由和侧边栏菜单

**Files:**
- Modify: `frontend/src/router/index.ts:11`
- Modify: `frontend/src/components/layout/AppSidebar.vue:9,41`

- [ ] **Step 1: 添加前端路由**

在 `frontend/src/router/index.ts` 第 11 行 `popularity` 路由后插入：

```typescript
    { path: '/wechat', name: 'wechat', component: () => import('../views/WechatView.vue') },
```

- [ ] **Step 2: 添加侧边栏菜单**

在 `frontend/src/components/layout/AppSidebar.vue` 第 9 行 import 中追加 `ChatbubbleEllipsesOutline`：

```typescript
import {
  PulseOutline, LayersOutline, TimeOutline,
  ReaderOutline, SettingsOutline, PeopleOutline,
  OptionsOutline, RocketOutline, TrendingUpOutline,
  ChatbubbleEllipsesOutline,
} from '@vicons/ionicons5'
```

在第 41 行 `signal-config` 后、`]` 闭合前插入新菜单项：

```typescript
      { key: 'wechat', label: '微信提取', path: '/wechat', icon: ChatbubbleEllipsesOutline },
```

---

### Task 7: 更新版本记录

**Files:**
- Modify: `frontend/src/data/changelog.ts:13`

- [ ] **Step 1: 添加版本记录**

在 `changelog` 数组第一个元素前（第 14 行）插入新版本：

```typescript
  {
    version: 'v1.6.0',
    date: '2026-05-18',
    title: '微信聊天股票提取',
    changes: [
      { text: '新增微信聊天记录读取功能，自动解密本地微信数据库', tag: 'new' },
      { text: '支持从群聊消息中自动识别 A 股代码（沪深主板/创业板/科创板）', tag: 'new' },
      { text: '提取到的股票可一键或批量加入股票池', tag: 'new' },
      { text: '智能过滤日期、金额、电话号码等误识别', tag: 'improve' },
    ],
  },
```

---

### Task 8: 验证和修复

- [ ] **Step 1: 启动后端验证 API**

```bash
cd D:\财务管理\交易系统\trading-monitor
python -m backend.main
```

验证 `/api/wechat/status` 能正常响应。

- [ ] **Step 2: 构建前端验证页面**

```bash
cd D:\财务管理\交易系统\trading-monitor\frontend
npm run build
```

验证无编译错误。

- [ ] **Step 3: 浏览器测试**

访问系统，确认：
1. 侧边栏出现「微信提取」菜单
2. 点击进入页面，显示「微信未连接」状态
3. 点击「连接微信」（微信需已登录）→ 显示群聊列表
4. 选择群聊 → 消息列表和股票提取正常
5. 一键加入股票池功能正常
