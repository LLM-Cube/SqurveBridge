import { useEffect, useMemo, useRef, useState } from 'react'
import QuestionLanes from './RunLanes.jsx'
import { focusOf, lanesFromTimeline, scoreboard } from './runLanes.js'

const ACTIVE = new Set(['queued', 'running'])

// Keeps every question seen so far; a response only repeats the ones that may still change.
function merge(previous, response) {
  const cells = { ...previous }
  for (const cell of response.cells || []) {
    const before = cells[cell.cell_id]?.byIndex || {}
    const byIndex = { ...before }
    for (const item of cell.questions || []) byIndex[item.index] = item
    cells[cell.cell_id] = {
      cell_id: cell.cell_id, method: cell.method, dataset: cell.dataset, total: cell.total, status: cell.status, byIndex,
    }
  }
  return cells
}

/** The run as it happens: the same lanes as the recorded replay, fed by the live timeline. */
export default function LiveRun({ t, api, experimentId, pollMs = 1500, replayMs = 1500, labelFor = id => id, onFinished }) {
  const [cells, setCells] = useState({})
  const [status, setStatus] = useState('queued')
  const [failed, setFailed] = useState(false)
  const [picked, setPicked] = useState(null)
  const [replaying, setReplaying] = useState(false)
  const cursor = useRef(0)
  const wasActive = useRef(false)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    let stopped = false
    let timer
    cursor.current = 0
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

  const seen = timelineCells.some(cell => cell.questions.length)
  if (!seen) {
    return <p className="showcase-empty">{failed ? t('run.liveUnavailable') : t('run.liveWaiting')}</p>
  }
  const index = over && picked !== null ? Math.min(picked, Math.max(0, total - 1)) : focusOf(timelineCells)
  const question = timelineCells.flatMap(cell => cell.questions).find(item => item.index === index)
  const board = scoreboard(timelineCells)
  const ended = ['cancelled', 'failed'].includes(status) ? t(`run.live.${status}`) : ''
  return (
    <section className="showcase showcase-run showcase-live" aria-label={t('run.liveTitle')} data-status={status}>
      <ul className="showcase-jobs" aria-label={t('run.liveScore')}>
        {timelineCells.map(cell => {
          const row = board[cell.method]
          const over = !ACTIVE.has(cell.status)
          return (
            <li key={cell.cell_id} data-method={cell.method} className={over ? 'done' : 'live'}>
              <b>{cell.label} → {cell.dataset}</b>
              <span className="showcase-chip">{t(`run.live.status.${cell.status}`)}</span>
              <div className="showcase-track"><div style={{ width: `${row.total ? (100 * row.done) / row.total : 0}%` }} /></div>
              <small>{row.done}/{row.total}</small>
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
        info={{ index, total, question: question?.question, db: question?.db, hardness: null, gold_sql: question?.gold_sql }}
        lanes={lanesFromTimeline(timelineCells, index)}
      />
    </section>
  )
}
