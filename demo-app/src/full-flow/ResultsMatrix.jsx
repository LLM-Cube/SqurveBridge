import { useEffect, useMemo, useState } from 'react'
import { SAMPLE_LIMITS, resolveFocusedConfig } from './model.js'
import { CostScatter, DiffPanel } from './ResultsInsights.jsx'
import ComparisonPanel from './ComparisonPanel.jsx'
import { groupedBars, heatmapRows, metricOptions, seriesSlot } from './resultsModel.js'
import { experimentAxes } from './variantModel.js'

// Recorded evidence shipped with the repository; the public Space opens on it.
const RECORDED = { limit: 100, mode: 'random', seed: 42 }

const unique = values => [...new Set(values.filter(Boolean))]

function axesFor(scope, configs, connections) {
  if (scope === 'all') {
    return {
      methods: unique(configs.map(item => item.method)),
      datasets: unique(configs.map(item => item.dataset)),
    }
  }
  const chosen = connections
    .map(item => resolveFocusedConfig(configs, item.method, item.database))
    .filter(Boolean)
  return { methods: unique(chosen.map(item => item.method)), datasets: unique(chosen.map(item => item.dataset)) }
}

function queryString(params) {
  return new URLSearchParams(Object.entries(params).filter(([, value]) => value !== '' && value != null)).toString()
}

