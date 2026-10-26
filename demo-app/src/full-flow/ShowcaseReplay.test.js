import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { act, cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: ShowcaseReplay } = await import('./ShowcaseReplay.jsx')
const { translate } = await import('../i18n/index.js')
const { default: fixture } = await import('./showcaseFixture.js')
const { buildFlow } = await import('./showcaseModel.js')

const t = (key, params) => translate('en-US', key, params)
const flow = buildFlow(fixture)
const plan = fixture.flow.plan
const planned = plan.methods.length
const stepOf = (phase, id) => flow[phase].steps.find(step => step.id === id)

// A manual animation clock: each step() runs the callback requested by the previous frame.
function installClock() {
  let callback = null
  let now = 0
  globalThis.requestAnimationFrame = fn => { callback = fn; return 1 }
  globalThis.cancelAnimationFrame = () => { callback = null }
  return {
    step: seconds => act(() => {
      now += seconds * 1000
      const run = callback
      callback = null
      run?.(now)
    }),
    pending: () => callback !== null,
  }
}
const seekTo = seconds => fireEvent.change(screen.getByRole('slider'), { target: { value: String(Math.round((1000 * seconds) / flow.total)) } })
const phase = () => screen.getByTestId('showcase').getAttribute('data-phase')

test.after(() => { cleanup(); unregister(); closeDom() })

const fresh = () => { installClock(); cleanup(); render(React.createElement(ShowcaseReplay, { t })) }
const at = (phase, seconds) => seekTo(flow[phase].start + seconds)
const cursor = () => screen.getByTestId('showcase-cursor')

test('compose: the cursor opens the limit dropdown and picks the real option', () => {
  fresh()
  assert.equal(phase(), 'compose')
  const select = screen.getByRole('button', { name: /Up to 20 questions/ })
  assert.equal(select.getAttribute('aria-expanded'), 'false')
  assert.equal(screen.queryByRole('listbox'), null)

  at('compose', stepOf('compose', 'open-limit').effect + 0.1)
  assert.equal(screen.getByRole('listbox') !== null, true)
  assert.equal(within(screen.getByRole('listbox')).getAllByRole('option').length, 6, 'the six real limit options')
  assert.equal(cursor().getAttribute('data-target'), 'limit')

  at('compose', stepOf('compose', 'pick-limit').effect + 0.1)
  assert.equal(screen.queryByRole('listbox'), null, 'choosing closes the dropdown')
  assert.ok(screen.getByRole('button', { name: new RegExp(`Up to ${plan.sampling.limit} questions`) }))
})

test('compose: no cell shows a score before the run, not even an earlier recording', () => {
  fresh()
  assert.deepEqual(fixture.flow.grid.recorded, {})
  const cells = [...document.querySelectorAll('[data-cell]')].filter(cell => !cell.className.includes('off'))
  assert.equal(cells.length, 8 * 7)
  assert.ok(cells.every(cell => cell.textContent === 'no record'), 'every installed cell reads "no record"')
  assert.doesNotMatch(document.body.textContent, /\d+%/)
})

test('compose: cells get ticked one by one, then "go to run" is pressed', () => {
  fresh()
  const ticked = () => screen.getAllByRole('checkbox').filter(box => box.checked).length
  assert.equal(screen.getAllByRole('checkbox').length, 8 * 7, 'the not-installed benchmark has no checkboxes')
  assert.equal(ticked(), 0)
  // the seek slider has 1/1000 resolution of the whole replay, so leave a margin
  at('compose', stepOf('compose', `tick-${plan.methods[1]}`).effect + 0.3)
  assert.equal(ticked(), 2)
  assert.ok(screen.getByText(new RegExp(`2 cells · 0 variants · ${plan.sampling.limit} questions each`)))
  at('compose', flow.compose.duration - 0.1)
  assert.equal(ticked(), planned)
  assert.ok(screen.getByRole('button', { name: 'Go to run' }).className.includes('pressed'))
})

test('run: nothing is queued until "Run config" is pressed, then every cell gets a job', () => {
  fresh()
  at('run', 0.2)
  assert.equal(phase(), 'run')
  assert.ok(screen.getByText(/No config jobs yet/))
  assert.equal(screen.queryAllByRole('article').length, 0)

  at('run', flow.run.runAt + 12)
  const jobs = screen.getByRole('list', { name: 'Config jobs' })
  assert.equal(within(jobs).getAllByRole('listitem').length, planned)
  assert.equal(screen.getAllByRole('article').length, planned, 'one lane per method')
  assert.equal(screen.queryByRole('button', { name: 'View comparison' }), null, 'not before the jobs finish')

  at('run', flow.run.duration - 0.2)
  assert.equal(within(screen.getByRole('list', { name: 'Config jobs' })).getAllByText('Finished').length, planned)
  assert.ok(screen.getByRole('button', { name: 'View comparison' }))
})

