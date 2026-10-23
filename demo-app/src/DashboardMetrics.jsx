import { useState } from 'react'
import RunMetrics from './RunMetrics.jsx'

const valueOf = value => typeof value === 'number' && Number.isFinite(value) ? value : value && typeof value === 'object' ? valueOf(value.avg ?? (value.status === 'available' ? value.value : null)) : null
const display = (value, unit = 'percent') => value == null ? '—' : unit === 'percent' ? `${(value * 100).toFixed(1)}%` : unit === 'seconds' ? `${value.toFixed(2)} s` : Intl.NumberFormat('en').format(value)
const CF1 = ['select', 'where', 'group', 'order', 'join', 'keywords', 'iuen']

/** A stable reading order from scope to outcomes to the evidence behind them. */
export default function DashboardMetrics({ runs = [], alignment, t }) {
  const [selected, setSelected] = useState('')
  const focused = runs.find(run => run.run_id === selected) || runs[0]
  const metric = (id, unit = 'percent', getter) => ({ id, unit, get: getter || (run => valueOf(run.aggregate?.[id])) })
  const groups = [
    { id: 'correctness', number: '01', metrics: [metric('ex'), metric('sf1')] },
    { id: 'efficiency', number: '02', metrics: [metric('rves'), metric('ves'), metric('tokens', 'number', run => run.token?.total_calls > 0 ? valueOf(run.token?.avg_per_sample) : null), metric('latency', 'seconds', run => valueOf(run.latency?.mean_s)), metric('p95', 'seconds', run => valueOf(run.latency?.p95_s))] },
    { id: 'structure', number: '03', metrics: [metric('em'), ...CF1.map(id => metric(`cf1_${id}`, 'percent', run => valueOf(run.aggregate?.cf1?.[`cf1_${id}`])))] },
    { id: 'process', number: '04', metrics: ['correct_ratio', 'incorrect_ratio', 'error_ratio', 'refinement_fix_rate', 'refinement_degradation_rate', 'selection_accuracy', 'linking_recall', 'outcome_coverage'].map(id => metric(id, 'percent', run => valueOf((run.process || run.aggregate?.process)?.[id]))) },
  ]
  return <div className="dashboard-sections">
    <section className="dashboard-scope" aria-labelledby="dashboard-scope-title">
      <header><span className="dashboard-section-number">00</span><div><h2 id="dashboard-scope-title">{t('dashboard.scope')}</h2><p>{t('dashboard.scopeHint')}</p></div></header>
      <dl className="dashboard-scope-facts"><div><dt>{t('dashboard.runs')}</dt><dd>{runs.length}</dd></div><div><dt>{t('dashboard.datasets')}</dt><dd>{[...new Set(runs.map(run => run.dataset))].join(' · ') || '—'}</dd></div><div><dt>Split</dt><dd>{[...new Set(runs.map(run => run.split))].join(' · ') || '—'}</dd></div><div><dt>{t('dashboard.alignment')}</dt><dd>{t(alignment?.aligned ? 'dashboard.aligned' : 'dashboard.notAligned')}</dd></div></dl>
      <div className="dashboard-run-identities">{runs.map(run => <details key={run.run_id}><summary>{run.method} · n={run.sample_count ?? '—'} · EX {run.ex_variant || 'custom'}</summary><dl><div><dt>Run ID</dt><dd>{run.run_id}</dd></div><div><dt>{t('dashboard.fingerprint')}</dt><dd>{run.sample_hash || '—'}</dd></div><div><dt>{t('dashboard.sampling')}</dt><dd>{run.sampling?.mode || '—'} · {run.sampling?.limit ?? '—'} · seed {run.sampling?.seed ?? '—'}</dd></div><div><dt>{t('dashboard.source')}</dt><dd>{run.source || '—'}</dd></div></dl></details>)}</div>
    </section>
    {groups.map(group => <section className={`dashboard-metric-section dashboard-${group.id}`} key={group.id} aria-labelledby={`dashboard-${group.id}`}>
      <header><span className="dashboard-section-number">{group.number}</span><div><h2 id={`dashboard-${group.id}`}>{t(`dashboard.${group.id}`)}</h2><p>{t(`dashboard.${group.id}Hint`)}</p></div></header>
      <div className="dashboard-table-wrap"><table><caption>{t(`dashboard.${group.id}`)}</caption><thead><tr><th scope="col">{t('compare.member')}</th>{group.metrics.map(item => <th scope="col" key={item.id}><span>{t(`dashboard.metric.${item.id}`)}</span><small>{t(`dashboard.meaning.${item.id}`)}</small></th>)}<th scope="col">n</th></tr></thead><tbody>{runs.map(run => <tr key={run.run_id}><th scope="row">{run.method}{group.id === 'correctness' && <small>EX · {run.ex_variant || 'custom'}</small>}</th>{group.metrics.map(item => {
        const value = item.get(run)
        const metadata = run.aggregate?.[item.id]
        return <td key={item.id} className={item.id === 'refinement_degradation_rate' && value > 0 ? 'metric-degradation' : ''}><strong>{display(value, item.unit)}</strong><small>{value == null ? metadata?.note || metadata?.reason || t('dashboard.unavailable') : metadata?.valid != null ? `${metadata.valid}/${metadata.total ?? run.sample_count} ${t('dashboard.observed')}` : t('dashboard.recorded')}</small></td>
      })}<td>{run.sample_count ?? '—'}</td></tr>)}</tbody></table></div>
      {group.id === 'process' && <div className="dashboard-error-roots">{runs.map(run => <details key={`process-${run.run_id}`}><summary>{run.method} · {t('layers.processDetails')}</summary><dl>{Object.entries(run.process || run.aggregate?.process || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{valueOf(value) ?? '—'}</dd></div>)}</dl></details>)}{runs.map(run => <details key={run.run_id}><summary>{run.method} · {t('dashboard.errors')}</summary><dl>{Object.entries(run.errors || {}).map(([name, item]) => <div key={name}><dt>{name}</dt><dd>{item.count ?? '—'} · {display(valueOf(item.pct))}</dd></div>)}</dl>{!Object.keys(run.errors || {}).length && <p>{t('dashboard.unavailable')}</p>}</details>)}</div>}
    </section>)}
    <section className="dashboard-samples" aria-labelledby="dashboard-samples-title"><header><span className="dashboard-section-number">05</span><div><h2 id="dashboard-samples-title">{t('layers.samples')}</h2><p>{t('dashboard.samplesHint')}</p></div></header><label>{t('compare.member')} <select value={focused?.run_id || ''} onChange={event => setSelected(event.target.value)}>{runs.map(run => <option key={run.run_id} value={run.run_id}>{run.method} · {run.run_id}</option>)}</select></label>{focused && <RunMetrics data={focused} t={t} samplesOnly />}</section>
  </div>
}
