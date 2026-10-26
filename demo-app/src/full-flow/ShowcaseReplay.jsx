import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import fixture from './showcaseFixture.js'
import record from './showcaseRecord.js'
import {
  buildFlow,
  compareView,
  composeView,
  cursorAt,
  LIMIT_OPTIONS,
  locate,
  pairOutcome,
  phaseAt,
  PHASES,
  runView,
  stageProgress,
  summarize,
  totalsAt,
} from './showcaseModel.js'
import QuestionLanes, { JobRow } from './RunLanes.jsx'
import { lanesFromRecording } from './runLanes.js'
import './showcase.css'

const SPEEDS = [0.5, 1, 2, 4]
const PHASE_KEY = { compose: 'process.compose', run: 'process.board', compare: 'process.evidence' }
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
// ?showcaseT=SECONDS opens the replay paused at that moment (shareable link to a scene).
const startTime = total => {
  const raw = typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('showcaseT')
  const value = raw === null ? NaN : Number(raw)
  return Number.isFinite(value) ? Math.min(total, Math.max(0, value)) : null
}
const labelOf = id => fixture.methods.find(method => method.id === id)?.label || id
const benchmarkLabel = () => fixture.flow.grid.datasets.find(d => d.id === fixture.flow.plan.dataset).label

// ① Compose: sampling parameters and the method × benchmark matrix, driven by the cursor.
function ComposePhase({ flow, local, hover, t }) {
  const { grid, plan } = fixture.flow
  const { limitOpen, limit, ticked, pressed } = composeView(flow, local)
  const tickedIds = new Set(plan.methods.slice(0, ticked))
  return (
    <div className="showcase-compose">
      <section className="showcase-panel">
        <h3>{t('grid.title')}</h3>
        <p>{t('grid.description')}</p>
        <div className="showcase-params">
          <div className="showcase-field">
            <span>{t('configure.sampleLimit')}</span>
            <button type="button" tabIndex={-1} className={`showcase-select ${hover === 'limit' ? 'hover' : ''}`} data-cursor="limit" aria-haspopup="listbox" aria-expanded={limitOpen}>
              {t('grid.limitOption', { count: limit })}<i aria-hidden="true">▾</i>
            </button>
            {limitOpen ? (
              <ul className="showcase-options" role="listbox" aria-label={t('configure.sampleLimit')}>
                {LIMIT_OPTIONS.map(count => (
                  <li key={count} role="option" aria-selected={count === limit} data-cursor={`limit-${count}`} className={hover === `limit-${count}` ? 'hover' : ''}>
                    {t('grid.limitOption', { count })}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <div className="showcase-field"><span>{t('configure.sampleMode')}</span><output>{t('configure.sampleSlice')}</output></div>
          <div className="showcase-field"><span>{t('configure.sampleSeed')}</span><output className="muted">{plan.sampling.seed}</output></div>
        </div>
      </section>
      <section className="showcase-panel">
        <h3>{t('grid.selectionTitle')}<em>{t('grid.selected', { count: ticked })}</em></h3>
        <div className="showcase-matrix-wrap">
          <table className="showcase-matrix" aria-label={t('grid.title')}>
            <thead>
              <tr><th>{t('compare.member')}</th>{grid.datasets.map(d => <th key={d.id}>{d.label}</th>)}</tr>
            </thead>
            <tbody>
              {grid.methods.map(method => (
                <tr key={method.id}>
                  <th scope="row">{method.label}</th>
                  {grid.datasets.map(dataset => {
                    const planned = dataset.id === plan.dataset && plan.methods.includes(method.id)
                    const checked = planned && tickedIds.has(method.id)
                    const missing = grid.not_installed.includes(dataset.id)
                    const score = grid.recorded[`${method.id}/${dataset.id}`]
                    const cursor = planned ? `cell:${method.id}` : undefined
                    return (
                      <td
                        key={dataset.id}
                        className={`${checked ? 'ticked' : ''} ${missing ? 'off' : ''} ${cursor && hover === cursor ? 'hover' : ''}`}
                        data-cell={`${method.id}/${dataset.id}`}
                        data-cursor={cursor}
                      >
                        {missing ? t('grid.notInstalled') : (
                          <>
                            <input type="checkbox" checked={checked} readOnly tabIndex={-1} aria-label={`${method.label} × ${dataset.label}`} />
                            <span>{score != null ? `${Math.round(score * 100)}%` : t('grid.noScore')}</span>
                          </>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="showcase-plan">
          <div>
            <small>{t('grid.workloadTitle')}</small>
            <strong>{t('plan.summary', { cells: ticked, variants: 0, limit, questions: ticked * limit })}</strong>
            <small>{t('grid.workloadHelp')}</small>
          </div>
          <button type="button" tabIndex={-1} data-cursor="go" className={`showcase-go ${pressed ? 'pressed' : ''} ${hover === 'go' ? 'hover' : ''}`}>
            {t('compose.goToRun')}
          </button>
        </div>
      </section>
    </div>
  )
}

// ② Run: confirm the plan, press "run config", watch one job per cell, then open the comparison.
function RunPhase({ flow, local, hover, t }) {
  const { started, at, done } = runView(flow, local)
  const { index, local: inQuestion } = locate(flow.run.schedule, at)
  const question = fixture.questions[index]
  const totals = totalsAt(fixture, flow.run.schedule, at)
  const planned = fixture.flow.plan.methods
  const { sampling } = fixture.flow.plan
  const n = fixture.n
  return (
    <div className="showcase-run">
      <section className="showcase-panel">
        <h3>{t('board.title')}</h3>
        <p>{t('board.description')}</p>
        <div className="showcase-plan">
          <div>
            <small>{t('plan.sampling', { limit: sampling.limit, mode: t('configure.sampleSlice'), seed: sampling.seed })}</small>
            <strong>{t('plan.summary', { cells: planned.length, variants: 0, limit: sampling.limit, questions: planned.length * sampling.limit })}</strong>
          </div>
          <div className="showcase-actions">
            <button type="button" tabIndex={-1} data-cursor="run-action" disabled={started} className={`showcase-go ${started ? 'pressed' : ''} ${hover === 'run-action' ? 'hover' : ''}`}>
              {t('run.action')}
            </button>
            {done ? (
              <button type="button" tabIndex={-1} data-cursor="view-compare" className={`showcase-go alt ${hover === 'view-compare' ? 'hover' : ''}`}>
                {t('plan.viewComparison')}
              </button>
            ) : null}
          </div>
        </div>
      </section>
      <section className="showcase-panel">
        <h3>{t('run.batchJobs')}</h3>
        {!started ? <p className="showcase-empty">{t('run.batchEmpty')}</p> : (
          <ul className="showcase-jobs" aria-label={t('run.batchJobs')}>
            {planned.map(id => {
              const lane = flow.run.schedule.questions[index].lanes[id]
              const sum = totals[id]
              const finished = sum.judged >= n
              const live = lane.stages.findIndex(window => stageProgress(window, inQuestion).state === 'run')
              const stage = !finished && live >= 0 ? question.methods[id].stages[live].name : null
              return (
                <JobRow
                  key={id} id={id} label={`${labelOf(id)} → ${benchmarkLabel()}`} finished={finished}
                  status={finished ? t('plan.status.done') : t('plan.status.running')} done={sum.judged} total={n} stage={stage}
                />
              )
            })}
          </ul>
        )}
      </section>

      {started ? (
        <>
          <QuestionLanes
            t={t}
            info={{ index, total: n, question: question.question, db: question.db, hardness: question.hardness, gold_sql: question.gold_sql }}
            lanes={lanesFromRecording({ fixture, schedule: flow.run.schedule, index, local: inQuestion, totals })}
          />
        </>
      ) : null}
    </div>
  )
}

// ③ Compare: pick the benchmark, tick the methods, read the table, then diff two of them.
function ComparePhase({ flow, local, hover, t }) {
  const summary = useMemo(() => Object.fromEntries(summarize(fixture).map(row => [row.id, row])), [])
  const { benchmark, members, diffOpen, diffs } = compareView(flow, local)
  const { plan, grid } = fixture.flow
  const chosen = plan.methods.slice(0, members)
  const rows = chosen.map(id => summary[id])
  // Validity comes from the recorded run: a run whose model calls failed is shown, but never starred.
  const validIds = new Set(record.batches.at(-1).ranking)
  const best = Math.max(0, ...rows.filter(row => validIds.has(row.id)).map(row => row.ex))
  const maxTokens = Math.max(1, ...rows.map(row => row.tokens))
  const label = benchmarkLabel()
  const [a, b] = plan.diff
  const all = members === plan.methods.length
  return (
    <div className="showcase-compare">
      <section className="showcase-panel">
        <h3>{t('compare.title')}<em>{benchmark ? label : ''}</em></h3>
        <div className="showcase-chips" role="group" aria-label={t('compare.benchmark')}>
          {grid.datasets.map(dataset => {
            const cursor = dataset.id === plan.dataset ? `bench:${dataset.id}` : undefined
            return (
              <button
                key={dataset.id}
                type="button"
                tabIndex={-1}
                data-cursor={cursor}
                aria-pressed={benchmark && dataset.id === plan.dataset}
                className={`${benchmark && dataset.id === plan.dataset ? 'on' : ''} ${cursor && hover === cursor ? 'hover' : ''}`}
              >
                {dataset.label}
              </button>
            )
          })}
        </div>
        {!benchmark ? <p className="showcase-empty">{t('compare.pickMethods')}</p> : (
          <>
            <ul className="showcase-members" aria-label={t('compare.members', { benchmark: label })}>
              {plan.methods.map((id, i) => (
                <li key={id} data-cursor={`member:${id}`} className={hover === `member:${id}` ? 'hover' : ''}>
                  <input type="checkbox" checked={i < members} readOnly tabIndex={-1} aria-label={labelOf(id)} />
                  {labelOf(id)}
                </li>
              ))}
            </ul>
            {rows.length ? (
              <table className="showcase-table" aria-label={t('compare.tableLabel', { benchmark: label })}>
                <thead>
                  <tr>
                    <th>{t('compare.member')}</th><th>EX</th><th>{t('compare.samples')}</th><th>{t('compare.tokens')}</th><th>{t('compare.latency')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr key={row.id} data-method={row.id} className="in">
                      <th scope="row">{row.label}{all && validIds.has(row.id) && row.ex === best ? <i aria-label="best">★</i> : null}{validIds.has(row.id) ? null : <small className="showcase-invalid"> · {t('showcase.metrics.invalidShort')}</small>}</th>
                      <td>
                        <div className="showcase-track"><div style={{ width: `${(100 * row.ex) / row.n}%` }} /></div>
                        {Math.round((100 * row.ex) / row.n)}% <small>({row.ex}/{row.n})</small>
                      </td>
                      <td>{row.n}</td>
                      <td>
                        <div className="showcase-track"><div style={{ width: `${(100 * row.tokens) / maxTokens}%` }} /></div>
                        ≈{row.tokens}
                      </td>
                      <td>{row.avgSeconds.toFixed(1)}s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {all ? (
              <button type="button" tabIndex={-1} data-cursor="diff-open" className={`showcase-go alt ${diffOpen ? 'pressed' : ''} ${hover === 'diff-open' ? 'hover' : ''}`}>
                {t('compare.diffPair')}
              </button>
            ) : null}
          </>
        )}
      </section>
      {diffOpen ? (
        <section className="showcase-panel">
          <h3>{t('showcase.diffTitle')}<em>{labelOf(a)} ↔ {labelOf(b)}</em></h3>
          <table className="showcase-diff" aria-label={t('showcase.diffTitle')}>
            <thead>
              <tr><th>#</th><th>{t('showcase.question')}</th><th>{labelOf(a)}</th><th>{labelOf(b)}</th><th>{t('showcase.outcome')}</th></tr>
            </thead>
            <tbody>
              {fixture.questions.map((question, i) => {
                const outcome = pairOutcome(question, a, b)
                const text = { both: t('showcase.both'), neither: t('showcase.neither'), a: t('showcase.only', { name: labelOf(a) }), b: t('showcase.only', { name: labelOf(b) }) }[outcome]
                return (
                  <tr key={question.id} className={i < diffs ? 'in' : 'out'}>
                    <td>{i + 1}</td>
                    <td title={question.question}>{question.question} <small>{question.hardness}</small></td>
                    {[a, b].map(id => {
                      const hit = question.methods[id].ex
                      return <td key={id} className={hit ? 'hit' : 'miss'}>{hit ? '✓' : '✗'}</td>
                    })}
                    <td className={`outcome ${outcome}`}>{text}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  )
}

// Pages stay mounted while hidden, so the clock only runs while the step is shown.
export default function ShowcaseReplay({ t, active = true, onStage }) {
  const flow = useMemo(() => buildFlow(fixture), [])
  const initial = useMemo(() => startTime(flow.total), [flow.total])
  const [time, setTime] = useState(initial ?? 0)
  const [playing, setPlaying] = useState(() => initial === null && !reducedMotion())
  const [speed, setSpeed] = useState(1)
  const clock = useRef({ time: initial ?? 0, last: null })

  const seek = useCallback(next => {
    clock.current.time = Math.min(flow.total, Math.max(0, next))
    setTime(clock.current.time)
  }, [flow.total])

  useEffect(() => {
    if (!playing || !active || typeof requestAnimationFrame !== 'function') return undefined
    let frame
    const tick = now => {
      const state = clock.current
      if (state.last !== null) state.time = Math.min(flow.total, state.time + ((now - state.last) / 1000) * speed)
      state.last = now
      setTime(state.time)
      if (state.time >= flow.total) setPlaying(false)
      else frame = requestAnimationFrame(tick)
    }
    clock.current.last = null
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing, active, speed, flow.total])

  const finished = time >= flow.total
  const { phase, local } = phaseAt(flow, time)
  const cursor = cursorAt(flow, phase, local)
  // Results exist only once the run has finished: not before "run", not while jobs are still going.
  const run = phase === 'run' ? runView(flow, local) : null
  const stage = phase === 'compare' || run?.done ? 'ready' : run?.started ? 'running' : 'idle'
  useEffect(() => { onStage?.(stage) }, [stage, onStage])
  const rootRef = useRef(null)
  const cursorRef = useRef(null)

  // The cursor glides to the element it is aimed at; the CSS transition does the gliding.
  useLayoutEffect(() => {
    const root = rootRef.current
    const pointer = cursorRef.current
    if (!root || !pointer || !cursor.target) return
    const element = root.querySelector(`[data-cursor="${cursor.target}"]`)
    if (!element) return
    const box = element.getBoundingClientRect()
    const base = root.getBoundingClientRect()
    pointer.style.transform = `translate(${box.left - base.left + Math.min(box.width * 0.5, 80)}px, ${box.top - base.top + box.height * 0.6}px)`
    pointer.dataset.ready = 'true'
  })
  const togglePlay = () => {
    if (finished) seek(0)
    setPlaying(value => !value)
  }

  return (
    <section ref={rootRef} className="showcase" data-testid="showcase" data-phase={phase} aria-label={t('showcase.title')}>
      <header className="showcase-head">
        <div>
          <h2>{t('showcase.title')} <span className="showcase-badge">{t('showcase.mock')}</span></h2>
          <p>{t('showcase.subtitle', { methods: fixture.flow.plan.methods.map(labelOf).join(' · '), count: fixture.n })}</p>
        </div>
        <div className="showcase-controls">
          <button type="button" onClick={togglePlay}>{playing ? t('showcase.pause') : t('showcase.play')}</button>
          <button type="button" onClick={() => { seek(0); setPlaying(true) }}>{t('showcase.replay')}</button>
          <label>
            {t('showcase.speed')}
            <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
              {SPEEDS.map(value => <option key={value} value={value}>{value}×</option>)}
            </select>
          </label>
          <input
            type="range"
            min="0"
            max="1000"
            value={Math.round((1000 * time) / flow.total)}
            aria-label={t('showcase.seek')}
            onChange={event => { setPlaying(false); seek((flow.total * event.target.value) / 1000) }}
          />
        </div>
      </header>

      <nav className="showcase-phases" aria-label={t('showcase.phases')}>
        {PHASES.map((id, index) => (
          <button
            key={id}
            type="button"
            aria-current={phase === id ? 'step' : undefined}
            onClick={() => { setPlaying(false); seek(flow[id].start) }}
          >
            <span aria-hidden="true">{index + 1}</span>
            {t(PHASE_KEY[id])}
          </button>
        ))}
      </nav>

      {phase === 'compose' ? <ComposePhase flow={flow} local={local} hover={cursor.target} t={t} /> : null}
      {phase === 'run' ? <RunPhase flow={flow} local={local} hover={cursor.target} t={t} /> : null}
      {phase === 'compare' ? <ComparePhase flow={flow} local={local} hover={cursor.target} t={t} /> : null}

      <div ref={cursorRef} className="showcase-cursor" aria-hidden="true" data-testid="showcase-cursor" data-target={cursor.target || ''}>
        <svg width="20" height="24" viewBox="0 0 20 24"><path d="M2 1l15 13.5-6.5.9 3.8 7.6-3.1 1.5-3.8-7.6L2 20z" fill="#2e2618" stroke="#fff" strokeWidth="1.5" strokeLinejoin="round" /></svg>
        {cursor.click ? <span key={cursor.click} className="showcase-ripple" /> : null}
      </div>

      <p className="showcase-note">
        {t('showcase.note')}
        {finished ? <b> {t('showcase.finished', { ranking: summarize(fixture).map(r => `${r.label} ${r.ex}/${fixture.n}`).join(' > ') })}</b> : null}
      </p>
    </section>
  )
}
