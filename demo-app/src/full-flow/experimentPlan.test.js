import test from 'node:test'
import assert from 'node:assert/strict'
import { planReducer, initialPlan, planSummary, planPayload, connectionsOf, variantsOf } from './experimentPlan.js'


test('toggling a cell adds it, and toggling it again removes it', () => {
  let plan = initialPlan()
  plan = planReducer(plan, { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['C3SQL/Spider', 'DINSQL/Spider'])
  plan = planReducer(plan, { type: 'toggleCell', method: 'DINSQL', dataset: 'Spider' })
  assert.deepEqual(plan.cells.map(cell => `${cell.method}/${cell.dataset}`), ['C3SQL/Spider'])
})

test('the last cell can be removed, leaving an editable empty plan', () => {
  let plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  assert.equal(plan.cells.length, 0)
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

import { templateFromExperiment } from './experimentPlan.js'

test('an earlier experiment becomes a plan to edit: its pairs, its variants and its sampling', () => {
  const experiment = {
    experiment_id: 'exp-9',
    sampling: { limit: 50, mode: 'random', seed: 7 },
    cells: [
      { method: 'C3SQL', dataset: 'Spider', stages: {} },
      { method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } },
      { method: 'DINSQL', dataset: 'BIRD', stages: {} },
    ],
  }
  assert.deepEqual(templateFromExperiment(experiment), {
    connections: [
      { method: 'C3SQL', database: 'Spider' },
      { method: 'DINSQL', database: 'BIRD' },
    ],
    variants: [{ method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } }],
    sampling: { limit: 50, mode: 'random', seed: 7 },
  })
})

import { normalizeStages } from './variantModel.js'

test('the plan holds connections and variants; a variant never stops the last connection from being removed', () => {
  let plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  plan = planReducer(plan, {
    type: 'addVariant',
    config: { method: 'C3SQL', dataset: 'Spider', stages: [{ id: 'c3sql_generate', actor: 'C3SQLGenerator', type: 'GenerateTask' }] },
    stages: { c3sql_generate: 'FINSQLGenerator' },
  })
  assert.equal(plan.cells.length, 2)
  plan = planReducer(plan, { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  assert.equal(plan.cells.filter(cell => !cell.stages || !Object.keys(cell.stages).length).length, 0, 'the last connection is removed')
  assert.equal(plan.cells.filter(cell => cell.stages && Object.keys(cell.stages).length).length, 1, 'and the variant is untouched')
})

test('a variant is added once, a no-op swap is refused, and a variant can be removed by its identity', () => {
  const config = { method: 'C3SQL', dataset: 'Spider', stages: [{ id: 'c3sql_generate', actor: 'C3SQLGenerator', type: 'GenerateTask' }] }
  let plan = initialPlan()
  plan = planReducer(plan, { type: 'addVariant', config, stages: { c3sql_generate: 'FINSQLGenerator' } })
  plan = planReducer(plan, { type: 'addVariant', config, stages: { c3sql_generate: 'FINSQLGenerator' } })
  plan = planReducer(plan, { type: 'addVariant', config, stages: { c3sql_generate: 'C3SQLGenerator' } })
  assert.equal(plan.cells.length, 1)
  plan = planReducer(plan, { type: 'removeVariant', identity: 'Spider/C3SQL|c3sql_generate→FINSQLGenerator' })
  assert.equal(plan.cells.length, 0)
})

test('the plan can be loaded from a draft or a template in one step', () => {
  const plan = planReducer(initialPlan(), {
    type: 'hydrate',
    connections: [{ method: 'C3SQL', database: 'Spider' }, { method: 'DINSQL', database: 'BIRD' }],
    variants: [{ method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } }],
    sampling: { limit: 50, mode: 'random', seed: 7 },
  })
  assert.equal(plan.cells.length, 3)
  assert.deepEqual(plan.sampling, { limit: 50, mode: 'random', seed: 7 })
  assert.deepEqual(connectionsOf(plan).map(item => item.key), ['C3SQL/Spider', 'DINSQL/BIRD'])
  assert.deepEqual(variantsOf(plan).map(item => item.dataset), ['Spider'])
})

test('bulk selection uses only runnable cells and an empty plan survives hydration', () => {
  const selected = planReducer(initialPlan(), { type: 'selectAll', runnable: new Set(['C3SQL/Spider', 'DINSQL/BIRD']) })
  assert.equal(selected.cells.length, 2)
  const cleared = planReducer(selected, { type: 'clear' })
  assert.equal(planSummary(cleared).reason, 'empty')
  assert.equal(planReducer(selected, { type: 'hydrate', connections: [], variants: [] }).cells.length, 0)
})

test('readiness reports missing credentials and unavailable data before submission', () => {
  const plan = planReducer(initialPlan(), { type: 'toggleCell', method: 'C3SQL', dataset: 'Spider' })
  const configs = [{ method: 'c3sql', dataset: 'spider' }]
  assert.equal(planSummary(plan, { configs, credentialConfigured: false }).reason, 'credentialMissing')
  assert.equal(planSummary(plan, { configs: [{ ...configs[0], installed: false }], credentialConfigured: true }).reason, 'unavailableCells')
})

test('experiment snapshots keep identity and lifecycle together; an edit becomes a draft', () => {
  const finished = planReducer(initialPlan(), { type: 'experimentReceived', adopt: true, experiment: { experiment_id: 'exp-1', status: 'completed', cells: [] } })
  assert.equal(finished.experimentId, 'exp-1')
  assert.equal(finished.status, 'done')
  const edited = planReducer(finished, { type: 'setSampling', sampling: { limit: 50, mode: 'random', seed: 7 } })
  assert.equal(edited.status, 'draft')
  assert.deepEqual(edited.sampling, { limit: 50, mode: 'random', seed: 7 })
  assert.equal(planReducer(edited, { type: 'experimentReceived', experiment: { experiment_id: 'exp-1', status: 'completed' } }).status, 'draft')
  const template = planReducer(edited, { type: 'hydrate', connections: [], variants: [] })
  assert.equal(planReducer(template, { type: 'experimentReceived', experiment: { experiment_id: 'exp-1', status: 'running' } }).experimentId, null)
})

test('a variant-only template stays variant-only, and payloads resolve canonical configs', () => {
  const template = templateFromExperiment({ cells: [{ method: 'C3SQL', dataset: 'Spider', stages: { generate: 'FINSQLGenerator' } }], sampling: { limit: 50, mode: 'random', seed: 7 } })
  assert.deepEqual(template.connections, [])
  const plan = planReducer(initialPlan(), { type: 'hydrate', ...template })
  const payload = planPayload(plan, [{ method: 'c3sql', dataset: 'spider', stages: [{ id: 'generate', actor: 'C3SQLGenerator' }] }])
  assert.deepEqual(payload, { cells: [{ method: 'c3sql', dataset: 'spider', stages: { generate: 'FINSQLGenerator' } }], sampling: { limit: 50, mode: 'random', seed: 7 } })
})
