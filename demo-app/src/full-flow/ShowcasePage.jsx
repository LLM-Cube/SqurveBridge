import { lazy, Suspense } from 'react'
import ShowcaseMetrics from './ShowcaseMetrics.jsx'

// The question-by-question replay embeds benchmark questions and SQL, which are not published;
// it is a local-only asset, so the page works whether or not that file exists.
const replayModules = (() => {
  try { return import.meta.glob('./ShowcaseReplay.jsx') } catch { return {} }
})()
const loadReplay = replayModules['./ShowcaseReplay.jsx']
const Replay = loadReplay ? lazy(loadReplay) : null

export default function ShowcasePage({ t, active }) {
  return (
    <div className="showcase-page" data-testid="showcase-page">
      {Replay ? <Suspense fallback={null}><Replay t={t} active={active} /></Suspense> : null}
      <ShowcaseMetrics t={t} />
    </div>
  )
}
