import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'
import { translate } from '../i18n/index.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, waitFor } = await import('@testing-library/react')
const unregister = register()
const { default: ExperimentGrid } = await import('./ExperimentGrid.jsx')
const props = {
  configs: [{ method: 'C3SQL', dataset: 'Spider' }],
  connections: [{ method: 'C3SQL', database: 'Spider' }],
  t: (key, params) => translate('en-US', key, params),
}

test.afterEach(cleanup)
test.after(() => { unregister(); closeDom() })

test('slice sampling explains its order and disables the unused seed; random enables it', () => {
  const { rerender } = render(React.createElement(ExperimentGrid, props))
  const seed = screen.getByRole('spinbutton', { name: 'Sample seed' })
  assert.equal(seed.disabled, true)
  assert.match(document.getElementById(seed.getAttribute('aria-describedby')).textContent, /not used/i)
  const mode = screen.getByRole('combobox', { name: 'Sample mode' })
  assert.match(document.getElementById(mode.getAttribute('aria-describedby')).textContent, /first N/i)
  rerender(React.createElement(ExperimentGrid, { ...props, sampleMode: 'random' }))
  assert.equal(seed.disabled, false)
  assert.match(document.getElementById(seed.getAttribute('aria-describedby')).textContent, /same dataset.*same seed/i)
})

test('parameter changes update callbacks and the requested workload without starting a run', () => {
  const updates = []
  const { rerender } = render(React.createElement(ExperimentGrid, {
    ...props, sampleMode: 'random', onSampleSeedChange: seed => updates.push(seed),
  }))
  const seed = screen.getByRole('spinbutton', { name: 'Sample seed' })
  fireEvent.change(seed, { target: { value: '17' } })
  fireEvent.change(seed, { target: { value: '1.5' } })
  assert.deepEqual(updates, [17])
  rerender(React.createElement(ExperimentGrid, { ...props, sampleLimit: 50 }))
  assert.match(screen.getByTestId('experiment-plan-footer').textContent, /50/)
  assert.match(screen.getByText(/Actual counts may be lower/).textContent, /not an LLM call count/)
})

test('a previous score disappears as soon as sampling settings change', async () => {
  const api = async path => path.includes('sample_limit=20')
    ? { cells: [{ method: 'C3SQL', dataset: 'Spider', status: 'ok', values: { ex: 0.95 } }] }
    : new Promise(() => {})
  const { rerender } = render(React.createElement(ExperimentGrid, { ...props, api }))
  await waitFor(() => assert.ok(screen.getByText('95%')))
  rerender(React.createElement(ExperimentGrid, { ...props, api, sampleLimit: 50 }))
  assert.equal(screen.queryByText('95%'), null)
})