test('compare: pick the benchmark, tick methods, then diff two question by question', () => {
  fresh()
  at('compare', 0.1)
  assert.ok(screen.getByText(/Pick a benchmark, then the methods/))
  assert.equal(screen.queryByRole('table'), null)

  at('compare', stepOf('compare', `member-${plan.methods[1]}`).effect + 0.1)
  const table = screen.getByRole('table', { name: /Metrics on Spider/ })
  assert.equal(within(table).getAllByRole('row').length, 1 + 2, 'two methods ticked so far')
  assert.equal(screen.queryByRole('button', { name: 'Compare the two question by question' }), null)

  at('compare', flow.compare.duration)
  assert.equal(within(screen.getByRole('table', { name: /Metrics on Spider/ })).getAllByRole('row').length, 1 + planned)
  const diff = screen.getByRole('table', { name: 'Question by question' })
  assert.equal(within(diff).getAllByRole('row').length, 1 + fixture.questions.length)
  assert.ok(screen.getByRole('button', { name: 'Compare the two question by question' }))
  assert.match(screen.getByText(/Replay finished/).textContent, /DIN-SQL 41\/50 > C3SQL 38\/50 > FinSQL 37\/50 > UniSAR 7\/50/)
  const rows = within(screen.getByRole('table', { name: /Metrics on Spider/ })).getAllByRole('row').slice(1)
  const rowFor = name => rows.find(row => within(row).getByRole('rowheader').textContent.startsWith(name))
  assert.match(rowFor('DIN-SQL').textContent, /★/, 'the best valid run is starred')
  for (const name of ['C3SQL', 'FinSQL', 'UniSAR']) assert.doesNotMatch(rowFor(name).textContent, /★/)
  for (const name of ['C3SQL', 'DIN-SQL', 'FinSQL', 'UniSAR']) assert.doesNotMatch(rowFor(name).textContent, /invalid run/, `${name} is valid in the recorded run`)
})

test('plays by itself: the clock drives the cursor and the click ripple', () => {
  const clock = installClock()
  cleanup()
  render(React.createElement(ShowcaseReplay, { t }))
  clock.step(0)
  const open = stepOf('compose', 'open-limit')
  clock.step(open.at + 0.05)
  assert.equal(cursor().getAttribute('data-target'), 'limit')
  assert.equal(cursor().querySelector('.showcase-ripple'), null)
  clock.step(open.effect - open.at - 0.05 + 0.1)
  assert.ok(cursor().querySelector('.showcase-ripple'), 'a ripple shows when the click lands')
})

test('seeking pauses playback and the phase pills jump to their phase', () => {
  const clock = installClock()
  cleanup()
  render(React.createElement(ShowcaseReplay, { t }))
  seekTo(flow.total)
  assert.equal(phase(), 'compare')
  assert.equal(clock.pending(), false)
  assert.ok(screen.getByRole('button', { name: 'Play' }))
  fireEvent.click(screen.getByRole('button', { name: /Compose/ }))
  assert.equal(phase(), 'compose')
  fireEvent.click(screen.getByRole('button', { name: /^Run|Run$/ }))
  assert.equal(phase(), 'run')
})

test('reports the stage so results exist only after the run: idle, running, then ready', () => {
  installClock()
  cleanup()
  const seen = []
  render(React.createElement(ShowcaseReplay, { t, onStage: stage => seen.push(stage) }))
  const stageAt = seconds => { seekTo(seconds); return seen.at(-1) }
  assert.equal(seen[0], 'idle', 'nothing has run when the replay opens')
  assert.equal(stageAt(flow.compose.duration - 0.5), 'idle', 'composing the plan is not running it')
  assert.equal(stageAt(flow.run.start + flow.run.runAt - 1), 'idle', 'before "Run config" lands the plan still has not run')
  assert.equal(stageAt(flow.run.start + flow.run.runAt + 5), 'running')
  assert.equal(stageAt(flow.run.start + flow.run.runAt + flow.run.schedule.total - 3), 'running', 'one unfinished job keeps it running')
  assert.equal(stageAt(flow.run.start + flow.run.duration - 0.2), 'ready', 'all jobs done: the comparison is produced')
  assert.equal(stageAt(flow.compare.start + 1), 'ready')
  assert.equal(stageAt(0), 'idle', 'rewinding takes the results away again')
})

test('does not run its clock while the step is hidden', () => {
  const clock = installClock()
  cleanup()
  render(React.createElement(ShowcaseReplay, { t, active: false }))
  assert.equal(clock.pending(), false)
})
