import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
const unregister = register()
const { default: ComparisonPanel } = await import('./ComparisonPanel.jsx')

const t = (key, params = {}) => [key, ...Object.values(params)].join(':')

const matrix = {
  methods: ['c3sql', 'dinsql'],
  datasets: ['spider', 'bird'],
  metrics: [{ id: 'ex', label: 'Execution accuracy' }],
  sampling: { limit: 20, mode: 'slice', seed: 42 },
  cells: [
    { method: 'c3sql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.8 }, sample_count: 20, run_id: 'run-a', label: 'c3sql' },
    { method: 'dinsql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.77 }, sample_count: 20, run_id: 'run-b', label: 'dinsql' },
    { method: 'c3sql', dataset: 'bird', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.5 }, sample_count: 20, run_id: 'run-c', label: 'c3sql' },
  ],
  labels: { c3sql: 'C3SQL', dinsql: 'DINSQL' },
}

test.afterEach(cleanup)
test.after(() => {
  unregister()
  closeDom()
})

test('choosing a benchmark and ticking its methods shows them side by side', () => {
  render(React.createElement(ComparisonPanel, { matrix, api: async () => ({}), postJson: async () => ({}), liveEvaluation: true, t }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  const table = screen.getByRole('table')
  assert.equal(table.querySelectorAll('tbody tr').length, 2)
  assert.equal(screen.queryByRole('checkbox', { name: 'C3SQL' }).checked, true)
})

test('a benchmark has its own members: switching benchmark clears the table', () => {
  render(React.createElement(ComparisonPanel, { matrix, api: async () => ({}), postJson: async () => ({}), liveEvaluation: true, t }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  assert.ok(screen.getByRole('table'))
  fireEvent.click(screen.getByRole('button', { name: 'bird' }))
  assert.equal(screen.queryByRole('table'), null)
})

test('saving a snapshot sends the benchmark, sampling and each member run', async () => {
  const sent = []
  render(React.createElement(ComparisonPanel, {
    matrix,
    api: async () => ({}),
    postJson: async (path, body) => { sent.push([path, body]); return { id: 'abc123abc123' } },
    liveEvaluation: true,
    t,
  }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  fireEvent.click(screen.getByRole('button', { name: 'compare.saveSnapshot' }))
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(sent[0][0], '/api/comparison-snapshots')
  assert.equal(sent[0][1].benchmark, 'spider')
  assert.deepEqual(sent[0][1].sampling, { limit: 20, mode: 'slice', seed: 42 })
  assert.deepEqual(sent[0][1].members.map(member => member.run_id), ['run-a', 'run-b'])
})

test('the hosted Space cannot save a snapshot', () => {
  render(React.createElement(ComparisonPanel, { matrix, api: async () => ({}), postJson: async () => ({}), liveEvaluation: false, t }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  assert.equal(screen.queryByRole('button', { name: 'compare.saveSnapshot' }), null)
})

test('a snapshot keeps the Actor swaps of a variant member', async () => {
  const sent = []
  const variantMatrix = {
    ...matrix,
    methods: ['c3sql', 'c3sql.var-1a2b3c'],
    labels: { 'c3sql': 'C3SQL', 'c3sql.var-1a2b3c': 'C3SQL · c3sql_generate→FINSQLGenerator' },
    cells: [
      { method: 'c3sql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.8 }, sample_count: 20, run_id: 'run-a', stages: {} },
      { method: 'c3sql.var-1a2b3c', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.7 }, sample_count: 20, run_id: 'run-b', stages: { c3sql_generate: 'FINSQLGenerator' } },
    ],
  }
  render(React.createElement(ComparisonPanel, {
    matrix: variantMatrix,
    liveEvaluation: true,
    postJson: async (path, body) => { sent.push(body); return { id: 'abc123abc123' } },
    t,
  }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL · c3sql_generate→FINSQLGenerator' }))
  fireEvent.click(screen.getByRole('button', { name: 'compare.saveSnapshot' }))
  await new Promise(resolve => setTimeout(resolve, 0))
  const variant = sent[0].members[1]
  assert.equal(variant.variant, 'c3sql.var-1a2b3c')
  assert.deepEqual(variant.stages, { c3sql_generate: 'FINSQLGenerator' })
})

const withCells = cells => ({ ...matrix, cells: [...matrix.cells.slice(0, 0), ...cells] })

test('a member that is not comparable stays listed with the reason, and is not ranked', () => {
  render(React.createElement(ComparisonPanel, {
    matrix: withCells([
      { method: 'c3sql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.8 }, sample_count: 20, run_id: 'run-a', stages: {} },
      { method: 'dinsql', dataset: 'spider', status: 'unaligned', values: { ex: 0.9 }, sample_count: 20, run_id: 'run-b', stages: {} },
    ]),
    liveEvaluation: false,
    postJson: async () => ({}),
    t,
  }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  const row = screen.getByRole('row', { name: /DINSQL/ })
  assert.match(row.textContent, /compare\.unaligned/)
  assert.equal(screen.getByRole('row', { name: /C3SQL/ }).getAttribute('data-best'), 'true', 'the unaligned 0.9 does not beat the comparable 0.8')
})

test('a missing value says no data, and an empty comparison says how to start', () => {
  render(React.createElement(ComparisonPanel, { matrix, liveEvaluation: false, postJson: async () => ({}), t }))
  assert.ok(screen.getByText('compare.pickMethods'))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  assert.ok(screen.getByRole('table'))
})

test('the bars show the chosen measure, metric or cost, for each member', () => {
  render(React.createElement(ComparisonPanel, {
    matrix: withCells([
      { method: 'c3sql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.8 }, cost: { tokens_total: 900, latency_s: 4 }, sample_count: 20, run_id: 'run-a', stages: {} },
      { method: 'dinsql', dataset: 'spider', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.77 }, cost: { tokens_total: 300, latency_s: 2 }, sample_count: 20, run_id: 'run-b', stages: {} },
    ]),
    liveEvaluation: false,
    postJson: async () => ({}),
    t,
  }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  fireEvent.change(screen.getByRole('combobox', { name: 'compare.barMeasure' }), { target: { value: 'latency_s' } })
  const bars = screen.getByRole('list', { name: 'compare.bars' })
  assert.match(bars.textContent, /C3SQL 4\.0s/)
  assert.match(bars.textContent, /DINSQL 2\.0s/)
})

test('the run of a member opens its run, and two comparable members open their question diff', () => {
  const opened = []
  const compared = []
  render(React.createElement(ComparisonPanel, {
    matrix,
    liveEvaluation: false,
    postJson: async () => ({}),
    onOpenRun: id => opened.push(id),
    onCompareDiff: (a, b) => compared.push([a, b]),
    t,
  }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  fireEvent.click(screen.getByRole('button', { name: 'run-a' }))
  assert.deepEqual(opened, ['run-a'])
  fireEvent.click(screen.getByRole('button', { name: 'compare.diffPair' }))
  assert.deepEqual(compared, [['run-a', 'run-b']])
})

test('changing the source keeps the members but says the comparison has changed', () => {
  const { rerender } = render(React.createElement(ComparisonPanel, { matrix, source: 'archive', liveEvaluation: false, postJson: async () => ({}), t }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  rerender(React.createElement(ComparisonPanel, { matrix, source: 'experiment', liveEvaluation: false, postJson: async () => ({}), t }))
  assert.ok(screen.getByRole('status', { name: 'compare.sourceChanged' }))
  assert.equal(screen.getByRole('checkbox', { name: 'C3SQL' }).checked, true)
})

test('a saved snapshot can be reopened, and a member whose run is gone is flagged', async () => {
  const api = async path => {
    if (String(path) === '/api/comparison-snapshots') return { snapshots: [{ id: 'abc123abc123', name: 'spider comparison', benchmark: 'spider', saved_at: '2026-10-22T10:00:00', members: 2 }] }
    if (String(path) === '/api/comparison-snapshots/abc123abc123') {
      return {
        id: 'abc123abc123',
        name: 'spider comparison',
        benchmark: 'spider',
        sampling: { limit: 20, mode: 'slice', seed: 42 },
        members: [
          { method: 'c3sql', label: 'C3SQL', run_id: 'run-a', status: 'ok', sample_hash: 'same-cohort', values: { ex: 0.8 }, stages: {} },
          { method: 'dinsql', label: 'DINSQL', run_id: 'run-b', status: 'run_missing', values: {}, stages: {} },
        ],
      }
    }
    return {}
  }
  render(React.createElement(ComparisonPanel, { matrix, api, liveEvaluation: false, postJson: async () => ({}), t }))
  fireEvent.click(await screen.findByRole('button', { name: /compare\.openSnapshot/ }))
  const opened = await screen.findByRole('table', { name: /compare\.snapshot/ })
  assert.match(within(opened).getByRole('row', { name: /DINSQL/ }).textContent, /compare\.runMissing/)
})

 test('missing fingerprints do not claim a fair multi-run comparison', () => {
  render(React.createElement(ComparisonPanel, { matrix: { ...matrix, cells: matrix.cells.map(cell => ({ ...cell, sample_hash: null })) }, t }))
  fireEvent.click(screen.getByRole('button', { name: 'spider' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'C3SQL' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'DINSQL' }))
  assert.equal(screen.getByRole('button', { name: 'compare.diffPair' }).disabled, true)
})
