import assert from 'node:assert/strict'
import test from 'node:test'

import {
  costScatter,
  diffCandidates,
  displayNamesFor,
  groupedBars,
  heatmapRows,
  metricOptions,
  seriesSlot,
} from './resultsModel.js'

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
    { method: 'c3sql', label: 'c3sql', value: 0.8, text: '80.0%', slot: 1 },
    { method: 'dinsql', label: 'dinsql', value: 0.77, text: '77.0%', slot: 2 },
  ])
  assert.deepEqual(groups[1].bars, [], 'missing and unaligned cells are not drawn as bars')
  assert.deepEqual(groups[1].absent, [
    { method: 'c3sql', label: 'c3sql', status: 'missing' },
    { method: 'dinsql', label: 'dinsql', status: 'unaligned' },
  ])
})

const costed = {
  methods: ['c3sql', 'dinsql', 'finsql'],
  datasets: ['spider'],
  metrics: [{ id: 'ex', label: 'Execution accuracy' }],
  cells: [
    cell('c3sql', 'spider', 'ok', { ex: 0.8 }, { run_id: 'run-c3', cost: { tokens_total: 9000, latency_s: 38.5 } }),
    cell('dinsql', 'spider', 'ok', { ex: 0.77 }, { run_id: 'run-din', cost: { tokens_total: null, latency_s: 38.3 } }),
    cell('finsql', 'spider', 'unaligned', { ex: 0.68 }, { run_id: 'run-fin', cost: { tokens_total: null, latency_s: 19.7 } }),
  ],
}

test('costScatter plots comparable cells that report the chosen cost', () => {
  const latency = costScatter(costed, 'ex', 'latency_s')
  assert.deepEqual(latency.points.map(point => [point.label, point.x, point.y]), [
    ['c3sql · spider', 38.5, 0.8],
    ['dinsql · spider', 38.3, 0.77],
  ])
  assert.equal(latency.xMax, 38.5)
  assert.equal(latency.yMax, 1)
  assert.deepEqual(latency.omitted, ['finsql · spider'])
  const tokens = costScatter(costed, 'ex', 'tokens_total')
  assert.deepEqual(tokens.points.map(point => point.label), ['c3sql · spider'])
  assert.deepEqual(tokens.omitted, ['dinsql · spider', 'finsql · spider'])
})

test('diffCandidates offers only comparable runs, grouped by benchmark', () => {
  assert.deepEqual(diffCandidates(costed), {
    spider: [
      { method: 'c3sql', label: 'c3sql', runId: 'run-c3' },
      { method: 'dinsql', label: 'dinsql', runId: 'run-din' },
    ],
  })
  assert.deepEqual(diffCandidates({ ...costed, cells: [costed.cells[0]] }), {}, 'one run has nothing to compare against')
})

test('displayNamesFor maps config ids back to Compose labels', () => {
  assert.deepEqual(displayNamesFor('gpt-baseline', 'BookSQL'), { method: 'GPT Baseline', database: 'BookSQL' })
  assert.deepEqual(displayNamesFor('c3sql', 'ehrsql-2024'), { method: 'C3SQL', database: 'EHRSQL-2024' })
  assert.equal(displayNamesFor('nope', 'spider'), null)
})

test('a variant column is shown by the label the server gives it', () => {
  const variant = 'c3sql.var-1a2b3c'
  const labelled = {
    methods: ['c3sql', variant],
    labels: { c3sql: 'c3sql', [variant]: 'c3sql · c3sql_generate→FINSQLGenerator' },
    datasets: ['spider'],
    metrics: [{ id: 'ex', label: 'Execution accuracy' }],
    cells: [
      cell('c3sql', 'spider', 'ok', { ex: 0.5 }, { run_id: 'run-a' }),
      cell(variant, 'spider', 'ok', { ex: 0.7 }, { run_id: 'run-b' }),
    ],
  }
  assert.deepEqual(heatmapRows(labelled, 'ex').map(row => row.label), ['c3sql', 'c3sql · c3sql_generate→FINSQLGenerator'])
  assert.equal(groupedBars(labelled, 'ex', labelled.methods)[0].bars[1].label, 'c3sql · c3sql_generate→FINSQLGenerator')
  assert.deepEqual(diffCandidates(labelled).spider.map(run => run.label), ['c3sql', 'c3sql · c3sql_generate→FINSQLGenerator'])
})
