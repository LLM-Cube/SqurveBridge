import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
const unregister = register()
const { default: ExperimentGrid } = await import('./ExperimentGrid.jsx')

import { translate } from '../i18n/index.js'

const t = (key, params = {}) => translate('en-US', key, params)
const configs = [
  { method: 'C3SQL', dataset: 'Spider', stages: [] },
  { method: 'DINSQL', dataset: 'Spider', stages: [] },
]
const baseProps = {
  api: async () => ({ cells: [] }),
  configs,
  connections: [{ method: 'C3SQL', database: 'Spider' }],
  variants: [],
  sampleLimit: 20,
  sampleMode: 'slice',
  sampleSeed: 42,
  onSampleLimitChange: () => {},
  onSampleModeChange: () => {},
  onSampleSeedChange: () => {},
  onGoToRun: () => {},
  t,
}

test.afterEach(cleanup)
test.after(() => {
  unregister()
  closeDom()
})

test('the sampling for the experiment is set here, where the cells are chosen', () => {
  const limits = []
  render(React.createElement(ExperimentGrid, { ...baseProps, onSampleLimitChange: value => limits.push(value) }))
  const select = screen.getByRole('combobox', { name: 'Sample limit' })
  fireEvent.change(select, { target: { value: '50' } })
  assert.deepEqual(limits, [50])
  assert.ok(screen.getByRole('combobox', { name: 'Sample mode' }))
  assert.ok(screen.getByRole('spinbutton', { name: 'Sample seed' }))
})

test('the plan footer states what the run will execute and sends the user to Run', () => {
  let wentToRun = 0
  render(React.createElement(ExperimentGrid, {
    ...baseProps,
    variants: [{ method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } }],
    onGoToRun: () => { wentToRun += 1 },
  }))
  const footer = screen.getByTestId('experiment-plan-footer')
  assert.match(footer.textContent, /2 cells · 1 variants · 20 questions each · 40 questions in total/)
  fireEvent.click(within(footer).getByRole('button', { name: 'Go to run' }))
  assert.equal(wentToRun, 1)
})

test('a cell with a variant shows how many variants it has', () => {
  render(React.createElement(ExperimentGrid, {
    ...baseProps,
    variants: [
      { method: 'C3SQL', dataset: 'Spider', stages: { c3sql_generate: 'FINSQLGenerator' } },
      { method: 'C3SQL', dataset: 'Spider', stages: { c3sql_reduce: 'FINSQLReducer' } },
    ],
  }))
  const cell = screen.getByRole('checkbox', { name: 'C3SQL × Spider' }).closest('td')
  assert.match(cell.textContent, /\+2/)
})
