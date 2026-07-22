import client from './client'

// 藏龙岛观点 (v1.7.738) — 飞书群群主(藏龙岛)盘中点评/操作观点的存档。
// 后端定时拉飞书群、只留藏龙岛发的入库(不推送, 用户本人飞书已收到原消息)。
export interface CoachMentionedStock {
  code: string
  name: string
  primary?: boolean
}

export interface CoachPost {
  id: number
  message_id: string
  coach_name: string
  posted_at: string   // 'YYYY-MM-DD HH:mm'
  content: string
  msg_type: string
  stocks?: CoachMentionedStock[]   // 正文里被提及的个股(后端撞名), 前端标成同花顺个股页链接
}

export async function listCoachPosts(limit = 100, offset = 0): Promise<{ posts: CoachPost[] }> {
  const { data } = await client.get('/api/lark-coach/posts', { params: { limit, offset } })
  return data
}

// 图片消息取图: 接口要带 JWT, <img src> 直连带不上, 拉 blob 转 objectURL 显示
export async function fetchCoachMedia(messageId: string): Promise<string> {
  const { data } = await client.get(`/api/lark-coach/media/${messageId}`, { responseType: 'blob' })
  return URL.createObjectURL(data)
}

// 转发形式(管理员): card=蓝头卡片 / text=纯文本, 后台系统设置页切换
export type CoachRelayStyle = 'card' | 'text'

export async function fetchCoachRelayStyle(): Promise<CoachRelayStyle> {
  const { data } = await client.get('/api/lark-coach/relay-style')
  return data.style === 'text' ? 'text' : 'card'
}

export async function saveCoachRelayStyle(style: CoachRelayStyle): Promise<void> {
  await client.post('/api/lark-coach/relay-style', { style })
}
