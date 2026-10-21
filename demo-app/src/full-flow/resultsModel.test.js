import assert from 'node:assert/strict'
import test from 'node:test'

import { groupedBars, heatmapRows, metricOptions, seriesSlot } from './resultsModel.js'

const cell = (method, dataset, status, values = {}, extra = {}) => ({
  method, dataset, status, values, not_computable: {}, cost: { tokens_total: null, latency_s: null }, ...extra,
})

const matrix = {
  methods: ['c3sql', 'dinsql'],
  datasets: ['spider', 'bird'],
  metrics: [
    { id: 'ex', label: 'Execution accuracy', higher_is_better: true },
    { id: 'em', label: 'Exact match', higher_is_better: true },
    { id: 'ves', label: 'Valid efficiency score', higher_is_better: true },
  ],
  cells: [
    cell('c3sql', 'spider', 'ok', { ex: 0.8, em: 0.36, ves: 1.25 }),
    cell('c3sql', 'bird', 'missing'),
    cell('dinsql', 'spider', 'ok', { ex: 0.77, em: null, ves: 0.5 }, { not_computable: { em: 'needs gold SQL' } }),
    cell('dinsql', 'bird', 'unaligned', { ex: 0.4 }),
  ],
}

test('metricOptions puts execution accuracy first', () => {
  assert.deepEqual(metricOptions(matrix).map(item => item.id), ['ex', 'em', 'ves'])
  assert.deepEqual(metricOptions({ metrics: [] }), [])
})

test('heatmap rows follow the matrix axes and format unit metrics as percentages', () => {
  const rows = heatmapRows(matrix, 'ex')
  assert.deepEqual(rows.map(row => row.method), ['c3sql', 'dinsql'])
  assert.deepEqual(rows[0].cells.map(item => item.dataset), ['spider', 'bird'])
  assert.equal(rows[0].cells[0].text, '80.0%')
  assert.equal(rows[1].cells[0].text, '77.0%')
})

test('heatmap shade steps rise with the value and stay inside the ramp', () => {
  const [high, low] = [heatmapRows(matrix, 'ex')[0].cells[0], heatmapRows(matrix, 'ex')[1].cells[0]]
  assert.equal(high.step, 5)
  assert.equal(low.step, 5)
  const perfect = heatmapRows({ ...matrix, cells: [cell('c3sql', 'spider', 'ok', { ex: 1 })] }, 'ex')[0].cells[0]
  assert.equal(perfect.step, 6)
  const zero = heatmapRows({ ...matrix, cells: [cell('c3sql', 'spider', 'ok', { ex: 0 })] }, 'ex')[0].cells[0]
  assert.equal(zero.step, 0)
})

test('cells without a comparable value carry a status instead of a shade', () => {
  const rows = heatmapRows(matrix, 'ex')
  const missing = rows[0].cells[1]
  assert.equal(missing.status, 'missing')
  assert.equal(missing.step, null)
  assert.equal(missing.text, 'missing')
  const unaligned = rows[1].cells[1]
  assert.equal(unaligned.status, 'unaligned')
  assert.equal(unaligned.step, null, 'an unaligned score is never shaded as comparable')
  const notComputable = heatmapRows(matrix, 'em')[1].cells[0]
  assert.equal(notComputable.status, 'not_computable')
  assert.equal(notComputable.text, '—')
  assert.match(notComputable.tooltip, /needs gold SQL/)
})

test('metrics that exceed 1 are shown as numbers and shaded against the largest value', () => {
  const rows = heatmapRows(matrix, 'ves')
  assert.equal(rows[0].cells[0].text, '1.25')
  assert.equal(rows[0].cells[0].step, 6)
  assert.equal(rows[1].cells[0].text, '0.50')
  assert.equal(rows[1].cells[0].step, 2)
})

test('series slots follow the method, not its position in the current selection', () => {
  const all = ['c3sql', 'dinsql', 'finsql']
  assert.equal(seriesSlot('finsql', all), 3)
  assert.equal(seriesSlot('c3sql', all), 1)
  assert.equal(seriesSlot('unknown', all), null)
})

test('grouped bars hold one group per benchmark and only comparable values', () => {
  const groups = groupedBars(matrix, 'ex', ['c3sql', 'dinsql'])
  assert.deepEqual(groups.map(group => group.dataset), ['spider', 'bird'])
  assert.deepEqual(groups[0].bars, [
    { method: 'c3sql', value: 0.8, text: '80.0%', slot: 1 },
    { method: 'dinsql', value: 0.77, text: '77.0%', slot: 2 },
  ])
  assert.deepEqual(groups[1].bars, [], 'missing and unaligned cells are not drawn as bars')
  assert.deepEqual(groups[1].absent, [
    { method: 'c3sql', status: 'missing' },
    { method: 'dinsql', status: 'unaligned' },
  ])
})
