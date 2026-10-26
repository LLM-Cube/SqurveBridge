import { useEffect, useMemo, useRef, useState } from 'react'
import QuestionLanes from './RunLanes.jsx'
import { focusOf, lanesFromTimeline, scoreboard } from './runLanes.js'

const ACTIVE = new Set(['queued', 'running'])
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
const FINISHED_STAGE = new Set(['done', 'failed'])
const stageKey = (cellId, index, name) => `${cellId}:${index}:${name}`

// When each finished stage was first seen while the run was going; it is typed out from then.
function noteFinishedStages(seenAt, response, now) {
  const watching = ACTIVE.has(response.status)
  for (const cell of response.cells || []) {
    for (const item of cell.questions || []) {
      for (const stage of item.run?.stages || []) {
        const key = stageKey(cell.cell_id, item.index, stage.name)
        if (FINISHED_STAGE.has(stage.state) && !seenAt.has(key)) seenAt.set(key, watching && !cell.corrected ? now : 0)
      }
    }
  }
}

function currentStage(cell) {
  for (const item of cell.questions) {
    const stage = item.run?.stages?.find(entry => entry.state === 'running')
    if (stage) return stage.name
  }
  return ''
}

// Keeps every question seen so far; a response only repeats the ones that may still change.
function merge(previous, response) {
  const cells = { ...previous }
  for (const cell of response.cells || []) {
    const before = cells[cell.cell_id]?.byIndex || {}
    const byIndex = { ...before }
    // A corrected cell repeats every question; the evaluation's version replaces the live one.
    for (const item of cell.questions || []) byIndex[item.index] = item
    cells[cell.cell_id] = {
      cell_id: cell.cell_id, method: cell.method, dataset: cell.dataset, total: cell.total, status: cell.status,
      corrected: Boolean(cell.corrected), byIndex,
    }
  }
  return cells
}

