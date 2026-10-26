import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: QuestionLanes } = await import('./RunLanes.jsx')
const { lanesFromRecording, lanesFromTimeline, focusOf, scoreboard } = await import('./runLanes.js')
const { default: fixture } = await import('./showcaseFixture.js')
const { buildSchedule } = await import('./showcaseModel.js')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)
test.after(() => { cleanup(); unregister(); closeDom() })

const stage = (name, state, extra = {}) => ({ name, kind: 'text', text: `${name} output`, seconds: 1.5, state, error: null, ...extra })
const question = (index, runs) => ({
  index, id: `dev_${index}`, db: 'concert_singer', question: `Question ${index}?`, gold_sql: 'SELECT 1',
  runs,
})
const cell = (method, questions, extra = {}) => ({
  cell_id: `c-${method}`, method, dataset: 'spider', total: questions.length, status: 'running', questions, ...extra,
})
const run = (state, stages, extra = {}) => ({ state, stages, pred_sql: '', ex: null, result: null, ...extra })

// Timeline questions arrive per cell; this builds the wire shape the Demo API serves.
const wire = (method, items) => cell(method, items.map(item => ({
  index: item.index, id: item.id, db: item.db, question: item.question, gold_sql: item.gold_sql, run: item.runs[method],
})))

test('a running stage is shown running and a finished stage done with all of its output', () => {
  const item = question(0, { c3sql: run('running', [stage('reduce', 'done'), stage('parse', 'running', { text: '' })]) })
  const [lane] = lanesFromTimeline([wire('c3sql', [item])], 0)
  assert.deepEqual(lane.stages.map(s => [s.name, s.state, s.typed]), [['reduce', 'done', 1], ['parse', 'run', 0]])
  assert.equal(lane.verdict.ready, false)
})

test('a finished prediction gets its verdict, a miss and an unjudged one are told apart', () => {
  const hit = run('done', [stage('generate', 'done', { kind: 'sql', text: 'SELECT 1' })], { pred_sql: 'SELECT 1', ex: true, result: { ok: true, n: 1, head: [['1']] } })
  const miss = run('done', [stage('generate', 'done', { kind: 'sql', text: 'SELECT 2' })], { pred_sql: 'SELECT 2', ex: false, result: { ok: true, n: 1, head: [['2']] } })
  const unjudged = run('done', [stage('generate', 'done', { kind: 'sql', text: 'SELECT 3' })], { pred_sql: 'SELECT 3' })
  const item = question(0, { a: hit, b: miss, c: unjudged })
  const lanes = lanesFromTimeline(['a', 'b', 'c'].map(id => wire(id, [item])), 0)
  assert.deepEqual(lanes.map(lane => lane.verdict.ex), [true, false, null])
  assert.ok(lanes.every(lane => lane.verdict.ready))
})

test('a failed stage and an empty prediction are reported, never left blank', () => {
  const failed = run('done', [stage('generate', 'failed', { kind: 'sql', text: '', error: 'connection reset' })])
  const lane = lanesFromTimeline([wire('c3sql', [question(0, { c3sql: failed })])], 0)[0]
  assert.match(lane.stages[0].text, /connection reset/)
  const html = renderLanes([lane])
  assert.match(html, /no SQL/i)
})

test('the scoreboard counts only judged questions and treats an unjudged one as undecided', () => {
  const items = [
    question(0, { c3sql: run('done', [], { ex: true, pred_sql: 'x' }) }),
    question(1, { c3sql: run('done', [], { ex: false, pred_sql: 'x' }) }),
    question(2, { c3sql: run('done', [], { ex: null, pred_sql: 'x' }) }),
    question(3, { c3sql: run('running', []) }),
  ]
  assert.deepEqual(scoreboard([wire('c3sql', items)]).c3sql, { ex: 1, judged: 2, done: 3, total: 4 })
})

test('the focus is the first question that is still running, else the last one', () => {
  const items = [
    question(0, { c3sql: run('done', []) }),
    question(1, { c3sql: run('running', []) }),
    question(2, { c3sql: run('idle', []) }),
  ]
  assert.equal(focusOf([wire('c3sql', items)]), 1)
  assert.equal(focusOf([wire('c3sql', items.map(item => ({ ...item, runs: { c3sql: run('done', []) } })))]), 2)
  assert.equal(focusOf([wire('c3sql', [])]), 0)
})

function renderLanes(lanes, info = { index: 0, total: 1, question: 'Q', db: 'd', hardness: null, gold_sql: 'SELECT 1' }) {
  cleanup()
  const { container } = render(React.createElement(QuestionLanes, { t, info, lanes }))
  return container.innerHTML
}

// The point of the single renderer: a finished run played back from a recording and the same
// run arriving from the live API must be indistinguishable on screen.
test('a recorded question and the same question arriving live render identically', () => {
  const methods = fixture.methods.map(method => method.id)
  const source = fixture.questions[1]
  const schedule = buildSchedule(fixture)
  const recorded = lanesFromRecording({
    fixture, schedule, index: 1, local: schedule.questions[1].duration,
    totals: Object.fromEntries(methods.map(id => [id, { ex: 0, judged: 0 }])),
  })
  const live = lanesFromTimeline(methods.map(id => {
    const recording = source.methods[id]
    return wire(id, [{
      index: 1, id: source.id, db: source.db, question: source.question, gold_sql: source.gold_sql,
      runs: {
        [id]: run('done', recording.stages.map(s => ({ ...s, state: 'done', error: null })), {
          pred_sql: recording.pred_sql, ex: recording.ex, result: recording.result,
        }),
      },
    }])
  }), 1).map(lane => ({ ...lane, label: fixture.methods.find(m => m.id === lane.id).label }))
  const info = { index: 1, total: fixture.n, question: source.question, db: source.db, hardness: source.hardness, gold_sql: source.gold_sql }
  const strip = lanes => lanes.map(lane => ({ ...lane, ex: 0, judged: 0 }))
  assert.equal(renderLanes(strip(live), info), renderLanes(strip(recorded), info))
})
