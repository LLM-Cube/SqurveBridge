import { useMemo, useState } from 'react'
import { Legend, METHOD_ORDER, CostScatter, DeltaDots, ErrorPanels, GroupedColumns, Heatmap, LatencyRange, OutcomeBars, PairBars, StagePipelines } from './ShowcaseCharts.jsx'
import record from './showcaseRecord.js'
import { useStableHeight } from './useStableHeight.js'
import './showcaseMetrics.css'

const pct = value => (value === null || value === undefined ? null : `${(value * 100).toFixed(1)}%`)
const num = (value, digits = 0) => (value === null || value === undefined ? null : Number(value).toFixed(digits))
const names = runs => [...new Set(runs.flatMap(([, run]) => Object.keys(run)))]

/** A recorded metric as text: its value, or an explicit "cannot be computed" with the reason. */
function MetricCell({ metric, t, digits = 3 }) {
  if (!metric || metric.status === 'unavailable' || metric.value === null || metric.value === undefined) {
    return <td className="na" title={metric?.reason || ''}>{t('showcase.metrics.unavailable')}</td>
  }
  const value = Number(metric.value)
  return <td>{Number.isInteger(value) && Math.abs(value) > 1 ? value : value.toFixed(digits)}</td>
}

function Plain({ text }) {
  return text === null || text === undefined ? <td className="na">—</td> : <td>{text}</td>
}

/** A metric group: its chart first, the exact numbers as a table view right below it. */
function Section({ title, hint, chart, tableView, children, className = '' }) {
  return (
    <section className={`showcase-metrics-section ${className}`.trim()}>
      <h3>{title}</h3>
      {hint ? <p>{hint}</p> : null}
      {chart}
      {chart && children ? (
        <details className="showcase-metrics-tableview">
          <summary>{tableView}</summary>
          <div className="showcase-metrics-scroll">{children}</div>
        </details>
      ) : null}
      {!chart && children ? <div className="showcase-metrics-scroll">{children}</div> : null}
    </section>
  )
}

