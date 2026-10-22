import test from 'node:test'
import assert from 'node:assert/strict'
import { planReducer, initialPlan, planSummary } from './experimentPlan.js'


test('toggling a cell adds it, and toggling it again removes it', () => {
  let plan = initialPlan()
  plan = planReducer(plan, { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['C3SQL/Spider', 'DINSQL/Spider'])
  plan = planReducer(plan, { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['C3SQL/Spider'])
})

test('the last cell of a plan cannot be removed', () => {
  let plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  assert.equal(plan.cells.length, 1)
})

test('selecting a line adds every runnable cell on it, and selecting it again clears them', () => {
  const runnable = new Set(['C3SQL/Spider', 'C3SQL/BIRD', 'DINSQL/Spider'])
  let plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'selectLine', method: 'C3SQL', runnable })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['DINSQL/Spider', 'C3SQL/Spider', 'C3SQL/BIRD'])
  plan = planReducer(plan, { type: 'selectLine', method: 'C3SQL', runnable })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['DINSQL/Spider'])
})

test('a line with nothing runnable changes nothing', () => {
  const plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  assert.equal(planReducer(plan, { type: 'selectLine', method: 'E-SQL', runnable: new Set(['C3SQL/Spider']) }), plan)
})

test('selecting a column adds the runnable cells of one benchmark', () => {
  const runnable = new Set(['C3SQL/BIRD', 'DINSQL/BIRD', 'DINSQL/Spider'])
  const plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  const next = planReducer(plan, { type: 'selectLine', dataset: 'BIRD', runnable })
  assert.deepEqual(next.cells.map(cell => `${cell.method}/${cell.dataset}`), ['DINSQL/Spider', 'C3SQL/BIRD', 'DINSQL/BIRD'])
})

test('sampling is set as a whole and keeps the plan otherwise unchanged', () => {
  const plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  const next = planReducer(plan, { type: 'setSampling', sampling: { limit: 50, mode: 'random', seed: 7 } })
  assert.deepEqual(next.sampling, { limit: 50, mode: 'random', seed: 7 })
  assert.deepEqual(next.cells, plan.cells)
})

test('a submitted plan is accepted with an experiment id, or rejected and kept for editing', () => {
  const plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  const submitted = planReducer(plan, { type: 'submitStarted' })
  assert.equal(submitted.status, 'submitted')
  const running = planReducer(submitted, { type: 'submitAccepted', experimentId: 'exp-1' })
  assert.equal(running.status, 'running')
  assert.equal(running.experimentId, 'exp-1')
  const rejected = planReducer(submitted, { type: 'submitRejected', problems: [{ method: 'C3SQL', dataset: 'Spider', error_code: 'credential_missing' }] })
  assert.equal(rejected.status, 'rejected')
  assert.deepEqual(rejected.cells, plan.cells, 'a rejected plan keeps its cells for editing')
  assert.equal(rejected.problems[0].error_code, 'credential_missing')
})

test('the summary counts cells, variants and questions, and says why a plan cannot run', () => {
  let plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'toggleCell', method: 'DINSQL', dataset: 'BIRD' })
  plan = { ...plan, cells: [...plan.cells, { method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } }] }
  assert.deepEqual(planSummary(plan), { cells: 3, variants: 1, questionsPerCell: 20, questions: 60, runnable: true, reason: null })
  assert.deepEqual(planSummary(initialPlan()), { cells: 0, variants: 0, questionsPerCell: 20, questions: 0, runnable: false, reason: 'empty' })
  assert.equal(planSummary({ ...plan, cells: Array.from({ length: 25 }, (_, i) => ({ method: 'M', dataset: `D${i}`, stages: {} })) }).reason, 'tooManyCells')
})