function Heatmap({ matrix, metricId, onOpenRun, t }) {
  const rows = heatmapRows(matrix, metricId)
  return (
    <div className="results-heatmap-wrap">
      <table className="results-heatmap" data-testid="results-heatmap">
        <caption className="visually-hidden">{t('results.heatmapCaption')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('results.method')}</th>
            {matrix.datasets.map(dataset => <th key={dataset} scope="col">{dataset}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.method}>
              <th scope="row">{row.label}</th>
              {row.cells.map(cell => (
                <td
                  key={cell.dataset}
                  data-status={cell.status}
                  data-step={cell.step ?? undefined}
                  title={cell.tooltip}
                >
                  {cell.status === 'ok' && cell.runId ? (
                    <button type="button" onClick={() => onOpenRun?.(cell.runId)} aria-label={cell.tooltip}>
                      {cell.text}
                    </button>
                  ) : (
                    <span>{cell.status === 'ok' || cell.status === 'not_computable'
                      ? cell.text
                      : t(`results.status.${cell.status}`)}</span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GroupedBars({ matrix, metricId, allMethods, t }) {
  // Benchmarks with nothing to draw stay in the heatmap; here they would only add noise.
  const groups = groupedBars(matrix, metricId, allMethods)
    .filter(group => group.bars.length > 0)
    .map(group => ({ ...group, absent: group.absent.filter(item => item.status !== 'missing') }))
  const plotted = unique(groups.flatMap(group => group.bars.map(bar => bar.method)))
  const labels = new Map(groups.flatMap(group => group.bars.map(bar => [bar.method, bar.label])))
  if (!plotted.length) return null
  return (
    <figure className="results-bars" data-testid="results-bars">
      <figcaption>{t('results.barsCaption')}</figcaption>
      {plotted.length > 1 && (
        <ul className="results-legend" aria-label={t('results.legend')}>
          {plotted.map(method => (
            <li key={method}>
              <i data-slot={seriesSlot(method, allMethods)} aria-hidden="true" />
              {labels.get(method) || method}
            </li>
          ))}
        </ul>
      )}
      <div className="results-bar-groups">
        {groups.map(group => (
          <section key={group.dataset} className="results-bar-group" aria-label={group.dataset}>
            <h4>{group.dataset}</h4>
            {group.bars.map(bar => (
              <div key={bar.method} className="results-bar-row" title={`${bar.label} · ${group.dataset} · ${bar.text}`}>
                <span className="results-bar-label">{bar.label}</span>
                <span className="results-bar-track">
                  <i
                    data-slot={bar.slot}
                    style={{ width: `${group.max > 0 ? (bar.value / group.max) * 100 : 0}%` }}
                  />
                </span>
                <span className="results-bar-value">{bar.text}</span>
              </div>
            ))}
            {group.absent.length > 0 && (
              <p className="results-bar-absent">
                {group.absent.map(item => `${item.label}: ${t(`results.status.${item.status}`)}`).join(' · ')}
              </p>
            )}
          </section>
        ))}
      </div>
    </figure>
  )
}

export default function ResultsMatrix({
  api,
  configs = [],
  connections = [],
  experimentId = '',
  fixedSource,
  comparisonState,
  onComparisonAction,
  sampleLimit = 20,
  sampleMode = 'slice',
  sampleSeed = 42,
  liveEvaluation = false,
  postJson,
  onOpenRun,
  onReplay,
  t,
}) {
  const [sourceState, setSource] = useState(experimentId ? 'experiment' : liveEvaluation ? 'archive' : 'recorded')
  const source = fixedSource || sourceState
  const [scope, setScope] = useState(liveEvaluation ? 'selection' : 'all')
  const [sampling, setSampling] = useState(
    liveEvaluation ? { limit: sampleLimit, mode: sampleMode, seed: sampleSeed } : RECORDED,
  )
  const [metricId, setMetricId] = useState('ex')
  const [matrix, setMatrix] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  // A pair the user chose in the benchmark comparison, to open in the question diff.
  const [diffPair, setDiffPair] = useState(null)
  // An experiment's columns are its own cells, variants included.
  const [cellAxes, setCellAxes] = useState(null)

  useEffect(() => {
    if (experimentId) setSource('experiment')
  }, [experimentId])

  useEffect(() => {
    setCellAxes(null)
    if (!api || !experimentId) return undefined
    let active = true
    api(`/api/experiments/${experimentId}`)
      .then(experiment => { if (active) setCellAxes(experimentAxes(experiment)) })
      .catch(() => {})
    return () => { active = false }
  }, [api, experimentId])

  const experimentColumns = source === 'experiment' && cellAxes?.methods.length ? cellAxes : null
  const allMethods = useMemo(
    () => unique([...configs.map(item => item.method), ...(experimentColumns?.methods || [])]),
    [configs, experimentColumns],
  )
  const selectionAxes = useMemo(() => axesFor(scope, configs, connections), [scope, configs, connections])
  const axes = experimentColumns || selectionAxes
  const effectiveSampling = source === 'recorded' ? RECORDED : sampling
  const params = useMemo(() => queryString({
    methods: axes.methods.join(','),
    datasets: axes.datasets.join(','),
    ...(source === 'experiment' && experimentId
      ? { experiment_id: experimentId }
      : {
        source,
        sample_limit: effectiveSampling.limit,
        sample_mode: effectiveSampling.mode,
        sample_seed: effectiveSampling.seed,
      }),
  }), [axes, source, experimentId, effectiveSampling])

  useEffect(() => {
    if (!api || !axes.methods.length || !axes.datasets.length) return undefined
    let active = true
    setLoading(true)
    setError('')
    const load = () => api(`/api/results/matrix?${params}`)
      .then(data => {
        if (!active) return
        const usable = Array.isArray(data?.cells) && Array.isArray(data?.datasets) && Array.isArray(data?.methods)
        setMatrix(usable ? data : null)
      })
      .catch(err => { if (active) setError(err.message || t('results.loadFailed')) })
      .finally(() => { if (active) setLoading(false) })
    load()
    // An experiment fills in while it runs; refresh until the page moves on.
    const timer = source === 'experiment' ? setInterval(load, 5000) : null
    return () => {
      active = false
      if (timer) clearInterval(timer)
    }
  }, [api, params, source])

  const metrics = metricOptions(matrix)
  const activeMetric = metrics.some(item => item.id === metricId) ? metricId : metrics[0]?.id

  return (
    <section className="results-matrix" data-testid="results-matrix">
      <header className="results-matrix-head">
        <div>
          <span>{t('results.eyebrow')}</span>
          <h3>{t('results.title')}</h3>
          <p>{t('results.description')}</p>
        </div>
        {matrix && (
          <a className="flow-secondary-action" href={`/api/results/matrix.csv?${params}`} download>
            {t('results.exportCsv')}
          </a>
        )}
      </header>

      <div className="results-controls" role="group" aria-label={t('results.controls')}>
        {!fixedSource && <label>
          <span>{t('results.source')}</span>
          <select aria-label={t('results.source')} value={source} onChange={event => setSource(event.target.value)}>
            {experimentId && <option value="experiment">{t('results.sourceExperiment')}</option>}
            {liveEvaluation && <option value="archive">{t('results.sourceArchive')}</option>}
            <option value="recorded">{t('results.sourceRecorded')}</option>
          </select>
        </label>}
        {source !== 'experiment' && <label>
          <span>{t('results.scope')}</span>
          <select aria-label={t('results.scope')} value={scope} onChange={event => setScope(event.target.value)}>
            <option value="selection">{t('results.scopeSelection')}</option>
            <option value="all">{t('results.scopeAll')}</option>
          </select>
        </label>}
        {source === 'archive' && (
          <>
            <label>
              <span>{t('configure.sampleLimit')}</span>
              <select
                value={sampling.limit}
                onChange={event => setSampling(current => ({ ...current, limit: Number(event.target.value) }))}
              >
                {SAMPLE_LIMITS.map(limit => <option key={limit} value={limit}>{limit}</option>)}
              </select>
            </label>
            <label>
              <span>{t('configure.sampleMode')}</span>
              <select
                value={sampling.mode}
                onChange={event => setSampling(current => ({ ...current, mode: event.target.value }))}
              >
                <option value="slice">{t('configure.sampleSlice')}</option>
                <option value="random">{t('configure.sampleRandom')}</option>
              </select>
            </label>
          </>
        )}
        {metrics.length > 0 && (
          <label>
            <span>{t('results.metric')}</span>
            <select aria-label={t('results.metric')} value={activeMetric} onChange={event => setMetricId(event.target.value)}>
              {metrics.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        )}
      </div>

      {error && <p className="error-banner" role="alert">{error}</p>}
      {loading && !matrix && <p className="results-loading">{t('results.loading')}</p>}
      {matrix && !error && (
        <>
          <Heatmap matrix={matrix} metricId={activeMetric} onOpenRun={onOpenRun} t={t} />
          {Object.values(matrix.alignment || {}).some(item => item && !item.aligned && item.methods?.length) && (
            <p className="results-note">{t('results.unalignedNote')}</p>
          )}
          <GroupedBars matrix={matrix} metricId={activeMetric} allMethods={allMethods} t={t} />
          <CostScatter
            matrix={matrix}
            metricId={activeMetric}
            metricLabel={metrics.find(item => item.id === activeMetric)?.label || activeMetric}
            t={t}
          />
          <ComparisonPanel
            allMethods={allMethods}
            comparisonState={comparisonState}
            onComparisonAction={onComparisonAction}
            matrix={matrix}
            source={source}
            liveEvaluation={liveEvaluation}
            api={api}
            postJson={postJson}
            onOpenRun={onOpenRun}
            onCompareDiff={(a, b) => setDiffPair({ a, b, stamp: Date.now() })}
            t={t}
          />
          <DiffPanel api={api} matrix={matrix} onReplay={onReplay} pair={diffPair} t={t} />
        </>
      )}
    </section>
  )
}