/** Rows are the names found in any run, columns are the methods; one table per metric group. */
function GroupTable({ label, runs, pick, cell }) {
  const rows = names(runs.map(([id, run]) => [id, pick(run) || {}]))
  if (!rows.length) return null
  return (
    <table className="showcase-metrics-table" aria-label={label}>
      <thead>
        <tr><th /> {runs.map(([id]) => <th key={id} scope="col">{id}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map(row => (
          <tr key={row}>
            <th scope="row">{row}</th>
            {runs.map(([id, run]) => cell((pick(run) || {})[row], `${id}/${row}`))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const LEVEL_IDS = ['l1', 'l2', 'l3', 'l4', 'l5', 'l6']
// ?metricsLevel=l3 opens that level (a shareable link to one group of metrics).
const startLevel = () => {
  const raw = typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('metricsLevel')
  return LEVEL_IDS.includes(raw) ? raw : 'l1'
}

export default function ShowcaseMetrics({ t, data = record }) {
  const [batchId, setBatchId] = useState(() => data.batches.at(-1).id)
  const [level, setLevel] = useState(startLevel)
  const [panelsRef, panelsStyle] = useStableHeight()
  const batch = data.batches.find(item => item.id === batchId) || data.batches[0]
  const runs = useMemo(() => Object.entries(batch.runs), [batch])
  const valid = new Set(batch.ranking)
  const count = batch.sample_count
  const rule = Math.round(batch.validity_rule.max_degenerate_rate * 100)
  const matrixIds = runs.map(([id]) => id)

  const methods = runs.map(([id]) => ({ id, valid: valid.has(id) }))
  const chartOf = title => t('showcase.metrics.chartOf', { title })
  const union = pick => [...new Set(runs.flatMap(([, run]) => Object.keys(pick(run) || {})))]

  const qualityGroups = [['ex', 'EX'], ['em', 'EM'], ['sf1', 'SF1'], ['ves', 'VES'], ['rves', 'R-VES']].map(([key, label]) => ({
    key, label, labelled: key === 'ex',
    bars: Object.fromEntries(runs.map(([id, run]) => {
      const metric = run.quality[key]
      return [id, metric.status === 'ok' ? {
        value: metric.value, low: metric.interval?.[0], high: metric.interval?.[1],
        text: metric.numerator != null ? `${metric.numerator}/${metric.denominator}` : undefined,
      } : null]
    })),
  }))
  const levels = ['easy', 'medium', 'hard', 'extra'].filter(level => runs.some(([, run]) => run.by_hardness[level]?.count > 0))
  const hardnessGroups = levels.map(level => ({
    key: level, label: level, sub: `n=${runs[0][1].by_hardness[level].count}`,
    bars: Object.fromEntries(runs.map(([id, run]) => [id, run.by_hardness[level]?.ex == null ? null : { value: run.by_hardness[level].ex }])),
  }))
  const exHeat = pick => union(pick).filter(key => runs.some(([, run]) => pick(run)[key]?.count > 0)).map(key => ({
    key, label: key,
    cells: Object.fromEntries(runs.map(([id, run]) => {
      const row = pick(run)[key]
      return [id, row?.count > 0 && row.ex != null ? { value: row.ex, text: `${Math.round(row.ex * 100)}%`, tip: `EX ${pct(row.ex)} · n=${row.count}` } : null]
    })),
  }))
  const componentRows = union(run => run.component_f1).map(key => ({
    key, label: key,
    cells: Object.fromEntries(runs.map(([id, run]) => {
      const value = run.component_f1[key]
      return [id, value == null ? null : { value, text: value.toFixed(2), tip: `F1 ${value.toFixed(3)}` }]
    })),
  }))
  const errorPanels = runs.map(([id, run]) => ({
    id, rows: Object.entries(run.error_roots).map(([name, row]) => ({ name, ...row })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)).slice(0, 6),
  }))
  const scatter = runs.filter(([, run]) => run.quality.ex.status === 'ok' && run.cost.total_tokens != null)
    .map(([id, run]) => ({ id, x: run.cost.total_tokens, y: run.quality.ex.value }))
  const latency = runs.filter(([, run]) => run.cost.latency.p50_s != null && run.cost.latency.p95_s != null)
    .map(([id, run]) => ({ id, p50: run.cost.latency.p50_s, p95: run.cost.latency.p95_s }))
  const tableView = t('showcase.metrics.tableView')

  // Structure delta: sort by how far a method strays, and fold the features nobody gets wrong into one note.
  const deltaRows = union(run => run.structure_delta).map(key => ({
    key, label: key,
    cells: Object.fromEntries(runs.map(([id, run]) => [id, run.structure_delta[key] ?? null])),
  }))
  const strays = row => Math.max(...Object.values(row.cells).map(cell => (cell ? Math.abs(cell.mean) : 0)))
  const flat = deltaRows.filter(row => strays(row) < 0.005 && Object.values(row.cells).every(cell => !cell || cell.std < 0.005))
  const deltaAll = deltaRows.filter(row => !flat.includes(row)).sort((a, b) => strays(b) - strays(a))
  const DELTA_SHOWN = 8
  const deltaShown = deltaAll.slice(0, DELTA_SHOWN)
  const deltaRest = deltaAll.slice(DELTA_SHOWN)
  const stagePanels = runs.map(([id, run]) => ({
    id, stages: Object.entries(run.stages).flatMap(([stage, item]) => Object.entries(item.metrics)
      .filter(([, metric]) => metric.status === 'ok').map(([metric, value]) => ({ name: `${stage} · ${metric}`, value: value.value }))),
  }))
  const processValue = (run, key) => (run.process[key]?.status === 'ok' ? run.process[key].value : null)
  const outcomeRows = runs.filter(([, run]) => processValue(run, 'correct_ratio') !== null).map(([id, run]) => ({
    id, correct: processValue(run, 'correct_ratio'), wrong: processValue(run, 'incorrect_ratio') ?? 0, error: processValue(run, 'error_ratio') ?? 0,
  }))
  const generationGroups = [['generation_pass1', 'pass@1'], ['generation_oracle_k', 'Oracle@k'], ['generation_exec_validity', t('showcase.metrics.execValidity')]].map(([key, text]) => ({
    key, label: text, labelled: true,
    bars: Object.fromEntries(runs.map(([id, run]) => [id, processValue(run, key) === null ? null : { value: processValue(run, key) }])),
  }))
  const unobserved = union(run => run.process).filter(key => runs.every(([, run]) => run.process[key].status !== 'ok'))

  const rowOf = (row, key) => <Plain key={key} text={row ? `${pct(row.ex) ?? '—'} · ${row.count}` : null} />

  const s_quality = (
      <Section key="quality" title={t('showcase.metrics.quality')} hint={t('showcase.metrics.qualityHint', { count })} tableView={tableView}
        chart={<GroupedColumns label={chartOf(t('showcase.metrics.quality'))} groups={qualityGroups} methods={methods} t={t} tipLabel={t('showcase.metrics.whiskerNote')} />}>
        <GroupTable label={t('showcase.metrics.quality')} runs={runs} pick={run => run.quality}
          cell={(metric, key) => <MetricCell key={key} metric={metric} t={t} />} />
      </Section>
  )
  const s_cost = (
      <Section key="cost" className="wide" title={t('showcase.metrics.cost')} tableView={tableView}
        chart={(
          <div className="showcase-chart-pair">
            <CostScatter label={chartOf(t('showcase.metrics.costQuality'))} points={scatter} methods={methods} xLabel={t('showcase.metrics.cost.totalTokens')} yLabel="EX" t={t} />
            <LatencyRange label={chartOf(t('showcase.metrics.cost.latencyAvg'))} rows={latency} methods={methods} t={t} />
          </div>
        )}>
        <table className="showcase-metrics-table" aria-label={t('showcase.metrics.cost')}>
          <thead><tr><th /> {runs.map(([id]) => <th key={id} scope="col">{id}</th>)}</tr></thead>
          <tbody>
            {[
              ['calls', run => num(run.cost.total_calls)],
              ['promptTokens', run => num(run.cost.prompt_tokens)],
              ['completionTokens', run => num(run.cost.completion_tokens)],
              ['totalTokens', run => num(run.cost.total_tokens)],
              ['tokensPerSample', run => num(run.cost.avg_tokens_per_sample, 1)],
              ['tokenSpread', run => (run.cost.tokens_per_sample.samples ? `${run.cost.tokens_per_sample.min} / ${run.cost.tokens_per_sample.median} / ${run.cost.tokens_per_sample.max}` : null)],
              ['latencyAvg', run => num(run.cost.latency.avg_s, 1)],
              ['latencyP50', run => num(run.cost.latency.p50_s, 1)],
              ['latencyP95', run => num(run.cost.latency.p95_s, 1)],
              ['wall', run => num(run.source.wall_seconds, 1)],
            ].map(([key, read]) => (
              <tr key={key}><th scope="row">{t(`showcase.metrics.cost.${key}`)}</th>{runs.map(([id, run]) => <Plain key={id} text={read(run)} />)}</tr>
            ))}
          </tbody>
        </table>
      </Section>
  )
  const s_byHardness = (
      <Section key="byHardness" title={t('showcase.metrics.byHardness')} tableView={tableView}
        chart={<GroupedColumns label={chartOf(t('showcase.metrics.byHardness'))} groups={hardnessGroups} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.byHardness')} runs={runs} pick={run => run.by_hardness} cell={rowOf} />
      </Section>
  )
  const s_byDb = (
      <Section key="byDb" title={t('showcase.metrics.byDb')}>
        <GroupTable label={t('showcase.metrics.byDb')} runs={runs} pick={run => run.by_db_type} cell={rowOf} />
      </Section>
  )
  const s_byFeature = (
      <Section key="byFeature" title={t('showcase.metrics.byFeature')} hint={t('showcase.metrics.cellHint')} tableView={tableView}
        chart={<Heatmap label={chartOf(t('showcase.metrics.byFeature'))} rows={exHeat(run => run.by_sql_feature)} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.byFeature')} runs={runs} pick={run => run.by_sql_feature} cell={rowOf} />
      </Section>
  )
  const s_byScenario = (
      <Section key="byScenario" title={t('showcase.metrics.byScenario')} hint={t('showcase.metrics.cellHint')} tableView={tableView}
        chart={<Heatmap label={chartOf(t('showcase.metrics.byScenario'))} rows={exHeat(run => run.by_scenario)} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.byScenario')} runs={runs} pick={run => run.by_scenario} cell={rowOf} />
      </Section>
  )
  const s_errors = (
      <Section key="errors" title={t('showcase.metrics.errors')} tableView={tableView}
        chart={<ErrorPanels label={chartOf(t('showcase.metrics.errors'))} panels={errorPanels} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.errors')} runs={runs} pick={run => run.error_roots}
          cell={(row, key) => <Plain key={key} text={row ? `${row.count} · ${pct(row.pct)}` : null} />} />
      </Section>
  )
  const s_components = (
      <Section key="components" title={t('showcase.metrics.components')} tableView={tableView}
        chart={<Heatmap label={chartOf(t('showcase.metrics.components'))} rows={componentRows} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.components')} runs={runs} pick={run => run.component_f1}
          cell={(value, key) => <Plain key={key} text={num(value, 3)} />} />
      </Section>
  )
  const s_delta = (
      <Section key="delta" className="tall" title={t('showcase.metrics.delta')} hint={t('showcase.metrics.deltaHint')} tableView={tableView}
        chart={(
          <>
            {deltaShown.length ? <DeltaDots label={chartOf(t('showcase.metrics.delta'))} rows={deltaShown} methods={methods} t={t} /> : null}
            {deltaRest.length ? <p className="showcase-chart-note">{t('showcase.metrics.deltaRest', { count: deltaRest.length, names: deltaRest.map(row => row.label).join(', ') })}</p> : null}
            {flat.length ? <p className="showcase-chart-note">{t('showcase.metrics.noDiff', { count: flat.length, names: flat.map(row => row.label).join(', ') })}</p> : null}
          </>
        )}>
        <GroupTable label={t('showcase.metrics.delta')} runs={runs} pick={run => run.structure_delta}
          cell={(row, key) => <Plain key={key} text={row ? `${num(row.mean, 2)} ± ${num(row.std, 2)}` : null} />} />
      </Section>
  )
  const s_stages = (
      <Section key="stages" title={t('showcase.metrics.stages')} hint={t('showcase.metrics.stagesHint')} tableView={tableView}
        chart={<StagePipelines label={chartOf(t('showcase.metrics.stages'))} panels={stagePanels} methods={methods} t={t} />}>
        <GroupTable label={t('showcase.metrics.stages')} runs={runs}
          pick={run => Object.fromEntries(Object.entries(run.stages).flatMap(([stage, item]) =>
            Object.entries(item.metrics).map(([metric, value]) => [`${stage} · ${metric}`, value])))}
          cell={(metric, key) => <MetricCell key={key} metric={metric} t={t} />} />
      </Section>
  )
  const s_process = (
      <Section key="process" title={t('showcase.metrics.process')} hint={t('showcase.metrics.processHint')} tableView={tableView}
        chart={(
          <>
            <GroupedColumns label={chartOf(t('showcase.metrics.generation'))} groups={generationGroups} methods={methods} t={t} />
            {unobserved.length ? <p className="showcase-chart-note" data-testid="unobserved">{t('showcase.metrics.unobserved', { count: unobserved.length, names: unobserved.join(', ') })}</p> : null}
          </>
        )}>
        <GroupTable label={t('showcase.metrics.process')} runs={runs} pick={run => run.process}
          cell={(metric, key) => <MetricCell key={key} metric={metric} t={t} />} />
      </Section>
  )
  const s_bottlenecks = (
      <Section key="bottlenecks" title={t('showcase.metrics.bottlenecks')}>
        <GroupTable label={t('showcase.metrics.bottlenecks')} runs={runs} pick={run => run.bottlenecks}
          cell={(value, key) => <Plain key={key} text={num(value)} />} />
      </Section>
  )
  const s_pairwise = (
      <Section key="pairwise" title={t('showcase.metrics.pairwise')} hint={t('showcase.metrics.pairwiseHint')} tableView={tableView}
        chart={batch.pairwise.length ? <PairBars label={chartOf(t('showcase.metrics.pairwise'))} pairs={batch.pairwise} t={t} /> : <p className="showcase-metrics-empty">{t('showcase.metrics.noPairs')}</p>}>
        {batch.pairwise.length ? (
          <table className="showcase-metrics-table" aria-label={t('showcase.metrics.pairwiseTable')}>
            <thead><tr><th>A</th><th>B</th><th>{t('showcase.both')}</th><th>{t('showcase.metrics.onlyA')}</th><th>{t('showcase.metrics.onlyB')}</th><th>{t('showcase.neither')}</th></tr></thead>
            <tbody>
              {batch.pairwise.map(pair => (
                <tr key={`${pair.a}-${pair.b}`}><td>{pair.a}</td><td>{pair.b}</td><td>{pair.both}</td><td>{pair.only_a}</td><td>{pair.only_b}</td><td>{pair.neither}</td></tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Section>
  )
  const s_matrix = (
      <Section key="matrix" className="wide" title={t('showcase.metrics.matrix')} hint={t('showcase.metrics.matrixHint')}>
        <table className="showcase-metrics-table matrix" aria-label={t('showcase.metrics.matrix')}>
          <thead><tr><th>#</th><th>{t('showcase.metrics.hardness')}</th>{matrixIds.map(id => <th key={id} scope="col" className={valid.has(id) ? '' : 'invalid'}>{id}</th>)}</tr></thead>
          <tbody>
            {batch.question_matrix.map(row => (
              <tr key={row.q}>
                <td>{row.q}</td><td>{row.hardness}</td>
                {matrixIds.map(id => <td key={id} className={`${row.ex[id] ? 'hit' : 'miss'} ${valid.has(id) ? '' : 'invalid'}`}>{row.ex[id] ? '✓' : '✗'}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
  )
  const s_outcomes = (
      <Section key="outcomes" title={t('showcase.metrics.outcomes')} hint={t('showcase.metrics.outcomesHint')}
        chart={<OutcomeBars label={chartOf(t('showcase.metrics.outcomes'))} rows={outcomeRows} methods={methods} t={t} />} />
  )
  const levelTabs = [
    ['l1', [s_quality, s_byHardness, s_byDb]],
    ['l2', [s_cost]],
    ['l3', [s_delta, s_byFeature, s_byScenario]],
    ['l4', [s_errors, s_outcomes, s_bottlenecks]],
    ['l5', [s_components, s_stages, s_process]],
    ['l6', [s_pairwise, s_matrix]],
  ]

  return (
    <section className="showcase-metrics" data-testid="showcase-metrics" aria-label={t('showcase.metrics.title')}>
      <header>
        <h2>{t('showcase.metrics.title')}</h2>
        <p>{t('showcase.metrics.subtitle')}</p>
        {data.batches.length > 1 ? (
        <div className="showcase-metrics-batches" role="group" aria-label={t('showcase.metrics.batches')}>
          {data.batches.map(item => (
            <button key={item.id} type="button" aria-pressed={item.id === batch.id} onClick={() => setBatchId(item.id)}>
              {t('showcase.metrics.batch', { count: item.sample_count })}
            </button>
          ))}
        </div>
        ) : null}
      </header>

      <ul className="showcase-kpis" aria-label={t('showcase.metrics.kpis')}>
        {runs.map(([id, run]) => (
          <li key={id} data-method={id} data-valid={valid.has(id)} className={valid.has(id) ? '' : 'invalid'} style={{ '--c': `var(--series-${Math.max(1, METHOD_ORDER.indexOf(id) + 1)})` }}>
            <span className="kpi-name">{id}<em>{valid.has(id) ? t('showcase.metrics.valid') : t('showcase.metrics.invalid')}</em></span>
            <strong>{run.quality.ex.status === 'ok' ? run.quality.ex.value.toFixed(2) : '—'}</strong>
            <small>EX · {run.quality.ex.numerator}/{run.quality.ex.denominator}</small>
            <small>{t('showcase.metrics.kpiCost', { tokens: Math.round((run.cost.total_tokens || 0) / 1000), p50: num(run.cost.latency.p50_s, 0) })}</small>
          </li>
        ))}
      </ul>

      <section className="showcase-metrics-validity" aria-label={t('showcase.metrics.validity')}>
        <p>{t('showcase.metrics.rule', { pct: rule })}</p>
        <p>{t('showcase.metrics.aligned', { count, fingerprint: batch.alignment.fingerprint })}</p>
        <ul>
          {runs.map(([id, run]) => (
            <li key={id} className={valid.has(id) ? 'valid' : 'invalid'} data-method={id}>
              <b>{id}</b>
              <span>{valid.has(id) ? `✓ ${t('showcase.metrics.valid')}` : `✗ ${t('showcase.metrics.invalid')}`}</span>
              <small>
                {t('showcase.metrics.diagnostics', {
                  rate: (run.diagnostics.degenerate_rate * 100).toFixed(0), errors: run.diagnostics.connection_errors,
                  exhausted: run.diagnostics.retries_exhausted,
                })}
              </small>
            </li>
          ))}
        </ul>
        {batch.invalid_runs.length ? <p className="warn">{t('showcase.metrics.excluded', { methods: batch.invalid_runs.join(', ') })}</p> : null}
        <p>{t('showcase.metrics.ranking')}: {batch.ranking.length ? batch.ranking.join(' > ') : '—'}</p>
      </section>

      <div className="showcase-metrics-controls">
        <div className="showcase-metrics-tabs" role="tablist" aria-label={t('showcase.metrics.levels')}
          onKeyDown={event => {
            const order = levelTabs.map(([id]) => id)
            const at = order.indexOf(level)
            const next = event.key === 'ArrowRight' ? order[(at + 1) % order.length] : event.key === 'ArrowLeft' ? order[(at + order.length - 1) % order.length] : null
            if (next) { event.preventDefault(); setLevel(next); document.getElementById(`showcase-tab-${next}`)?.focus() }
          }}>
          {levelTabs.map(([id]) => (
            <button key={id} id={`showcase-tab-${id}`} type="button" role="tab" aria-selected={level === id} aria-controls={`showcase-panel-${id}`}
              tabIndex={level === id ? 0 : -1} onClick={() => setLevel(id)}>
              {t(`showcase.metrics.level.${id}`)}
            </button>
          ))}
        </div>
        <Legend methods={methods} t={t} />
      </div>
      <div className="showcase-metrics-panels" ref={panelsRef} style={panelsStyle}>
        {levelTabs.map(([id, sections]) => (level === id ? (
          <div key={id} id={`showcase-panel-${id}`} role="tabpanel" aria-labelledby={`showcase-tab-${id}`} className="showcase-metrics-grid" data-level={id}>
            {sections}
          </div>
        ) : null))}
      </div>

      <p className="showcase-metrics-source">
        {t('showcase.metrics.source', {
          date: runs[0][1].source.recorded_on, dataset: runs[0][1].source.dataset, split: runs[0][1].source.split,
        })}
      </p>
    </section>
  )
}
