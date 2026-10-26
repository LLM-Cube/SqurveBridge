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
export default function LiveRun({ t, api, experimentId, pollMs = 1500, labelFor = id => id }) {
  const [cells, setCells] = useState({})
  const [status, setStatus] = useState('queued')
  const [failed, setFailed] = useState(false)
  const cursor = useRef(0)

  useEffect(() => {
    let stopped = false
    let timer
    cursor.current = 0
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
        if (!ACTIVE.has(response.status)) return
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

  const seen = timelineCells.some(cell => cell.questions.length)
  if (!seen) {
    return <p className="showcase-empty">{failed ? t('run.liveUnavailable') : t('run.liveWaiting')}</p>
  }
  const index = focusOf(timelineCells)
  const question = timelineCells.flatMap(cell => cell.questions).find(item => item.index === index)
  const total = Math.max(...timelineCells.map(cell => cell.total || 0))
  const board = scoreboard(timelineCells)
  const ended = ['cancelled', 'failed'].includes(status) ? t(`run.live.${status}`) : ''
  return (
    <section className="showcase-run showcase-live" aria-label={t('run.liveTitle')} data-status={status}>
      <ul className="showcase-scoreboard" aria-label={t('run.liveScore')}>
        {timelineCells.map(cell => {
          const row = board[cell.method]
          return (
            <li key={cell.cell_id}>
              <b>{cell.label}</b> EX {row.ex}/{row.judged} · {row.done}/{row.total} · {row.tokens} tok · {row.seconds.toFixed(1)}s
            </li>
          )
        })}
      </ul>
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
