// The results area: nothing before a run, progress during it, the metrics only once every job has finished.
import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import test, { mock } from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { act, cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: ShowcasePage } = await import('./ShowcasePage.jsx')
const { default: record } = await import('./showcaseRecord.js')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)
const batch = record.batches.at(-1)
const METHODS = Object.keys(batch.runs)

const mount = (search = '') => {
  cleanup()
  window.history.pushState(null, '', `/${search}`)
  render(React.createElement(ShowcasePage, { t, active: true }))
}
const advance = ms => act(() => { mock.timers.tick(ms) })
const hasMetrics = () => screen.queryByTestId('showcase-metrics') !== null
const progressOf = method => Number(within(screen.getByRole('list', { name: 'Config jobs' })).getByRole('progressbar', { name: method }).getAttribute('aria-valuenow'))

// One clock for the whole file; each test unmounts its page while that clock is still the mocked one.
test.before(() => mock.timers.enable({ apis: ['setInterval', 'Date'] }))
test.afterEach(() => cleanup())
test.after(() => { mock.timers.reset(); window.history.pushState(null, '', '/'); unregister(); closeDom() })

test('before a run there are no results at all, only the plan and what a run will produce', () => {
  mount()
  assert.equal(hasMetrics(), false)
  assert.equal(screen.queryAllByRole('tab').length, 0, 'no level tabs yet')
  assert.equal(screen.queryAllByRole('img').length, 0, 'no charts yet')
  assert.equal(screen.queryByRole('list', { name: 'Method summary' }), null, 'no method scores yet')
  assert.doesNotMatch(document.body.textContent, /0\.76|38\/50/, 'no recorded number leaks before the run')

  const gate = screen.getByTestId('showcase-run-gate')
  assert.equal(gate.dataset.stage, 'idle')
  assert.ok(within(gate).getByText('Not run yet'))
  assert.match(gate.textContent, new RegExp(`${METHODS.join(', ')} × spider, ${batch.sample_count} questions each`))
  const levels = within(within(gate).getByRole('list', { name: 'Metric levels' })).getAllByRole('listitem')
  assert.deepEqual(levels.map(item => item.textContent.slice(0, 2)), ['L1', 'L2', 'L3', 'L4', 'L5', 'L6'])
  assert.ok(within(gate).getByRole('button', { name: 'Run experiment (mock)' }))
})

test('while the run is going every method shows progress and still no results appear', () => {
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Run experiment (mock)' }))
  assert.equal(screen.getByTestId('showcase-run-gate').dataset.stage, 'running')
  const jobs = within(screen.getByRole('list', { name: 'Config jobs' })).getAllByRole('listitem')
  assert.deepEqual(jobs.map(job => job.dataset.method), METHODS)
  assert.deepEqual(METHODS.map(progressOf), [0, 0, 0, 0])

  advance(4500)  // half of the slowest job
  assert.ok(progressOf('c3sql') > 20 && progressOf('c3sql') < 30, 'c3sql, the slowest, is about half way')
  assert.equal(progressOf('unisar'), batch.sample_count, 'unisar, the fastest, has finished by now')
  assert.ok(screen.getAllByText('Finished').length >= 1 && screen.getAllByText('Running').length >= 1)
  assert.equal(hasMetrics(), false, 'one finished job is not enough')
  assert.equal(screen.queryAllByRole('tab').length, 0)
})

test('the results are generated only when every job has finished, then they fade in with a notice', () => {
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Run experiment (mock)' }))
  for (let elapsed = 0; elapsed < 9000; elapsed += 500) {
    advance(500)
    assert.equal(hasMetrics(), false, `still running at ${elapsed + 500}ms`)
  }
  assert.equal(progressOf('c3sql'), batch.sample_count)
  assert.match(screen.getByTestId('showcase-run-gate').textContent, /generating the comparison/)
  advance(900)
  assert.equal(hasMetrics(), true)
  assert.equal(screen.queryByTestId('showcase-run-gate'), null, 'the run panel gives way to the results')
  assert.equal(screen.getAllByRole('tab').length, 6)
  assert.match(screen.getByRole('status').textContent, /Comparison generated: 4 methods, 1 valid\./)
  assert.ok(screen.getByRole('tabpanel'))
})

test('the run can be cleared and started again', () => {
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Run experiment (mock)' }))
  advance(10000)
  assert.equal(hasMetrics(), true)
  fireEvent.click(screen.getByRole('button', { name: 'Clear results and start over' }))
  assert.equal(hasMetrics(), false)
  assert.equal(screen.getByTestId('showcase-run-gate').dataset.stage, 'idle')
  fireEvent.click(screen.getByRole('button', { name: 'Run experiment (mock)' }))
  assert.deepEqual(METHODS.map(progressOf), [0, 0, 0, 0], 'a second run starts from zero')
  assert.equal(hasMetrics(), false)
})

test('a link to a metrics level, or ?showcaseRun=done, opens the finished results directly', () => {
  mount('?metricsLevel=l4')
  assert.equal(hasMetrics(), true)
  assert.equal(screen.getByRole('tabpanel').dataset.level, 'l4')
  mount('?showcaseRun=done')
  assert.equal(hasMetrics(), true)
  mount('?unrelated=1')
  assert.equal(hasMetrics(), false)
})
