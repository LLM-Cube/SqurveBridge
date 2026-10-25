import { seriesSlot } from './resultsModel.js'
import { useEffect, useMemo, useReducer, useState } from 'react'
import ComparisonTable from './ComparisonTable.jsx'
import { comparisonContext, comparisonCsv, comparisonRows, comparisonReducer, initialComparison, MAX_COMPARISON_MEMBERS } from './comparisonSet.js'

// Why a member's numbers are not in the comparison; a member with a result is comparable.
const REASON_BY_STATUS = {
  unaligned: 'compare.unaligned',
  running: 'compare.running',
  failed: 'compare.notFinished',
  cancelled: 'compare.notFinished',
  run_missing: 'compare.runMissing',
  run_changed: 'compare.runChanged',
  missing: 'compare.noData',
}
const COST_MEASURES = [
  { id: 'tokens_total', label: 'compare.tokens' },
  { id: 'latency_s', label: 'compare.latency' },
]

const formatMeasure = (measure, value) => {
  if (value === null || value === undefined) return '—'
  if (measure === 'latency_s') return `${Number(value).toFixed(1)}s`
  if (measure === 'tokens_total') return String(Math.round(value))
  return `${(value * 100).toFixed(1)}%`
}

/** Choose one benchmark, tick the methods to compare on it, and read their metrics side by side. */
export default function ComparisonPanel({
  matrix,
  allMethods,
  comparisonState,
  onComparisonAction,
  source = 'archive',
  liveEvaluation = false,
  api,
  postJson,
  onOpenRun,
  onCompareDiff,
  t,
}) {
  const [localSet, localDispatch] = useReducer(comparisonReducer, undefined, initialComparison)
  const set = comparisonState || localSet
  const dispatch = onComparisonAction || localDispatch
  const [requestError, setRequestError] = useState('')
  const [savedId, setSavedId] = useState('')
  const [pickedSource, setPickedSource] = useState(source)
  const [barMeasure, setBarMeasure] = useState('ex')
  const [snapshots, setSnapshots] = useState([])
  const [opened, setOpened] = useState(null)
  const labels = matrix?.labels || {}
  const metrics = matrix?.metrics || []

  useEffect(() => {
    if (!api) return undefined
    let active = true
    api('/api/comparison-snapshots')
      .then(data => { if (active) setSnapshots(Array.isArray(data?.snapshots) ? data.snapshots : []) })
      .catch(() => {})
    return () => { active = false }
  }, [api])

  // Every result of the benchmark is a candidate member; a missing one has nothing to show.
  const candidatesOf = benchmark => (matrix?.cells || []).filter(cell => cell.dataset === benchmark)
  const rows = useMemo(() => comparisonRows(set, matrix, source), [set, matrix, source])
  const comparableRows = rows.filter(row => row.comparable)

  if (!matrix?.datasets?.length) return null

  const choose = benchmark => dispatch({ type: 'chooseBenchmark', benchmark })
  const selected = method => set.members.some(member => member.method === method)
  const toggle = cell => {
    if (selected(cell.method)) {
      dispatch({ type: 'removeMember', variant: cell.method })
      return
    }
    if (!set.members.length) setPickedSource(source)
    dispatch({ type: 'addMember', context: comparisonContext(matrix, source), member: { method: cell.method, dataset: set.benchmark, label: labels[cell.method] || cell.method, runId: cell.run_id || null } })
  }

  const saveSnapshot = async () => {
    if (!postJson || !rows.length) return
    setRequestError('')
    try {
    const saved = await postJson('/api/comparison-snapshots', {
      name: `${set.benchmark} comparison`,
      benchmark: set.benchmark,
      source,
      sampling: matrix.sampling,
      split: matrix.split,
      members: rows.map(row => ({
        method: row.method,
        variant: row.method,
        label: row.label,
        run_id: row.runId,
        stages: row.stages,
      })),
    })
    setSavedId(saved?.id || '')
    setSnapshots(current => [...current, saved].filter(item => item?.id))
    } catch { setRequestError(t('compare.requestFailed')) }
  }

  const openSnapshot = async id => {
    if (!api) return
    setRequestError('')
    try { setOpened(await api(`/api/comparison-snapshots/${id}`)) } catch { setRequestError(t('compare.requestFailed')) }
  }

  const exportCsv = () => {
    const text = comparisonCsv({
      benchmark: set.benchmark,
      source,
      split: matrix.split,
      sampling: matrix.sampling,
      rows,
      metrics: metrics.map(metric => metric.id),
    })
    const href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = `${set.benchmark}-comparison.csv`
    anchor.click()
    URL.revokeObjectURL(href)
  }

  const barOptions = [...metrics.map(metric => ({ id: metric.id, label: metric.label })), ...COST_MEASURES.map(item => ({ id: item.id, label: t(item.label) }))]
  const barValue = row => (COST_MEASURES.some(item => item.id === barMeasure) ? row.cost?.[barMeasure] : row.values?.[barMeasure])
  const barRows = comparableRows.filter(row => barValue(row) !== null && barValue(row) !== undefined)
  const barMax = Math.max(0, ...barRows.map(row => Number(barValue(row)) || 0))
  const diffReady = comparableRows.length === 2 && comparableRows.every(row => row.runId)
  const sourceChanged = rows.length > 0 && (pickedSource !== source || (set.context && set.context !== comparisonContext(matrix, source)))

  return (
    <section className="comparison-panel" aria-label={t('compare.title')}>
      <div className="comparison-benchmarks" role="group" aria-label={t('compare.benchmark')}>
        {matrix.datasets.map(benchmark => (
          <button
            key={benchmark}
            type="button"
            aria-pressed={set.benchmark === benchmark}
            onClick={() => choose(benchmark)}
          >
            {benchmark}
          </button>
        ))}
      </div>

      {requestError && <p role="alert">{requestError}</p>}
      {set.benchmark && (
        <fieldset className="comparison-members">
          <legend>{t('compare.members', { benchmark: set.benchmark })}</legend>
          {candidatesOf(set.benchmark).map(cell => (
            <label key={cell.method}>
              <input
                type="checkbox"
                checked={selected(cell.method)}
                onChange={() => toggle(cell)}
                disabled={!selected(cell.method) && set.members.length >= MAX_COMPARISON_MEMBERS}
                aria-label={labels[cell.method] || cell.method}
              />
              {labels[cell.method] || cell.method}
            </label>
          ))}
          {set.members.length >= MAX_COMPARISON_MEMBERS && (
            <small>{t('compare.capReached', { max: MAX_COMPARISON_MEMBERS })}</small>
          )}
        </fieldset>
      )}

      {rows.length === 0 && <p className="comparison-empty">{t('compare.pickMethods')}</p>}

      {sourceChanged && (
        <p className="comparison-warning" role="status" aria-label={t('compare.sourceChanged')}>{t('compare.sourceChanged')}</p>
      )}

      {rows.length > 0 && (
        <>
          <p>{t(rows.every(row => row.comparable) ? 'compare.alignment' : 'compare.unverified')}</p>
          <ComparisonTable onOpenRun={onOpenRun} benchmark={set.benchmark} metrics={metrics} rows={rows} t={t} />
          <div className="comparison-runs">
            {rows.filter(row => row.runId).map(row => (
              <button key={row.runId} type="button" onClick={() => onOpenRun?.(row.runId)}>{row.runId}</button>
            ))}
            <button type="button" disabled={!diffReady} onClick={() => onCompareDiff?.(comparableRows[0].runId, comparableRows[1].runId)}>
              {t('compare.diffPair')}
            </button>
          </div>
          <label>
            <span>{t('compare.barMeasure')}</span>
            <select aria-label={t('compare.barMeasure')} value={barMeasure} onChange={event => setBarMeasure(event.target.value)}>
              {barOptions.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
            </select>
          </label>
          {barRows.length > 0 && (
            <ul className="comparison-bars" aria-label={t('compare.bars')}>
              {[...barRows].sort((a, b) => Number(barValue(b)) - Number(barValue(a))).map(row => (
                <li key={row.method}>
                  <span>{row.label}</span>
                  <i data-slot={seriesSlot(row.method, allMethods || matrix.methods)} style={{ width: `${barMax > 0 ? (Number(barValue(row)) / barMax) * 100 : 0}%` }} />
                  <b>{` ${formatMeasure(barMeasure, barValue(row))}`}</b>
                </li>
              ))}
            </ul>
          )}
          <div className="comparison-actions">
            <button type="button" onClick={exportCsv}>{t('compare.exportCsv')}</button>
            {liveEvaluation && !sourceChanged && comparableRows.length === rows.length && (
              <button type="button" onClick={saveSnapshot}>{t('compare.saveSnapshot')}</button>
            )}
            {savedId && <span role="status">{t('compare.saved', { id: savedId })}</span>}
          </div>
        </>
      )}

      {snapshots.length > 0 && (
        <section className="comparison-snapshots" aria-label={t('compare.savedSnapshots')}>
          <h4>{t('compare.savedSnapshots')}</h4>
          <ul>
            {snapshots.map(item => (
              <li key={item.id}>
                <button type="button" onClick={() => openSnapshot(item.id)} aria-label={t('compare.openSnapshot', { name: item.name })}>
                  {item.name}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {opened && (
        <ComparisonTable
          label={t('compare.snapshot', { name: opened.name })}
          benchmark={opened.benchmark}
          metrics={metrics}
          rows={opened.members.map(member => ({
            method: member.method,
            label: member.label,
            values: member.status === 'ok' ? member.values || {} : {},
            cost: member.status === 'ok' ? member.cost || {} : {},
            runId: member.run_id,
            sampleCount: member.sample_count ?? null,
            reason: member.status === 'ok' ? null : REASON_BY_STATUS[member.status] || 'compare.noData',
          }))}
          t={t}
        />
      )}
    </section>
  )
}
