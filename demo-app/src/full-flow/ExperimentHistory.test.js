import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, waitFor, within } = await import('@testing-library/react')
const unregister = register()
const { default: ExperimentHistory } = await import('./ExperimentHistory.jsx')
const { translate } = await import('../i18n/index.js')

const t = (key, params = {}) => translate('en-US', key, params)
const experiments = [
  {
    experiment_id: 'exp-2',
    status: 'completed',
    sampling: { limit: 20, mode: 'slice', seed: 42 },
    created_at: 1700000200,
    cells: [{ method: 'C3SQL', dataset: 'Spider', stages: {} }],
  },
  {
    experiment_id: 'exp-1',
    status: 'partial',
    sampling: { limit: 50, mode: 'random', seed: 7 },
    created_at: 1700000100,
    cells: [
      { method: 'C3SQL', dataset: 'Spider', stages: {} },
      { method: 'DINSQL', dataset: 'BIRD', stages: {} },
    ],
  },
]

test.afterEach(cleanup)
test.after(() => {
  unregister()
  closeDom()
})

test('past experiments are listed newest first, with their status, sampling and size', async () => {
  render(React.createElement(ExperimentHistory, {
    api: async () => ({ experiments }),
    currentExperimentId: '',
    onOpenExperiment: () => {},
    onUseTemplate: () => {},
    t,
  }))
  const rows = await screen.findAllByRole('listitem')
  assert.equal(rows.length, 2)
  assert.match(rows[0].textContent, /exp-2|completed/)
  assert.match(rows[1].textContent, /partial/)
  assert.match(rows[1].textContent, /random/)
  assert.match(rows[1].textContent, /2 cells/)
})

test('an experiment can be opened for comparison, or used as the template of a new plan', async () => {
  const opened = []
  const templated = []
  render(React.createElement(ExperimentHistory, {
    api: async () => ({ experiments }),
    currentExperimentId: '',
    onOpenExperiment: id => opened.push(id),
    onUseTemplate: experiment => templated.push(experiment.experiment_id),
    t,
  }))
  const row = (await screen.findAllByRole('listitem'))[1]
  fireEvent.click(within(row).getByRole('button', { name: 'Open' }))
  fireEvent.click(within(row).getByRole('button', { name: 'Use as template' }))
  assert.deepEqual(opened, ['exp-1'])
  assert.deepEqual(templated, ['exp-1'])
})

test('the experiment being shown is marked, and an empty history says so', async () => {
  render(React.createElement(ExperimentHistory, {
    api: async () => ({ experiments }),
    currentExperimentId: 'exp-2',
    onOpenExperiment: () => {},
    onUseTemplate: () => {},
    t,
  }))
  await waitFor(() => assert.ok(screen.getAllByRole('listitem')[0].querySelector('[aria-current="true"]')))
  cleanup()
  render(React.createElement(ExperimentHistory, {
    api: async () => ({ experiments: [] }),
    currentExperimentId: '',
    onOpenExperiment: () => {},
    onUseTemplate: () => {},
    t,
  }))
  assert.ok(await screen.findByText('No experiments yet.'))
})
