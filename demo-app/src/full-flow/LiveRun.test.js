import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render, screen, waitFor } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: LiveRun } = await import('./LiveRun.jsx')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)
test.after(() => { cleanup(); unregister(); closeDom() })

const stage = (name, state, text = '') => ({ name, kind: name === 'generate' ? 'sql' : 'text', text, seconds: 1.2, state, error: null })
const item = (index, run) => ({ index, id: `dev_${index}`, db: 'pets_1', question: `Question ${index}?`, gold_sql: 'SELECT 1', run })
const idle = { state: 'idle', stages: [], pred_sql: '', ex: null, result: null }
const snapshot = (status, questions, extra = {}) => ({
  experiment_id: 'exp1', status, cursor: 0,
  cells: [{ cell_id: 'c1', method: 'c3sql', dataset: 'spider', status, total: 2, cursor: 0, questions }],
  ...extra,
})

function fakeApi(frames) {
  const calls = []
  const api = async path => {
    calls.push(path)
    return frames[Math.min(calls.length, frames.length) - 1]
  }
  return { api, calls }
}

test('shows the question and lanes as the run produces them, then the verdict', async () => {
  cleanup()
  const { api, calls } = fakeApi([
    snapshot('running', [item(0, { ...idle, state: 'running', stages: [stage('reduce', 'running')] }), item(1, idle)]),
    snapshot('running', [item(0, { state: 'done', stages: [stage('generate', 'done', 'SELECT 1')], pred_sql: 'SELECT 1', ex: true, result: { ok: true, n: 1, head: [['1']] } }), item(1, idle)], { cursor: 1 }),
    snapshot('completed', [item(1, { state: 'done', stages: [stage('generate', 'done', 'SELECT 2')], pred_sql: 'SELECT 2', ex: false, result: { ok: true, n: 1, head: [['2']] } })], { cursor: 2 }),
  ])
  render(React.createElement(LiveRun, { t, api, experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText('Question 1?'))
  // The last response only repeats question 1; question 0's hit comes from the earlier response.
  await waitFor(() => assert.ok(screen.getAllByText(/EX 1\/2/).length >= 1))
  assert.ok(screen.getByText(/EX mismatch/))
  assert.match(calls[0], /\/api\/experiments\/exp1\/timeline\?since=0$/)
  assert.match(calls[1], /since=0$/)
  assert.match(calls[2], /since=1$/)
})

test('stops polling once the experiment is over', async () => {
  cleanup()
  const { api, calls } = fakeApi([snapshot('completed', [item(0, { state: 'done', stages: [], pred_sql: 'SELECT 1', ex: true, result: null }), item(1, idle)], { cursor: 2 })])
  render(React.createElement(LiveRun, { t, api, experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText('Question 1?'))
  const seen = calls.length
  await new Promise(resolve => setTimeout(resolve, 60))
  assert.equal(calls.length, seen)
})

test('says it is waiting before the first event, and says why when the timeline cannot be read', async () => {
  cleanup()
  render(React.createElement(LiveRun, { t, api: async () => snapshot('queued', []), experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText(/Waiting for the first questions/))
  cleanup()
  render(React.createElement(LiveRun, { t, api: async () => { throw new Error('boom') }, experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText(/Live view unavailable/))
})

test('shows the running score with tokens and time, and says so when the run was cancelled', async () => {
  cleanup()
  const done = { state: 'done', stages: [{ ...stage('generate', 'done', 'SELECT 1'), tokens: 40 }], pred_sql: 'SELECT 1', ex: true, result: { ok: true, n: 1, head: [['1']] } }
  const { api } = fakeApi([snapshot('cancelled', [item(0, done), item(1, idle)])])
  render(React.createElement(LiveRun, { t, api, experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText(/40 tok/))
  assert.match(screen.getByText(/40 tok/).textContent, /EX 1\/1 · 1\/2 · 40 tok · 1\.2s/)
  assert.ok(screen.getByRole('status').textContent.startsWith('Cancelled'))
})

test('says tokens are not provided instead of showing zero when no stage reported them', async () => {
  cleanup()
  const done = { state: 'done', stages: [stage('generate', 'done', 'SELECT 1')], pred_sql: 'SELECT 1', ex: true, result: { ok: true, n: 1, head: [['1']] } }
  const { api } = fakeApi([snapshot('running', [item(0, done)])])
  render(React.createElement(LiveRun, { t, api, experimentId: 'exp1', pollMs: 5 }))
  await waitFor(() => screen.getByText(/tokens not provided/))
  assert.doesNotMatch(screen.getByText(/tokens not provided/).textContent, /0 tok/)
})
