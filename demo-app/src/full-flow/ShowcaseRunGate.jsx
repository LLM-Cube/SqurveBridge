import { useEffect, useMemo, useRef, useState } from 'react'
import record from './showcaseRecord.js'
import { allDone, jobsAt, mockJobs, totalSeconds } from './showcaseRunModel.js'

const LEVELS = ['l1', 'l2', 'l3', 'l4', 'l5', 'l6']
const SETTLE_MS = 600
const TICK_MS = 100

/**
 * What the results area shows before the metrics exist: nothing but the plan and what a run will
 * produce; while jobs run, their progress. The metrics only appear once every job has finished.
 * With `passive`, an external replay drives the run, so no start button is offered here.
 */
export default function ShowcaseRunGate({ t, stage, onStage, passive = false }) {
  const batch = record.batches.at(-1)
  const jobs = useMemo(() => mockJobs(batch), [batch])
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(0)
  const dataset = Object.values(batch.runs)[0].source.dataset

  useEffect(() => {
    if (passive || stage !== 'running') return undefined
    startedAt.current = Date.now()
    setElapsed(0)
    const timer = setInterval(() => {
      const seconds = (Date.now() - startedAt.current) / 1000
      setElapsed(seconds)
      if (seconds * 1000 >= totalSeconds(jobs) * 1000 + SETTLE_MS) { clearInterval(timer); onStage('ready') }
    }, TICK_MS)
    return () => clearInterval(timer)
  }, [stage, passive, jobs, onStage])

  const plan = t('showcase.run.idleBody', { methods: jobs.map(job => job.id).join(', '), benchmark: dataset, count: batch.sample_count })
  const progress = jobsAt(jobs, elapsed)

  return (
    <section className="showcase-run-gate" data-testid="showcase-run-gate" data-stage={stage} aria-label={t('showcase.run.title')}>
      <h2>{t('showcase.run.title')}</h2>
      {stage === 'idle' ? (
        <>
          <div className="showcase-run-empty">
            <strong>{t('showcase.run.idleTitle')}</strong>
            <p>{plan}</p>
            <p className="showcase-run-will">{t('showcase.run.willProduce')}</p>
            <ul aria-label={t('showcase.metrics.levels')}>
              {LEVELS.map(level => <li key={level}>{t(`showcase.metrics.level.${level}`)}</li>)}
            </ul>
          </div>
          {passive
            ? <p className="showcase-run-hint">{t('showcase.run.passiveHint')}</p>
            : <button type="button" className="showcase-run-start" onClick={() => onStage('running')}>{t('showcase.run.start')}</button>}
        </>
      ) : (
        <>
          <p className="showcase-run-hint">{passive ? t('showcase.run.passiveRunning') : (allDone(jobs, elapsed) ? t('showcase.run.generating') : t('showcase.run.runningTitle'))}</p>
          {passive ? null : (
            <ul className="showcase-run-jobs" aria-label={t('run.batchJobs')}>
              {progress.map(job => (
                <li key={job.id} data-method={job.id} className={job.done ? 'done' : 'live'}>
                  <b>{job.id}</b>
                  <span className="showcase-chip">{job.done ? t('plan.status.done') : t('plan.status.running')}</span>
                  <div
                    className="showcase-track" role="progressbar" aria-label={job.id}
                    aria-valuemin="0" aria-valuemax={job.total} aria-valuenow={job.completed}
                  >
                    <div style={{ width: `${job.progress * 100}%` }} />
                  </div>
                  <small>{t('showcase.run.jobProgress', { done: job.completed, total: job.total })}</small>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
