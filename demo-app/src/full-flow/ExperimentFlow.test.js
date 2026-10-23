import assert from 'node:assert/strict'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, waitFor, within } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: FullFlowDemo } = await import('./FullFlowDemo.jsx')
const configs = ['c3sql', 'dinsql'].map(method => ({ method, dataset: 'spider', installed: true, stages: [{ id: 'generate', type: 'GenerateTask', actor: method === 'c3sql' ? 'C3SQLGenerator' : 'DINSQLGenerator' }] }))
const capabilities = { reproduce_configs: configs, stage_actors: { GenerateTask: ['C3SQLGenerator', 'DINSQLGenerator', 'FINSQLGenerator'] }, deployment: { target: 'local', features: { live_evaluation: true, agent_chat: false } } }
const open = props => {
  window.localStorage.setItem('squrve-demo-locale', 'en-US')
  window.history.replaceState(null, '', '#compose')
  return render(React.createElement(FullFlowDemo, { capabilities, sqlAuth: { configured: true }, databases: [], postJson: async () => ({}), api: async () => ({}), ...props }))
}
test.afterEach(() => { cleanup(); window.localStorage.clear() })
test.after(() => { unregister(); closeDom() })

test('design two pairs and a variant, submit one experiment, then open its comparison', async () => {
  let snapshot
  const calls = []
  const requested = []
  const api = async path => {
    requested.push(String(path))
    if (path === '/api/experiments') return { experiments: snapshot ? [snapshot] : [] }
    if (path === '/api/experiments/exp-flow') return snapshot
    if (String(path).startsWith('/api/results/matrix')) return { methods: ['c3sql', 'dinsql', 'c3sql-variant'], datasets: ['spider'], cells: [], metrics: [] }
    return {}
  }
  open({ api, postJson: async (path, body) => {
    calls.push([path, body])
    snapshot = { experiment_id: 'exp-flow', status: 'completed', sampling: body.sampling, cells: body.cells.map((cell, index) => ({ ...cell, cell_id: `c${index}`, variant: index === 2 ? 'c3sql-variant' : cell.method, status: 'completed', job_id: `j${index}` })) }
    return snapshot
  } })
  const user = userEvent.setup()
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × Spider' }))
  await user.click(within(grid).getByRole('button', { name: 'Show the workflow of C3SQL × Spider' }))
  await user.selectOptions(screen.getByRole('combobox', { name: 'Actor for generate' }), 'FINSQLGenerator')
  await user.click(screen.getByRole('button', { name: 'Add as variant' }))
  await user.selectOptions(within(grid).getByRole('combobox', { name: 'Sample mode' }), 'random')
  fireEvent.change(within(grid).getByRole('spinbutton', { name: 'Sample seed' }), { target: { value: '7' } })
  assert.match(screen.getByTestId('experiment-plan-footer').textContent, /3 cells · 1 variants/)
  await user.click(within(grid).getByRole('button', { name: 'Go to run' }))
  assert.equal(window.location.hash, '#board')
  await user.click(screen.getByRole('button', { name: 'Run config' }))
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/experiments')
  assert.equal(calls[0][1].cells.length, 3)
  assert.deepEqual(calls[0][1].sampling, { limit: 20, mode: 'random', seed: 7 })
  await user.click(screen.getByRole('button', { name: 'Past experiments' }))
  await user.click(await screen.findByRole('button', { name: 'View comparison' }))
  await waitFor(() => assert.ok(requested.some(path => path.includes('experiment_id=exp-flow'))))
  assert.equal(window.location.hash, '#evidence')
  assert.equal(screen.queryByRole('combobox', { name: 'Results from' }), null)
  assert.equal(screen.getByRole('button', { name: 'Current experiment' }).getAttribute('aria-pressed'), 'true')
})

test('a completed experiment restores after reload and empty drafts stay empty', async () => {
  const completed = { experiment_id: 'exp-done', status: 'completed', sampling: { limit: 50, mode: 'random', seed: 7 }, cells: [{ cell_id: 'c1', method: 'c3sql', dataset: 'spider', variant: 'c3sql', status: 'completed' }] }
  const api = async path => path === '/api/experiments' ? { experiments: [completed] } : path === '/api/experiments/exp-done' ? completed : {}
  const first = open({ api })
  await waitFor(() => assert.equal(screen.getByTestId('experiment-status').textContent, 'Finished'))
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Clear plan' }))
  first.unmount()
  open({ api })
  await waitFor(() => assert.match(screen.getByTestId('experiment-plan-footer').textContent, /0 cells/))
  assert.equal(screen.getByRole('button', { name: 'Go to run' }).disabled, true)
  assert.ok(await screen.findByText(/Experiment exp-done: 1 cells · up to 50/))
})

test('credentials and cell limits block the plan; a cleared seed can be replaced', async () => {
  const { rerender } = open({ sqlAuth: { configured: false } })
  const grid = screen.getByTestId('experiment-grid')
  assert.equal(within(grid).getByRole('button', { name: 'Go to run' }).disabled, true)
  assert.match(grid.textContent, /Configure an LLM before running/)
  rerender(React.createElement(FullFlowDemo, { capabilities, sqlAuth: { configured: true }, api: async () => ({}), databases: [] }))
  const user = userEvent.setup()
  await user.selectOptions(within(grid).getByRole('combobox', { name: 'Sample mode' }), 'random')
  const seed = within(grid).getByRole('spinbutton', { name: 'Sample seed' })
  await user.clear(seed)
  await user.type(seed, '17')
  assert.equal(seed.value, '17')
  assert.deepEqual(JSON.parse(window.localStorage.getItem('squrve-demo-draft')).sampling, { limit: 20, mode: 'random', seed: 17 })
})
