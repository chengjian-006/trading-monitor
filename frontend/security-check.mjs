import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'https://app.guxiaocha.com/',
})
const { window } = dom

// DOMPurify binds to window when the production rendering module is imported.
Object.assign(globalThis, {
  window,
  document: window.document,
  Node: window.Node,
  Element: window.Element,
  HTMLElement: window.HTMLElement,
  DocumentFragment: window.DocumentFragment,
  HTMLTemplateElement: window.HTMLTemplateElement,
})

const [{ renderReportContent }, { renderKLineTooltip }] = await Promise.all([
  import('./src/views/signalReportRendering.ts'),
  import('./src/components/chart/kLineTooltip.ts'),
])

function assertNoExecutableMarkup(root, label) {
  assert.equal(
    root.querySelector('script, iframe, object, embed, svg, math'),
    null,
    `${label} must not create executable or embedded nodes`,
  )

  for (const element of root.querySelectorAll('*')) {
    for (const attribute of element.attributes) {
      assert.doesNotMatch(
        attribute.name,
        /^on/i,
        `${label} must not preserve event-handler attributes`,
      )
      if (/^(?:href|src|xlink:href|formaction)$/i.test(attribute.name)) {
        assert.doesNotMatch(
          attribute.value.trim(),
          /^(?:javascript|vbscript|data\s*:\s*text\/html)/i,
          `${label} must not preserve dangerous URLs`,
        )
      }
    }
  }
}

// Exercise the exact function used by SignalView.vue's v-html bindings.
const reportPayload = [
  '<h3>Safe heading</h3>',
  '<strong>Safe emphasis</strong>',
  '<table><tbody><tr><td>Safe cell</td></tr></tbody></table>',
  '<img id="event-payload" src="x" onerror="globalThis.reportPwned=true">',
  '<script id="script-payload">globalThis.reportPwned=true</script>',
  '<a id="javascript-url" href="javascript:globalThis.reportPwned=true">bad link</a>',
  '<a id="data-url" href="data:text/html,<script>globalThis.reportPwned=true</script>">bad data</a>',
  '<iframe id="injected-frame" srcdoc="<script>globalThis.reportPwned=true</script>"></iframe>',
  '<svg id="injected-svg" onload="globalThis.reportPwned=true"></svg>',
].join('')
const reportHost = window.document.createElement('section')
reportHost.innerHTML = renderReportContent(reportPayload)

assert.equal(reportHost.querySelector('h3')?.textContent, 'Safe heading')
assert.equal(reportHost.querySelector('strong')?.textContent, 'Safe emphasis')
assert.equal(reportHost.querySelector('td')?.textContent, 'Safe cell')
assert.equal(reportHost.querySelector('#event-payload')?.hasAttribute('onerror'), false)
assert.equal(reportHost.querySelector('#javascript-url')?.hasAttribute('href'), false)
assert.equal(reportHost.querySelector('#data-url')?.hasAttribute('href'), false)
assert.equal(reportHost.querySelector('#script-payload, #injected-frame, #injected-svg'), null)
assertNoExecutableMarkup(reportHost, 'report renderer')

const markdownHost = window.document.createElement('section')
markdownHost.innerHTML = renderReportContent(
  '**Safe markdown**\n<img id="markdown-injection" src=x onerror="globalThis.reportPwned=true">',
)
assert.equal(markdownHost.querySelector('strong')?.textContent, 'Safe markdown')
assert.equal(markdownHost.querySelector('#markdown-injection'), null)
assertNoExecutableMarkup(markdownHost, 'markdown renderer')

// Exercise the exact function used by KLineChart.vue's crosshair callback.
const tooltip = window.document.createElement('div')
const datePayload = '<img id="date-injection" src=x onerror="globalThis.chartPwned=true">'
const namePayload = '<script id="name-injection">globalThis.chartPwned=true</script>'
const timePayload = '<a id="url-injection" href="javascript:globalThis.chartPwned=true">bad</a>'
const pricePayload = '<iframe id="price-injection" srcdoc="<script>globalThis.chartPwned=true</script>">'
renderKLineTooltip(tooltip, datePayload, [{
  date: '2026-07-24',
  direction: 'buy onmouseover="globalThis.chartPwned=true"',
  signal_name: namePayload,
  time: timePayload,
  price: pricePayload,
}])

assert.equal(tooltip.children.length, 2)
assert.equal(tooltip.querySelector('.mk-date')?.textContent, datePayload)
assert.equal(tooltip.querySelector('.mk-name')?.textContent, namePayload)
assert.match(tooltip.querySelector('.mk-meta')?.textContent ?? '', /javascript:/)
assert.match(tooltip.querySelector('.mk-meta')?.textContent ?? '', /<iframe/)
assert.deepEqual(
  [...(tooltip.querySelector('.mk-tag')?.classList ?? [])],
  ['mk-tag', 'sell'],
)
assert.equal(tooltip.querySelector('script, img, a, iframe, svg, #name-injection'), null)
assertNoExecutableMarkup(tooltip, 'chart tooltip renderer')

dom.window.close()
console.log('Security injection checks passed through production report and tooltip renderers.')
