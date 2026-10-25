import { useEffect, useState } from 'react'

/** Past experiments, newest first: open one to compare it, or use it as the template of a new plan. */
export default function ExperimentHistory({ api, currentExperimentId = '', refreshKey, onOpenExperiment, onUseTemplate, t }) {
  const [experiments, setExperiments] = useState(null)

  useEffect(() => {
    if (!api) return undefined
    let active = true
    api('/api/experiments')
      .then(data => { if (active) setExperiments(Array.isArray(data?.experiments) ? data.experiments : []) })
      .catch(() => { if (active) setExperiments([]) })
    return () => { active = false }
  }, [api, currentExperimentId, refreshKey])

  if (experiments === null) return null
  if (!experiments.length) return <p className="experiment-history-empty">{t('history.empty')}</p>

  return (
    <section className="experiment-history" aria-label={t('history.title')}>
      <h4>{t('history.title')}</h4>
      <p>{t('plan.historyRetention')}</p>
      <ul>
        {experiments.map(experiment => (
          <li key={experiment.experiment_id}>
            <span aria-current={experiment.experiment_id === currentExperimentId ? 'true' : undefined}>
              <b>{experiment.experiment_id}</b>
              {' '}
              <small>{experiment.status}</small>
            </span>
            <small>
              <time dateTime={new Date(Number(experiment.created_at || 0) * 1000).toISOString()}>{new Date(Number(experiment.created_at || 0) * 1000).toLocaleString()}</time>
              {' · '}
              {experiment.sampling?.mode} {experiment.sampling?.limit}
              {experiment.sampling?.mode === 'random' ? ` · seed ${experiment.sampling.seed}` : ''}
              {' · '}
              {t('history.cells', { count: experiment.cells?.length || 0 })}
            </small>
            <span className="experiment-history-actions">
              <button type="button" onClick={() => onOpenExperiment?.(experiment.experiment_id)}>{t('history.open')}</button>
              <button type="button" onClick={() => onUseTemplate?.(experiment)}>{t('history.useTemplate')}</button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
