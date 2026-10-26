// Timeline of the recorded mock run. Every question is a pure function of the
// playback time, so pause, seek and replay only have to set that time.
// A stage is shown for a short, bounded time that grows with its real duration, so 50 questions play in well under two minutes.
export const TIMING = { scale: 0.005, minStage: 0.12, maxStage: 0.8, lead: 0.15, gap: 0.04, verdictDelay: 0.2, hold: 0.3 }

const clamp = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value))

export function buildSchedule(fixture, timing = TIMING) {
  let total = 0
  const questions = fixture.questions.map(question => {
    const lanes = {}
    let end = 0
    for (const method of fixture.methods) {
      let at = timing.lead
      const stages = question.methods[method.id].stages.map(stage => {
        const start = at
        const finish = start + Math.min(timing.maxStage, timing.minStage + stage.seconds * timing.scale)
        at = finish + timing.gap
        return [start, finish]
      })
      const verdict = at + timing.verdictDelay
      lanes[method.id] = { stages, verdict }
      end = Math.max(end, verdict)
    }
    const entry = { start: total, duration: end + timing.hold, lanes }
    total += entry.duration
    return entry
  })
  return { questions, total }
}

/** The question playing at time t and the time elapsed inside it. */
export function locate(schedule, t) {
  const time = clamp(t, 0, schedule.total)
  const last = schedule.questions.length - 1
  for (let index = 0; index <= last; index += 1) {
    const question = schedule.questions[index]
    if (time < question.start + question.duration) return { index, local: time - question.start }
  }
  return { index: last, local: schedule.questions[last].duration }
}

/** One stage at local time: its state and how much of its output is typed. */
export function stageProgress([start, finish], local) {
  if (local < start) return { state: 'idle', typed: 0 }
  if (local >= finish) return { state: 'done', typed: 1 }
  return { state: 'run', typed: clamp((local - start) / ((finish - start) * 0.8)) }
}

/** Displayed output of a stage; calibration stages carry a note and an optional rewrite. */
export function stageText(stage) {
  if (stage.kind !== 'calib') return stage.text
  const { note, sql } = JSON.parse(stage.text)
  return sql ? `${note}\n${sql}` : note
}

export function describeResult(result, t) {
  if (!result.ok) return t('showcase.error', { message: result.error })
  const head = result.head.map(row => `(${row.join(', ')})`).join(' ')
  const more = result.n > result.head.length ? ' …' : ''
  return `${t('showcase.rows', { count: result.n })}${head ? ` · ${head}${more}` : ''}`
}

/** Running scoreboard of every method at time t. */
export function totalsAt(fixture, schedule, t) {
  const { index, local } = locate(schedule, t)
  const totals = {}
  for (const method of fixture.methods) {
    const sum = { ex: 0, judged: 0, tokens: 0, seconds: 0 }
    fixture.questions.forEach((question, position) => {
      if (position > index) return
      const run = question.methods[method.id]
      const lane = schedule.questions[position].lanes[method.id]
      const at = position < index ? Infinity : local
      run.stages.forEach((stage, i) => {
        if (at >= lane.stages[i][1]) {
          sum.tokens += stage.tokens
          sum.seconds += stage.seconds
        }
      })
      if (at >= lane.verdict) {
        sum.judged += 1
        sum.ex += run.ex ? 1 : 0
      }
    })
    totals[method.id] = sum
  }
  return totals
}

/** Final standing: more EX first, then fewer tokens. */
export function ranking(fixture) {
  return fixture.methods
    .map(method => ({
      id: method.id,
      label: method.label,
      ex: fixture.questions.filter(question => question.methods[method.id].ex).length,
      tokens: fixture.questions.reduce(
        (sum, question) => sum + question.methods[method.id].stages.reduce((a, stage) => a + stage.tokens, 0),
        0,
      ),
    }))
    .sort((a, b) => b.ex - a.ex || a.tokens - b.tokens)
}

// ---- Experiment flow: the scripted user walks compose -> run -> compare ------------------
// Each phase is a list of steps: the cursor starts moving to `target` at `at` and the click
// lands CLICK seconds later (`effect`). Everything on screen is derived from those times.
export const PHASES = ['compose', 'run', 'compare']
export const CLICK = 0.7
export const LIMIT_OPTIONS = [3, 10, 20, 50, 100, 200]
export const DEFAULT_LIMIT = 20
const TAIL = 1

const step = (id, target, at) => ({ id, target, at, effect: at + CLICK })

