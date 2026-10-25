import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render } = await import('@testing-library/react')
const unregister = register()
const { default: ActorWorkflow } = await import('./ActorWorkflow.jsx')

const config = {
  method: 'c3sql',
  dataset: 'spider',
  stages: [
    { id: 'c3sql_reduce', type: 'ReduceTask', actor: 'C3SQLReducer' },
    { id: 'c3sql_generate', type: 'GenerateTask', actor: 'C3SQLGenerator' },
  ],
}
const stageActors = { GenerateTask: ['C3SQLGenerator', 'FINSQLGenerator'], ReduceTask: ['C3SQLReducer'] }
const t = (key, params = {}) => [key, ...Object.values(params)].join(':')

test.afterEach(cleanup)
test.after(() => {
  unregister()
  closeDom()
})

test('swapping a stage and adding it hands the variant up', () => {
  const added = []
  const { getByLabelText, getByText, queryByLabelText } = render(React.createElement(ActorWorkflow, {
    focusedConfig: config, stageActors, onAddVariant: (...args) => added.push(args), t,
  }))
  assert.equal(queryByLabelText('variants.stageActor:c3sql_reduce'), null, 'a stage with no alternative has no picker')
  const add = getByText('variants.add')
  assert.ok(add.disabled, 'nothing to add before a swap')
  fireEvent.change(getByLabelText('variants.stageActor:c3sql_generate'), { target: { value: 'FINSQLGenerator' } })
  assert.ok(!add.disabled)
  fireEvent.click(add)
  assert.deepEqual(added, [[config, { c3sql_generate: 'FINSQLGenerator' }]])
})

test('variants are listed and can be removed', () => {
  const removed = []
  const { getByLabelText } = render(React.createElement(ActorWorkflow, {
    focusedConfig: config,
    stageActors,
    variants: [{ method: 'c3sql', dataset: 'spider', stages: { c3sql_generate: 'FINSQLGenerator' } }],
    onAddVariant: () => {},
    onRemoveVariant: identity => removed.push(identity),
    t,
  }))
  fireEvent.click(getByLabelText('variants.remove:c3sql · c3sql_generate→FINSQLGenerator / spider'))
  assert.deepEqual(removed, ['spider/c3sql|c3sql_generate→FINSQLGenerator'])
})

test('without variant support the workflow stays read only', () => {
  const { queryByText, getByText } = render(React.createElement(ActorWorkflow, { focusedConfig: config, t }))
  assert.ok(getByText('C3SQLGenerator'))
  assert.equal(queryByText('variants.add'), null)
})
