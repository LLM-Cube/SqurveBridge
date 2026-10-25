const numeric = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const pct = (value) => (numeric(value) === null ? '—' : `${(value * 100).toFixed(1)}%`)
const val = (value) => (typeof value === 'object' && value ? (value.avg ?? value.value) : value)

/** One metric hierarchy shared by live and archived run details. */
export default function RunMetrics({ data = {}, t, samplesOnly = false }) {
  const metrics = data.metrics || data.aggregate || {}
  const process = data.process || data.aggregate?.process || {}
  const cf1 = data.component_f1 || data.aggregate?.cf1 || {}
  const samples = data.sample_evidence || []
  const degraded = numeric(process.refinement_degradation_rate) > 0
  const entry = (name, value, danger = false) => (
    <div key={name}>
      <dt>{name}</dt>
      <dd className={danger ? 'metric-degradation' : ''}>{pct(val(value))}</dd>
    </div>
  )
  return (
    <section className="run-metric-evidence" aria-label={t('layers.title')}>
      {!samplesOnly && (
        <div className="run-layer-strip">
          <section className="run-layer run-layer-one">
            <h4>L1 · {t('layers.correctness')}</h4>
            <span className="metric-protocol">EX · {data.ex_variant || 'custom'}</span>
            <dl>
              {entry('EX', metrics.ex)}
              {entry('SF1', metrics.sf1)}
            </dl>
          </section>
          <section className="run-layer">
            <h4>L2 · {t('layers.efficiency')}</h4>
            <dl>
              {entry('R-VES', metrics.rves)}
              {entry(`VES · ${t('layers.reference')}`, metrics.ves)}
            </dl>
            <details>
              <summary>{t('layers.definition')}</summary>
              <p>{t('layers.rvesDefinition')}</p>
            </details>
          </section>
          <section className="run-layer run-layer-muted">
            <h4>L3 · {t('layers.structure')}</h4>
            <dl>{entry('EM', metrics.em)}</dl>
            <details open>
              <summary>{t('layers.clauses')}</summary>
              <dl>
                {Object.entries(cf1).map(([name, value]) => entry(name.toUpperCase(), value))}
              </dl>
            </details>
          </section>
          <section className="run-layer run-layer-muted">
            <h4>L4 · {t('layers.process')}</h4>
            <dl>
              {entry('CR', process.correct_ratio)}
              {entry('IR', process.incorrect_ratio)}
              {entry('ER', process.error_ratio)}
              {entry('C2I', process.refinement_degradation_rate, degraded)}
            </dl>
            <small>
              {t('layers.coverage')}: {pct(process.outcome_coverage)} · n=
              {process.outcome_observed ?? '—'}
            </small>
            <details>
              <summary>{t('layers.processDetails')}</summary>
              <p>{t('layers.outcomeDefinition')}</p>
              <dl>
                {Object.entries(process)
                  .filter(
                    ([key]) =>
                      ![
                        'correct_ratio',
                        'incorrect_ratio',
                        'error_ratio',
                        'outcome_observed',
                        'outcome_coverage',
                      ].includes(key),
                  )
                  .map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{numeric(value) ?? '—'}</dd>
                    </div>
                  ))}
              </dl>
            </details>
          </section>
        </div>
      )}
      <div className="sample-evidence-wrap">
        <table className="sample-evidence">
          <caption>{t('layers.samples')}</caption>
          <thead>
            <tr>
              {[
                '#',
                t('layers.question'),
                'L1',
                'L2',
                'L3',
                t('layers.executionDiff'),
                t('layers.error'),
                'C2I',
              ].map((name) => (
                <th scope="col" key={name}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {samples.map((row, index) => (
              <tr key={index}>
                <th scope="row">{row.index ?? index + 1}</th>
                <td>{row.question || t('layers.withheld')}</td>
                <td>
                  EX {pct(row.ex)} · SF1 {pct(row.sf1)}
                </td>
                <td>
                  R-VES {pct(row.rves)} · VES {pct(row.ves)}
                </td>
                <td>
                  EM {pct(row.em)}
                  {Object.entries(row.cf1 || {}).map(([key, value]) => (
                    <small key={key}>
                      {key}: {pct(value)}
                    </small>
                  ))}
                </td>
                <td>
                  {row.execution_diff == null
                    ? '—'
                    : t(row.execution_diff ? 'layers.different' : 'layers.same')}
                </td>
                <td>{row.error == null ? '—' : t(row.error ? 'layers.error' : 'layers.none')}</td>
                <td className={degraded && row.c2i === true ? 'metric-degradation' : ''}>
                  {row.c2i == null ? '—' : t(row.c2i ? 'layers.yes' : 'layers.none')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!samples.length && <p>{t('layers.noSamples')}</p>}
      </div>
    </section>
  )
}
