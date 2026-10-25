import test from 'node:test'
import assert from 'node:assert/strict'
import {
  addVariant,
  experimentAxes,
  experimentCells,
  normalizeStages,
  removeVariant,
  stageChoices,
  variantIdentity,
  variantLabel,
} from './variantModel.js'

const c3sql = {
  method: 'c3sql',
  dataset: 'spider',
  stages: [
    { id: 'c3sql_reduce', type: 'ReduceTask', actor: 'C3SQLReducer' },
    { id: 'c3sql_generate', type: 'GenerateTask', actor: 'C3SQLGenerator' },
  ],
}
const stageActors = { GenerateTask: ['C3SQLGenerator', 'FINSQLGenerator'], ReduceTask: ['C3SQLReducer', 'FINSQLReducer'] }

test('a swap that keeps the own Actor or names another stage is dropped', () => {
  assert.deepEqual(normalizeStages(c3sql, {
    c3sql_generate: 'FINSQLGenerator',
    c3sql_reduce: 'C3SQLReducer',
    nope: 'FINSQLGenerator',
  }), { c3sql_generate: 'FINSQLGenerator' })
})

test('variants are added once and a no-op swap is not a variant', () => {
  let variants = addVariant([], c3sql, { c3sql_generate: 'FINSQLGenerator' })
  variants = addVariant(variants, c3sql, { c3sql_generate: 'FINSQLGenerator' })
  variants = addVariant(variants, c3sql, { c3sql_generate: 'C3SQLGenerator' })
  assert.deepEqual(variants, [{ method: 'c3sql', dataset: 'spider', stages: { c3sql_generate: 'FINSQLGenerator' } }])
  variants = addVariant(variants, c3sql, { c3sql_generate: 'FINSQLGenerator', c3sql_reduce: 'FINSQLReducer' })
  assert.equal(variants.length, 2)
  assert.deepEqual(removeVariant(variants, variantIdentity(variants[0])), [variants[1]])
})

test('labels read like the server labels', () => {
  assert.equal(variantLabel('c3sql', {}), 'c3sql')
  assert.equal(
    variantLabel('c3sql', { c3sql_generate: 'FINSQLGenerator', c3sql_reduce: 'FINSQLReducer' }),
    'c3sql · c3sql_generate→FINSQLGenerator, c3sql_reduce→FINSQLReducer',
  )
})

test('an experiment sends the selected configs and every variant as cells', () => {
  const variants = addVariant([], c3sql, { c3sql_generate: 'FINSQLGenerator' })
  assert.deepEqual(experimentCells([c3sql], variants), [
    { method: 'c3sql', dataset: 'spider' },
    { method: 'c3sql', dataset: 'spider', stages: { c3sql_generate: 'FINSQLGenerator' } },
  ])
})

test('an experiment has one column per cell, variants included', () => {
  assert.deepEqual(experimentAxes({
    cells: [
      { method: 'c3sql', variant: 'c3sql', dataset: 'spider' },
      { method: 'c3sql', variant: 'c3sql.var-1a2b3c', dataset: 'spider' },
      { method: 'dinsql', variant: 'dinsql', dataset: 'bird' },
    ],
  }), { methods: ['c3sql', 'c3sql.var-1a2b3c', 'dinsql'], datasets: ['spider', 'bird'] })
})

test('a stage offers its own Actor first, then others of its type', () => {
  assert.deepEqual(stageChoices(c3sql.stages[1], stageActors), ['C3SQLGenerator', 'FINSQLGenerator'])
  assert.deepEqual(stageChoices(c3sql.stages[1], {}), ['C3SQLGenerator'])
})
