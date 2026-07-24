import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const sourceRoot = new URL('./src/', import.meta.url)
const signalSource = await readFile(new URL('views/SignalView.vue', sourceRoot), 'utf8')
const chartSource = await readFile(new URL('components/chart/KLineChart.vue', sourceRoot), 'utf8')

assert.match(
  signalSource,
  /DOMPurify\.sanitize\(/,
  'report renderer must sanitize untrusted HTML with DOMPurify',
)
assert.doesNotMatch(
  chartSource,
  /\.innerHTML\s*=/,
  'chart tooltip must not assign untrusted values through innerHTML',
)
assert.match(
  chartSource,
  /\.textContent\s*=/,
  'chart tooltip must render untrusted values through textContent',
)

const [{ default: createDOMPurify }, { JSDOM }] = await Promise.all([
  import('dompurify'),
  import('jsdom'),
])
const window = new JSDOM('').window
const DOMPurify = createDOMPurify(window)

const reportPayload = [
  '<h3>Safe heading</h3>',
  '<strong>Safe emphasis</strong>',
  '<table><tr><td>Safe cell</td></tr></table>',
  '<img src=x onerror="globalThis.reportPwned=true">',
  '<script>globalThis.reportPwned=true</script>',
  '<a href="javascript:globalThis.reportPwned=true">bad link</a>',
].join('')
const sanitizedReport = DOMPurify.sanitize(reportPayload)

assert.match(sanitizedReport, /<h3>Safe heading<\/h3>/)
assert.match(sanitizedReport, /<strong>Safe emphasis<\/strong>/)
assert.match(sanitizedReport, /<table>/)
assert.doesNotMatch(sanitizedReport, /onerror|<script|javascript:/i)

const markerPayload = '<img src=x onerror="globalThis.chartPwned=true">marker'
const markerName = window.document.createElement('span')
markerName.textContent = markerPayload

assert.equal(markerName.textContent, markerPayload)
assert.equal(markerName.querySelector('img'), null)
assert.match(markerName.innerHTML, /^&lt;img /)

console.log('Security injection checks passed: report HTML sanitized; chart text escaped.')
