import client from './client'

// 藏龙岛观点 (v1.7.738) — 飞书群群主(藏龙岛)盘中点评/操作观点的存档。
// 后端定时拉飞书群、只留藏龙岛发的入库(不推送, 用户本人飞书已收到原消息)。
export interface CoachPost {
  id: number
  message_id: string
  coach_name: string
  posted_at: string   // 'YYYY-MM-DD HH:mm'
  content: string
  msg_type: string
}

export async function listCoachPosts(limit = 100, offset = 0): Promise<{ posts: CoachPost[] }> {
  const { data } = await client.get('/api/lark-coach/posts', { params: { limit, offset } })
  return data
}
