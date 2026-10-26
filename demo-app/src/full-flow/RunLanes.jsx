import { describeResult } from './showcaseModel.js'
import './showcase.css'

function Stage({ stage, t }) {
  const meta = stage.state === 'run'
    ? t('showcase.running')
    : stage.state !== 'done' ? ''
      : stage.tokens ? t('showcase.stageMeta', { tokens: stage.tokens, seconds: stage.seconds })
        : stage.seconds != null ? t('showcase.stageSeconds', { seconds: stage.seconds }) : ''
  const text = stage.text || ''
  return (
    <li className={`showcase-stage ${stage.state}`}>
      <div className="showcase-stage-row">
        <span className="showcase-dot" aria-hidden="true" />
        <b>{stage.name}</b>
        <small>{meta}</small>
      </div>
      <pre className={`showcase-out ${stage.kind === 'sql' ? 'sql' : ''}`}>{text.slice(0, Math.ceil(text.length * stage.typed))}</pre>
    </li>
  )
}

function Verdict({ verdict, t }) {
  if (!verdict.ready) return <div className="showcase-verdict wait">{t('showcase.waiting')}</div>
  if (verdict.withheld && verdict.ex !== null && verdict.ex !== undefined) {
    return (
      <div className={`showcase-verdict ${verdict.ex ? 'ok' : 'bad'}`}>
        <b>{verdict.ex ? `✓ ${t('showcase.hit')}` : `✗ ${t('showcase.miss')}`}</b>
        <div>{t('showcase.withheld')}</div>
      </div>
    )
  }
  if (!verdict.pred_sql) return <div className="showcase-verdict bad"><b>✗ {t('showcase.noSql')}</b></div>
  if (verdict.ex === null || verdict.ex === undefined) {
    return (
      <div className="showcase-verdict wait">
        <b>{t('showcase.unjudged')}</b>
        <pre>{verdict.pred_sql}</pre>
        {verdict.result ? <div>{describeResult(verdict.result, t)}</div> : null}
      </div>
    )
  }
  return (
    <div className={`showcase-verdict ${verdict.ex ? 'ok' : 'bad'}`}>
      <b>{verdict.ex ? `✓ ${t('showcase.hit')}` : `✗ ${t('showcase.miss')}`}</b>
      <pre>{verdict.pred_sql}</pre>
      {verdict.result ? <div>{describeResult(verdict.result, t)}</div> : null}
    </div>
  )
}

/** One question and every method's lane for it; the same view for a recording and a live run. */
export default function QuestionLanes({ t, info, lanes }) {
  return (
    <>
      <div className="showcase-question" aria-live="polite">
        <div className="showcase-qhead">
          <span>Q{info.index + 1}/{info.total}</span>
          {info.question ? <strong>{info.question}</strong> : null}
          <code>{info.db}</code>
          {info.hardness ? <code>{info.hardness}</code> : null}
        </div>
        {info.gold_sql ? (
          <>
            <small>{t('showcase.gold')}</small>
            <pre>{info.gold_sql}</pre>
          </>
        ) : null}
      </div>
      <div className="showcase-lanes">
        {lanes.map(lane => (
          <article key={lane.id} className="showcase-lane" data-method={lane.id}>
            <div className="showcase-lane-head"><b>{lane.label}</b><span>EX {lane.ex}/{lane.judged}</span></div>
            <ol>
              {lane.stages.map(stage => <Stage key={`${info.index}-${stage.name}`} stage={stage} t={t} />)}
            </ol>
            <Verdict verdict={lane.verdict} t={t} />
          </article>
        ))}
      </div>
    </>
  )
}
