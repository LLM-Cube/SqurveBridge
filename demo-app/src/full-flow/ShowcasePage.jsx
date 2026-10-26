import { lazy, Suspense, useCallback, useState } from 'react'
import ShowcaseMetrics from './ShowcaseMetrics.jsx'
import ShowcaseRunGate from './ShowcaseRunGate.jsx'
import record from './showcaseRecord.js'

// The question-by-question replay is loaded lazily and optionally, so the page still works
// (results only, behind the run gate) if that file is ever absent.
const replayModules = (() => {
  try { return import.meta.glob('./ShowcaseReplay.jsx') } catch { return {} }
})()
const loadReplay = replayModules['./ShowcaseReplay.jsx']
const Replay = loadReplay ? lazy(loadReplay) : null

// A link to a metrics level (or ?showcaseRun=done) shows the finished results straight away.
const linkedToResults = () => {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  return params.has('metricsLevel') || params.get('showcaseRun') === 'done'
}

/** Results exist only after a run: idle -> running -> ready, driven by the replay when there is one. */
export default function ShowcasePage({ t, active }) {
  const forced = linkedToResults()
  const [stage, setStage] = useState(forced ? 'ready' : 'idle')
  const follow = useCallback(next => { if (!forced) setStage(next) }, [forced])
  const batch = record.batches.at(-1)
  const valid = batch.ranking.length

  return (
    <div className="showcase-page" data-testid="showcase-page">
      {Replay ? <Suspense fallback={null}><Replay t={t} active={active} onStage={follow} /></Suspense> : null}
      {stage === 'ready' ? (
        <div className="showcase-reveal">
          <p className="showcase-generated" role="status">
            {t('showcase.run.generated', { count: Object.keys(batch.runs).length, valid })}
            {Replay ? null : <button type="button" onClick={() => setStage('idle')}>{t('showcase.run.reset')}</button>}
          </p>
          <ShowcaseMetrics t={t} />
        </div>
      ) : <ShowcaseRunGate t={t} stage={stage} onStage={setStage} passive={Boolean(Replay)} />}
    </div>
  )
}