/** The run as it happens: the same lanes as the recorded replay, fed by the live timeline. */
export default function LiveRun({
  t, api, experimentId, pollMs = 1500, replayMs = 1500, typeMs = 800, labelFor = id => id, onFinished, onCancelCell,
}) {
  const [cells, setCells] = useState({})
  const [status, setStatus] = useState('queued')
  const [failed, setFailed] = useState(false)
  const [picked, setPicked] = useState(null)
  const [replaying, setReplaying] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const cursor = useRef(0)
  const seenAt = useRef(new Map())
  const typing = reducedMotion() ? 0 : typeMs
  const wasActive = useRef(false)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    let stopped = false
    let timer
    cursor.current = 0
    seenAt.current = new Map()
    wasActive.current = false
    setPicked(null)
    setReplaying(false)
    setCells({})
    setFailed(false)
    const poll = async () => {
      try {
        const response = await api(`/api/experiments/${experimentId}/timeline?since=${cursor.current}`)
        if (stopped) return
        cursor.current = response.cursor ?? cursor.current
        noteFinishedStages(seenAt.current, response, Date.now())
        setCells(previous => merge(previous, response))
        setStatus(response.status)
        setFailed(false)
        if (ACTIVE.has(response.status)) wasActive.current = true
        else {
          // Only a run watched while it was going switches the view; reopening an old one does not.
          if (wasActive.current && response.status === 'completed') finished.current?.(response.status)
          wasActive.current = false
          return
        }
      } catch {
        if (stopped) return
        setFailed(true)
      }
      timer = setTimeout(poll, pollMs)
    }
    poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [api, experimentId, pollMs])

  const timelineCells = useMemo(() => Object.values(cells).map(cell => ({
    ...cell,
    label: labelFor(cell.method),
    questions: Object.values(cell.byIndex).sort((a, b) => a.index - b.index),
  })), [cells, labelFor])

  const total = Math.max(0, ...timelineCells.map(cell => cell.total || 0))
  const over = !ACTIVE.has(status)
  useEffect(() => {
    if (!replaying) return undefined
    const timer = setInterval(() => setPicked(current => {
      if (current === null || current + 1 >= total) { setReplaying(false); return current === null ? total - 1 : current }
      return current + 1
    }), replayMs)
    return () => clearInterval(timer)
  }, [replaying, total, replayMs])

  // Ticks only while some stage is still being typed out.
  const stillTyping = typing > 0 && [...seenAt.current.values()].some(at => at && now - at < typing)
  useEffect(() => {
    if (!stillTyping) return undefined
    const timer = setTimeout(() => setNow(Date.now()), 40)
    return () => clearTimeout(timer)
  }, [stillTyping, now])
  // A response with new finished stages restarts the clock.
  useEffect(() => { setNow(Date.now()) }, [cells])

  const seen = timelineCells.some(cell => cell.questions.length)
  if (!seen) {
    return <p className="showcase-empty">{failed ? t('run.liveUnavailable') : t('run.liveWaiting')}</p>
  }
  const index = over && picked !== null ? Math.min(picked, Math.max(0, total - 1)) : focusOf(timelineCells)
  const question = timelineCells.flatMap(cell => cell.questions).find(item => item.index === index)
  const board = scoreboard(timelineCells)
  const ended = ['cancelled', 'failed'].includes(status) ? t(`run.live.${status}`) : ''
  const lanes = lanesFromTimeline(timelineCells, index).map((lane, i) => ({
    ...lane,
    stages: lane.stages.map(stage => {
      const at = seenAt.current.get(stageKey(timelineCells[i].cell_id, index, stage.name))
      if (!at || !typing || stage.typed < 1 || (over && picked !== null)) return stage
      return { ...stage, typed: Math.min(1, (now - at) / typing) }
    }),
  }))
  const allCorrected = timelineCells.every(cell => cell.corrected)
  return (
    <section className="showcase showcase-run showcase-live" aria-label={t('run.liveTitle')} data-status={status}>
      <ul className="showcase-jobs" aria-label={t('run.liveScore')}>
        {timelineCells.map(cell => {
          const row = board[cell.method]
          const over = !ACTIVE.has(cell.status)
          const stage = over ? '' : currentStage(cell)
          return (
            <li key={cell.cell_id} data-method={cell.method} className={over ? 'done' : 'live'}>
              <b>{cell.label} → {cell.dataset}</b>
              <span className="showcase-chip">{t(`run.live.status.${cell.status}`)}</span>
              <div className="showcase-track"><div style={{ width: `${row.total ? (100 * row.done) / row.total : 0}%` }} /></div>
              <small>{row.done}/{row.total}{stage ? ` · ${stage}` : ''}</small>
              {!over && onCancelCell ? (
                <button
                  type="button" className="showcase-cancel" onClick={() => onCancelCell(cell.cell_id)}
                  aria-label={t('run.liveCancelCell', { name: cell.label, dataset: cell.dataset })}
                >
                  {t('plan.cancelCellAction')}
                </button>
              ) : null}
              <small className="showcase-jobmeta">
                EX {row.ex}/{row.judged} · {row.tokens === null ? t('run.liveNoTokens') : `${row.tokens} tok`} · {row.seconds === null ? '—' : `${row.seconds.toFixed(1)}s`}
              </small>
            </li>
          )
        })}
      </ul>
      {over && total > 1 ? (
        <div className="showcase-scrub">
          <button type="button" onClick={() => { setPicked(0); setReplaying(true) }}>{t('run.liveReplay')}</button>
          <input
            type="range" min={1} max={total} value={index + 1} aria-label={t('run.liveScrub')}
            onChange={event => { setReplaying(false); setPicked(Number(event.target.value) - 1) }}
          />
        </div>
      ) : null}
      {ended ? <p className="showcase-empty" role="status">{ended}</p> : null}
      {failed ? <p className="showcase-empty">{t('run.liveUnavailable')}</p> : null}
      <QuestionLanes
        t={t}
        info={{ index, total, question: question?.question, db: question?.db, hardness: question?.hardness, gold_sql: question?.gold_sql }}
        lanes={lanes}
      />
      <p className="showcase-note" role="note">{t(allCorrected ? 'run.liveNoteCorrected' : 'run.liveNote')}</p>
    </section>
  )
}