/** Stats shown in the comparison table, in ranking order. */
export function summarize(fixture) {
  const n = fixture.questions.length
  return ranking(fixture).map(entry => {
    const runs = fixture.questions.map(question => question.methods[entry.id])
    const seconds = runs.reduce((sum, run) => sum + run.stages.reduce((a, stage) => a + stage.seconds, 0), 0)
    return { ...entry, n, avgSeconds: seconds / n }
  })
}

export function buildFlow(fixture) {
  const { methods: plan, sampling } = fixture.flow.plan
  const pick = LIMIT_OPTIONS.includes(sampling.limit) ? `limit-${sampling.limit}` : `limit-${DEFAULT_LIMIT}`

  const composeSteps = [
    step('open-limit', 'limit', 0.5),
    step('pick-limit', pick, 2.0),
    ...plan.map((id, i) => step(`tick-${id}`, `cell:${id}`, 3.4 + i * 1.1)),
  ]
  composeSteps.push(step('go', 'go', composeSteps.at(-1).at + 1.5))
  const compose = { start: 0, duration: composeSteps.at(-1).effect + TAIL, steps: composeSteps }

  const schedule = buildSchedule(fixture)
  const startStep = step('run', 'run-action', 0.5)
  const runAt = startStep.effect + 0.5
  const viewStep = step('view', 'view-compare', runAt + schedule.total + 0.3)
  const run = {
    start: compose.duration, duration: viewStep.effect + TAIL, schedule, runAt, steps: [startStep, viewStep],
  }

  const compareSteps = [
    step('bench', `bench:${fixture.flow.plan.dataset}`, 0.5),
    ...plan.map((id, i) => step(`member-${id}`, `member:${id}`, 1.8 + i * 1.0)),
  ]
  const diffStep = step('diff', 'diff-open', compareSteps.at(-1).at + 1.4)
  compareSteps.push(diffStep)
  const diffAt = fixture.questions.map((_, i) => diffStep.effect + 0.5 + i * 0.4)
  const compare = {
    start: run.start + run.duration, duration: diffAt.at(-1) + 3.5, steps: compareSteps, diffAt,
  }
  return { compose, run, compare, total: compare.start + compare.duration }
}

/** Phase playing at time t, with the time elapsed inside it. */
export function phaseAt(flow, t) {
  const time = clamp(t, 0, flow.total)
  for (const phase of PHASES) {
    const { start, duration } = flow[phase]
    if (time < start + duration) return { phase, local: time - start }
  }
  return { phase: 'compare', local: flow.compare.duration }
}

const landed = (steps, id, local) => local >= steps.find(item => item.id === id).effect

/** Where the cursor is heading and which click just landed (for the ripple). */
export function cursorAt(flow, phase, local) {
  const steps = flow[phase].steps
  let target = null
  let click = null
  for (const item of steps) {
    if (item.at <= local) target = item.target
    if (local >= item.effect && local < item.effect + 0.45) click = item.id
  }
  return { target, click }
}

/** Compose: the limit dropdown, ticked cells and the "go run" press. */
export function composeView(flow, local) {
  const { steps } = flow.compose
  const open = steps.find(item => item.id === 'open-limit')
  const choose = steps.find(item => item.id === 'pick-limit')
  const chosen = local >= choose.effect
  return {
    limitOpen: local >= open.effect && !chosen,
    limit: chosen ? Number(choose.target.replace('limit-', '')) : DEFAULT_LIMIT,
    ticked: steps.filter(item => item.id.startsWith('tick-') && local >= item.effect).length,
    pressed: landed(steps, 'go', local),
  }
}

/** Run: whether "run config" was pressed, the playback time of the jobs, and whether they finished. */
export function runView(flow, local) {
  const started = landed(flow.run.steps, 'run', local)
  return {
    started,
    at: started ? clamp(local - flow.run.runAt, 0, flow.run.schedule.total) : 0,
    done: started && local - flow.run.runAt >= flow.run.schedule.total,
  }
}

/** Compare: chosen benchmark, how many methods are ticked, and the revealed per-question rows. */
export function compareView(flow, local) {
  const { steps, diffAt } = flow.compare
  return {
    benchmark: landed(steps, 'bench', local),
    members: steps.filter(item => item.id.startsWith('member-') && local >= item.effect).length,
    diffOpen: landed(steps, 'diff', local),
    diffs: diffAt.filter(at => local >= at).length,
  }
}

/** One question's outcome for a pair of methods. */
export function pairOutcome(question, a, b) {
  const first = question.methods[a].ex
  const second = question.methods[b].ex
  if (first && second) return 'both'
  if (first) return 'a'
  if (second) return 'b'
  return 'neither'
}
