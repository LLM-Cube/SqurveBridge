import test from 'node:test'
import assert from 'node:assert/strict'
import { comparisonReducer, initialComparison, MAX_COMPARISON_MEMBERS } from './comparisonSet.js'

const member = (method, dataset = 'Spider', extra = {}) => ({ method, dataset, label: method, ...extra })

test('a member joins only the chosen benchmark, and choosing a new benchmark clears the set', () => {
  let set = comparisonReducer(initialComparison(), { type: 'chooseBenchmark', benchmark: 'Spider' })
  set = comparisonReducer(set, { type: 'addMember', member: member('C3SQL') })
  set = comparisonReducer(set, { type: 'addMember', member: member('DINSQL', 'BIRD') })
  assert.deepEqual(set.members.map(item => item.method), ['C3SQL'], 'a member from another benchmark is refused')
  set = comparisonReducer(set, { type: 'chooseBenchmark', benchmark: 'BIRD' })
  assert.deepEqual(set.members, [], 'changing benchmark clears the set')
})

test('the set holds at most the comparison limit, and the same member is added once', () => {
  let set = comparisonReducer(initialComparison(), { type: 'chooseBenchmark', benchmark: 'Spider' })
  for (let index = 0; index < MAX_COMPARISON_MEMBERS + 2; index += 1) {
    set = comparisonReducer(set, { type: 'addMember', member: member(`M${index}`) })
    set = comparisonReducer(set, { type: 'addMember', member: member(`M${index}`) })
  }
  assert.equal(set.members.length, MAX_COMPARISON_MEMBERS)
  assert.equal(new Set(set.members.map(item => item.method)).size, MAX_COMPARISON_MEMBERS)
})

test('a removed member leaves the set, and the original and a variant of one method can sit side by side', () => {
  let set = comparisonReducer(initialComparison(), { type: 'chooseBenchmark', benchmark: 'Spider' })
  set = comparisonReducer(set, { type: 'addMember', member: member('C3SQL') })
  set = comparisonReducer(set, { type: 'addMember', member: member('C3SQL.var-1a2b3c', 'Spider', { label: 'C3SQL · generate→FINSQLGenerator' }) })
  assert.equal(set.members.length, 2)
  set = comparisonReducer(set, { type: 'removeMember', variant: 'C3SQL' })
  assert.deepEqual(set.members.map(item => item.method), ['C3SQL.var-1a2b3c'])
})

import { compareMetric } from './comparisonSet.js'

test('a metric ranks the members, marks the best, and gives each gap to it in points', () => {
  const rows = [
    { method: 'C3SQL', values: { ex: 0.80 } },
    { method: 'DINSQL', values: { ex: 0.77 } },
    { method: 'FinSQL', values: { ex: null } },
  ]
  const result = compareMetric(rows, 'ex')
  assert.equal(result.best, 'C3SQL')
  assert.deepEqual(result.rows, [
    { method: 'C3SQL', value: 0.80, gap: 0, best: true },
    { method: 'DINSQL', value: 0.77, gap: -3, best: false },
    { method: 'FinSQL', value: null, gap: null, best: false },
  ])
})

test('a metric no member can compute has no best and is not ranked as zero', () => {
  const result = compareMetric([{ method: 'A', values: {} }, { method: 'B', values: { ex: null } }], 'ex')
  assert.equal(result.best, null)
  assert.ok(result.rows.every(row => row.value === null && row.gap === null))
})

import { comparisonCsv } from './comparisonSet.js'

test('the export names its benchmark and sampling, and leaves uncomputed values blank', () => {
  const csv = comparisonCsv({
    benchmark: 'Spider',
    source: 'archive',
    sampling: { limit: 20, mode: 'slice', seed: 42 },
    rows: [
      { method: 'C3SQL', label: 'C3SQL', values: { ex: 0.8 }, sampleCount: 20, runId: 'run-a' },
      { method: 'C3SQL.var-1a2b3c', label: 'C3SQL · generate→FINSQLGenerator', values: { ex: null }, sampleCount: null, runId: null },
    ],
    metrics: ['ex'],
  })
  assert.equal(csv, [
    '# benchmark=Spider source=archive limit=20 mode=slice seed=42',
    'method,label,ex,sample_count,run_id',
    'C3SQL,C3SQL,0.8,20,run-a',
    'C3SQL.var-1a2b3c,C3SQL · generate→FINSQLGenerator,,,',
  ].join('\n'))
})

test('a label with a comma is quoted so the columns stay aligned', () => {
  const csv = comparisonCsv({
    benchmark: 'Spider', source: 'archive', sampling: { limit: 3, mode: 'slice', seed: 42 },
    rows: [{ method: 'A', label: 'A, with comma', values: { ex: 1 }, sampleCount: 3, runId: 'r' }],
    metrics: ['ex'],
  })
  assert.equal(csv.split('\n')[2], 'A,"A, with comma",1,3,r')
})
