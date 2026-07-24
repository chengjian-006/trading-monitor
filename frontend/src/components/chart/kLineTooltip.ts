import type { DailyMarker } from '../../api/kline'

function directionText(direction: string): string {
  return direction === 'buy' ? '买' : (direction === 'reduce' ? '减' : '卖')
}

/** Populate the production K-line tooltip without parsing marker data as HTML. */
export function renderKLineTooltip(
  tip: HTMLElement,
  date: string,
  hits: readonly DailyMarker[],
): void {
  tip.replaceChildren()

  const dateEl = document.createElement('div')
  dateEl.className = 'mk-date'
  dateEl.textContent = date
  tip.append(dateEl)

  for (const hit of hits) {
    const directionClass = hit.direction === 'buy'
      ? 'buy'
      : (hit.direction === 'reduce' ? 'reduce' : 'sell')
    const price = hit.price != null ? ` ¥${hit.price}` : ''
    const time = hit.time ? ` ${hit.time}` : ''

    const row = document.createElement('div')
    row.className = 'mk-row'

    const tag = document.createElement('span')
    tag.classList.add('mk-tag', directionClass)
    tag.textContent = directionText(hit.direction)

    const name = document.createElement('span')
    name.className = 'mk-name'
    name.textContent = hit.signal_name || ''

    const meta = document.createElement('span')
    meta.className = 'mk-meta'
    meta.textContent = `${time}${price}`

    row.append(tag, name, meta)
    tip.append(row)
  }
}
