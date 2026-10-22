import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen } = await import('@testing-library/react')
const unregister = register()
const { default: ComparisonPanel } = await import('./ComparisonPanel.jsx')

const t = (key, params = {}) => [key, ...Object.values(params)].join(':')

const matrix = {
  methods: ['c3sql', 'dinsql'],
  datasets: ['spider', 'bird'],
  metrics: [{ id: 'ex', label: 'Execution accuracy' }],
  sampling: { limit: 20, mode: 'slice', seed: 42 },
  cells: [
    { method: 'c3sql', dataset: 'spider', status: 'ok', values: { ex: 0.8 }, sample_count: 20, run_id: 'run-a', label: 'c3sql' },
    { method: 'dinsql', dataset: 'spider', status: 'ok', values: { ex: 0.77 }, sample_count: 20, run_id: 'run-b', label: 'dinsql' },
    { method: 'c3sql', dataset: 'bird', status: 'ok', values: { ex: 0.5 }, sample_count: 20, run_id: 'run-c', label: 'c3sql' },
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
