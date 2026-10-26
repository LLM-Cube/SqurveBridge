// Lanes: what one question looks like on screen, whatever it is played from.
// A recording and a live run both reduce to the same lane objects, so one renderer shows both.
import { locate, stageProgress, stageText } from './showcaseModel.js'

const STAGE_STATE = { done: 'done', failed: 'done', running: 'run' }

/** Lanes for the question playing at `local` seconds of a recorded run. */
export function lanesFromRecording({ fixture, schedule, index, local, totals }) {
  const question = fixture.questions[index]
  return fixture.flow.plan.methods.map(id => {
    const run = question.methods[id]
    const lane = schedule.questions[index].lanes[id]
    return {
      id,
      label: fixture.methods.find(method => method.id === id)?.label || id,
      ex: totals[id].ex,
      judged: totals[id].judged,
      stages: run.stages.map((stage, i) => ({
        name: stage.name,
        kind: stage.kind,
        text: stageText(stage),
        seconds: stage.seconds,
        tokens: stage.tokens,
        ...stageProgress(lane.stages[i], local),
      })),
      verdict: { ready: local >= lane.verdict, ex: run.ex, pred_sql: run.pred_sql, result: run.result },
    }
  })
}

// null when no stage reported the field: "not provided" must not read as zero.
const total = (runs, field) => {
  const values = runs.flatMap(run => (run.stages || []).map(stage => stage[field])).filter(value => typeof value === 'number')
  return values.length ? values.reduce((a, b) => a + b, 0) : null
}
/** Per method: how many questions were judged right, judged at all, finished, and planned. */
export function scoreboard(cells) {
  return Object.fromEntries(cells.map(cell => {
    const runs = cell.questions.map(item => item.run)
    return [cell.method, {
      ex: runs.filter(run => run.ex === true).length,
      judged: runs.filter(run => run.ex !== null && run.ex !== undefined).length,
      done: runs.filter(run => run.state === 'done').length,
      tokens: total(runs, 'tokens'),
      seconds: total(runs, 'seconds'),
      total: cell.total || runs.length,
    }]
  }))
}

/** The question to show: the first one any method is still working on, else the last. */
export function focusOf(cells) {
  const pending = cells.flatMap(cell => cell.questions.filter(item => item.run.state !== 'done').map(item => item.index))
  if (pending.length) return Math.min(...pending)
  return Math.max(0, ...cells.flatMap(cell => cell.questions.map(item => item.index)))
}

/** Lanes for question `index` from the cells of a live timeline. */
export function lanesFromTimeline(cells, index) {
  const totals = scoreboard(cells)
  return cells.map(cell => {
    const run = cell.questions.find(item => item.index === index)?.run
    const stages = (run?.stages || []).map(stage => ({
      name: stage.name,
      kind: stage.kind,
      text: stage.state === 'failed' ? `error: ${stage.error || 'failed'}` : stage.text,
      seconds: stage.seconds,
      tokens: stage.tokens,
      state: STAGE_STATE[stage.state] || 'idle',
      typed: stage.state === 'done' || stage.state === 'failed' ? 1 : 0,
    }))
    return {
      id: cell.method,
      label: cell.label || cell.method,
      ex: totals[cell.method].ex,
      judged: totals[cell.method].judged,
      stages,
      verdict: {
        ready: run?.state === 'done',
        ex: run?.ex ?? null,
        pred_sql: run?.pred_sql || '',
        result: run?.result || null,
        withheld: Boolean(run?.withheld),
      },
    }
  })
}

/** Which question a recording is on at time `at`, with the time inside it. */
export const recordingPosition = (schedule, at) => locate(schedule, at)
