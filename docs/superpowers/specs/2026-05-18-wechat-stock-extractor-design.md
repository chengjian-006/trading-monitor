# 微信聊天记录股票提取功能 - 设计规格

## 概述

在交易监控系统中新增「微信提取」功能模块，从本机微信 PC 端的群聊消息中自动识别股票代码，展示提取结果并支持一键加入股票池。

## 技术方案

使用 PyWxDump 开源库读取微信本地加密数据库：
1. 微信 PC 端登录状态下，从进程内存提取 SQLCipher 解密密钥
2. 解密本地 MSG*.db 数据库文件
3. 读取消息记录，正则匹配股票代码

## 新增文件

### 后端

- `backend/services/wechat_reader.py` — 封装 PyWxDump，提供密钥提取、数据库解密、消息读取、股票识别功能
- `backend/routers/wechat.py` — REST API 路由

### 前端

- `frontend/src/views/WechatView.vue` — 微信提取页面
- `frontend/src/api/wechat.ts` — API 调用封装

### 修改文件

- `backend/main.py` — 注册 wechat router
- `frontend/src/router/index.ts` — 添加 /wechat 路由
- `frontend/src/components/layout/AppSidebar.vue` — 添加菜单项
- `frontend/src/data/changelog.ts` — 版本记录

## API 设计

### GET /api/wechat/status

检查微信运行状态和密钥获取情况。

**Response:**
```json
{
  "wechat_running": true,
  "key_extracted": true,
  "data_dir": "C:\\Users\\xxx\\Documents\\WeChat Files\\wxid_xxx",
  "db_count": 5
}
```

### POST /api/wechat/connect

从微信进程提取解密密钥并解密数据库。

**Response:**
```json
{
  "ok": true,
  "msg": "密钥提取成功，已解密 5 个数据库"
}
```

### GET /api/wechat/contacts?keyword=股票

获取群聊/联系人列表，支持关键词搜索。

**Response:**
```json
[
  {
    "wxid": "xxx@chatroom",
    "nickname": "股票交流群",
    "type": "group",
    "msg_count": 1523
  }
]
```

### GET /api/wechat/messages?wxid=xxx@chatroom&page=1&size=50

获取指定联系人/群聊的消息列表，分页加载。

**Response:**
```json
{
  "total": 1523,
  "items": [
    {
      "id": 1,
      "sender": "张三",
      "content": "600519 贵州茅台 看好",
      "timestamp": "2026-05-18 10:30:00",
      "type": "text"
    }
  ]
}
```

### POST /api/wechat/extract-stocks

从指定群聊消息中提取股票代码。

**Request:**
```json
{
  "wxid": "xxx@chatroom",
  "days": 7
}
```

**Response:**
```json
{
  "stocks": [
    {
      "code": "600519",
      "name": "贵州茅台",
      "mention_count": 5,
      "latest_mention": "2026-05-18 10:30:00",
      "sample_messages": ["600519 贵州茅台 看好", "茅台今天走势不错"]
    }
  ],
  "total": 12
}
```

## 股票识别规则

正则匹配：
- 沪市主板: `60[0-9]{4}`
- 深市主板: `00[0-9]{4}`
- 创业板: `30[0-9]{4}`
- 科创板: `68[0-9]{4}`

排除误识别：
- 排除日期格式 (`2026-05-18` 中的 `260518`)
- 排除金额格式 (`100000元`)
- 排除电话号码等连续数字

匹配到 6 位代码后，调用 data_fetcher 验证是否为有效股票代码并获取股票名称。

## 前端页面设计

页面分为三个区域：

### 顶部状态栏
- 微信连接状态指示灯（绿色已连接 / 红色未连接）
- 「连接微信」/「刷新数据」按钮

### 左侧面板 - 群聊列表
- 搜索框筛选群聊
- 群聊列表，显示群名和消息数量
- 点击选中群聊

### 右侧面板 - 分为上下两部分

**上部 - 消息列表：**
- 展示选中群聊的文本消息
- 消息中的股票代码高亮显示
- 分页加载，默认最近7天

**下部 - 提取结果：**
- 右上角汇总信息（共提取 N 只股票）
- 表格展示：股票代码、名称、提及次数、最近提及时间
- 每行「加入股票池」按钮（size="small" type="primary"）
- 「全部加入股票池」批量操作按钮

## 依赖

新增 Python 依赖：
- `pywxdump` — 微信数据库解密核心库

## 注意事项

- 微信必须处于 PC 端登录状态才能提取密钥
- 解密操作在后端执行，解密后的数据不持久化存储，仅内存中临时使用
- 所有时间显示使用空格替换 T（如 `2026-05-18 10:30:00`）
- 按钮统一 size="small" type="primary"
