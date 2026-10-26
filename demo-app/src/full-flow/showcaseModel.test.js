import assert from 'node:assert/strict'
import test from 'node:test'

import fixture from './showcaseFixture.js'
import {
  buildFlow, buildSchedule, compareView, composeView, CLICK, cursorAt, describeResult, locate, pairOutcome,
  phaseAt, ranking, runView, stageProgress, stageText, summarize, totalsAt,
} from './showcaseModel.js'

const schedule = buildSchedule(fixture)
const t = (key, params = {}) => `${key}:${JSON.stringify(params)}`

test('the schedule lays questions end to end', () => {
  assert.equal(schedule.questions.length, fixture.questions.length)
  schedule.questions.reduce((expected, question) => {
    assert.ok(Math.abs(question.start - expected) < 1e-9)
    return question.start + question.duration
  }, 0)
  const last = schedule.questions.at(-1)
  assert.ok(Math.abs(schedule.total - (last.start + last.duration)) < 1e-9)
})

test('every verdict lands after its last stage finishes', () => {
  for (const question of schedule.questions) {
    for (const lane of Object.values(question.lanes)) {
      assert.ok(lane.verdict > lane.stages.at(-1)[1])
      assert.ok(lane.verdict < question.duration)
    }
  }
})

test('locate clamps to the timeline and finds the playing question', () => {
  assert.deepEqual(locate(schedule, -5), { index: 0, local: 0 })
  const second = schedule.questions[1]
  assert.equal(locate(schedule, second.start + 1).index, 1)
  assert.equal(locate(schedule, schedule.total + 99).index, fixture.questions.length - 1)
})

test('a stage types out only while it runs', () => {
  const window = [1, 3]
  assert.deepEqual(stageProgress(window, 0.5), { state: 'idle', typed: 0 })
  assert.deepEqual(stageProgress(window, 3), { state: 'done', typed: 1 })
  const running = stageProgress(window, 2)
  assert.equal(running.state, 'run')
  assert.ok(running.typed > 0 && running.typed < 1)
})

test('calibration stages show the note and the rewrite', () => {
  assert.equal(stageText({ kind: 'text', text: 'plain' }), 'plain')
  assert.equal(stageText({ kind: 'calib', text: JSON.stringify({ note: 'ok → keep', sql: null }) }), 'ok → keep')
  assert.equal(stageText({ kind: 'calib', text: JSON.stringify({ note: 'fix', sql: 'SELECT 1' }) }), 'fix\nSELECT 1')
})

test('totals start empty and end at the recorded outcome', () => {
  for (const sum of Object.values(totalsAt(fixture, schedule, 0))) {
    assert.deepEqual(sum, { ex: 0, judged: 0, tokens: 0, seconds: sum.seconds })
    assert.equal(sum.seconds, 0)
  }
  const final = totalsAt(fixture, schedule, schedule.total)
  for (const method of fixture.methods) {
    const expected = fixture.questions.filter(question => question.methods[method.id].ex).length
    assert.equal(final[method.id].ex, expected)
    assert.equal(final[method.id].judged, fixture.questions.length)
  }
})

test('the ranking orders by EX then by tokens', () => {
  const ranked = ranking(fixture)
  for (let i = 1; i < ranked.length; i += 1) {
    assert.ok(ranked[i - 1].ex > ranked[i].ex || (ranked[i - 1].ex === ranked[i].ex && ranked[i - 1].tokens <= ranked[i].tokens))
  }
})

test('results read as a row count or an execution error', () => {
  assert.match(describeResult({ ok: false, error: 'boom' }, t), /showcase.error.*boom/)
  const text = describeResult({ ok: true, n: 3, head: [['a', 'b']], ms: 1 }, t)
  assert.match(text, /showcase.rows.*"count":3/)
  assert.ok(text.includes('(a, b) …'))
})

test('the fixture is a replay of the real run and covers every question for every method', () => {
  assert.equal(fixture.notes.mock, false)
  assert.equal(fixture.n, 50)
  for (const question of fixture.questions) {
    for (const method of fixture.methods) assert.ok(question.methods[method.id], `${question.id}/${method.id}`)
  }
  const hits = id => fixture.questions.filter(question => question.methods[id].ex).length
  assert.deepEqual(['c3sql', 'dinsql', 'finsql', 'unisar'].map(hits), [38, 41, 37, 7], 'the same outcomes as the recorded runs')
})

const flow = buildFlow(fixture)
const plan = fixture.flow.plan

test('the flow runs compose, run and compare back to back', () => {
  assert.equal(flow.compose.start, 0)
  assert.ok(Math.abs(flow.run.start - flow.compose.duration) < 1e-9)
  assert.ok(Math.abs(flow.compare.start - (flow.run.start + flow.run.duration)) < 1e-9)
  assert.ok(Math.abs(flow.total - (flow.compare.start + flow.compare.duration)) < 1e-9)
  assert.equal(phaseAt(flow, -1).phase, 'compose')
  assert.equal(phaseAt(flow, flow.run.start + 1).phase, 'run')
  assert.equal(phaseAt(flow, flow.compare.start + 1).phase, 'compare')
  assert.equal(phaseAt(flow, flow.total + 50).phase, 'compare')
})

