import DOMPurify from 'dompurify'

const STRIP_SECTIONS = ['全球股市', 'A股大盘概况', '大盘概况', '市场温度']

function stripDataSections(html: string): string {
  let out = html
  for (const title of STRIP_SECTIONS) {
    const re = new RegExp(
      `<h3[^>]*>\\s*${title}[^<]*(?:<[^>]*>[^<]*<\\/[^>]*>[^<]*)*<\\/h3>[\\s\\S]*?(?=<h3|$)`,
      'g',
    )
    out = out.replace(re, '')
  }
  return out
}

/** Render untrusted AI-report content for the production v-html sink. */
export function renderReportContent(text: string): string {
  if (!text) return ''

  let rendered: string
  if (text.includes('<table') || text.includes('<h3')) {
    rendered = stripDataSections(text)
  } else {
    rendered = text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/\n/g, '<br>')
      .replace(/<br><blockquote>/g, '<blockquote>')
      .replace(/<\/blockquote><br>/g, '</blockquote>')
  }

  return DOMPurify.sanitize(rendered, { USE_PROFILES: { html: true } })
}