test('the sample limit is one of the options the real Demo offers', () => {
  assert.ok([3, 10, 20, 50, 100, 200].includes(plan.sampling.limit))
  assert.equal(plan.sampling.limit, fixture.questions.length, 'the recording has exactly that many questions')
})

test('every click lands CLICK seconds after the cursor starts moving, in order', () => {
  for (const phase of ['compose', 'run', 'compare']) {
    const steps = flow[phase].steps
    steps.forEach((step, i) => {
      assert.ok(Math.abs(step.effect - step.at - CLICK) < 1e-9)
      if (i) assert.ok(step.at >= steps[i - 1].effect, `${phase}/${step.id} waits for the previous click`)
      assert.ok(step.effect < flow[phase].duration)
    })
  }
})

test('the cursor aims at the latest step and flashes only just after a click', () => {
  const [open] = flow.compose.steps
  assert.deepEqual(cursorAt(flow, 'compose', 0), { target: null, click: null })
  assert.deepEqual(cursorAt(flow, 'compose', open.at + 0.1), { target: 'limit', click: null })
  assert.equal(cursorAt(flow, 'compose', open.effect + 0.1).click, 'open-limit')
  assert.equal(cursorAt(flow, 'compose', open.effect + 0.6).click, null)
})

test('compose: open the dropdown, choose the limit, tick cells, press go', () => {
  const steps = Object.fromEntries(flow.compose.steps.map(step => [step.id, step]))
  assert.deepEqual(composeView(flow, 0), { limitOpen: false, limit: 20, ticked: 0, pressed: false })
  assert.equal(composeView(flow, steps['open-limit'].effect + 0.1).limitOpen, true)
  const chosen = composeView(flow, steps['pick-limit'].effect + 0.1)
  assert.deepEqual([chosen.limitOpen, chosen.limit], [false, plan.sampling.limit])
  assert.equal(composeView(flow, steps[`tick-${plan.methods[1]}`].effect + 0.01).ticked, 2)
  assert.deepEqual(composeView(flow, flow.compose.duration), { limitOpen: false, limit: plan.sampling.limit, ticked: plan.methods.length, pressed: true })
})

test('the dropdown option the cursor picks is the plan limit', () => {
  const pick = flow.compose.steps.find(step => step.id === 'pick-limit')
  assert.equal(pick.target, `limit-${plan.sampling.limit}`)
})

test('run: nothing runs until "run config" lands, and the jobs finish before the comparison link', () => {
  assert.deepEqual(runView(flow, 0), { started: false, at: 0, done: false })
  const press = flow.run.steps.find(step => step.id === 'run')
  const after = runView(flow, press.effect + 0.01)
  assert.equal(after.started, true)
  assert.ok(flow.run.runAt >= press.effect)
  assert.equal(runView(flow, flow.run.duration).done, true)
  const view = flow.run.steps.find(step => step.id === 'view')
  assert.ok(view.at >= flow.run.runAt + flow.run.schedule.total, 'the link is clicked after the jobs are done')
})

test('compare: benchmark first, then methods one by one, then the pairwise diff', () => {
  assert.deepEqual(compareView(flow, 0), { benchmark: false, members: 0, diffOpen: false, diffs: 0 })
  const bench = flow.compare.steps.find(step => step.id === 'bench')
  assert.equal(compareView(flow, bench.effect + 0.01).benchmark, true)
  const end = compareView(flow, flow.compare.duration)
  assert.deepEqual(end, { benchmark: true, members: plan.methods.length, diffOpen: true, diffs: fixture.questions.length })
  const diff = flow.compare.steps.find(step => step.id === 'diff')
  assert.ok(Math.min(...flow.compare.diffAt) > diff.effect)
})

test('the planned methods match the methods that were run and compared', () => {
  assert.deepEqual([...plan.methods].sort(), fixture.methods.map(method => method.id).sort())
  for (const id of plan.methods) assert.ok(fixture.flow.grid.methods.some(method => method.id === id), id)
  assert.ok(plan.diff.every(id => plan.methods.includes(id)))
})

test('a pair of methods lands in exactly one outcome per question', () => {
  const [a, b] = plan.diff
  const counts = { both: 0, a: 0, b: 0, neither: 0 }
  for (const question of fixture.questions) counts[pairOutcome(question, a, b)] += 1
  assert.equal(Object.values(counts).reduce((x, y) => x + y, 0), fixture.questions.length)
  assert.equal(counts.both + counts.a, fixture.questions.filter(question => question.methods[a].ex).length)
})

test('the summary is ranked and counts every question', () => {
  const rows = summarize(fixture)
  assert.equal(rows.length, fixture.methods.length)
  assert.ok(rows.every(row => row.n === fixture.questions.length && row.avgSeconds > 0))
  assert.deepEqual(rows.map(row => row.id), ranking(fixture).map(row => row.id))
})
