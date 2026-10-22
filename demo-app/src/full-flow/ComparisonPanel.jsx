import { useMemo, useReducer, useState } from 'react'
import ComparisonTable from './ComparisonTable.jsx'
import { comparisonCsv, comparisonReducer, initialComparison } from './comparisonSet.js'

/** Choose one benchmark, tick the methods to compare on it, and read their metrics side by side. */
export default function ComparisonPanel({ matrix, source = 'archive', liveEvaluation = false, postJson, t }) {
  const [set, dispatch] = useReducer(comparisonReducer, undefined, initialComparison)
  const [savedId, setSavedId] = useState('')
  const labels = matrix?.labels || {}
  const metrics = matrix?.metrics || []

  // Only cells that produced a result can be compared.
  const membersOf = benchmark => (matrix?.cells || []).filter(cell => cell.dataset === benchmark && cell.status === 'ok')
  const rows = useMemo(() => (set.members || []).map(member => {
    const cell = membersOf(set.benchmark).find(item => item.method === member.method)
    return {
      method: member.method,
      label: member.label || labels[member.method] || member.method,
      values: cell?.values || {},
      sampleCount: cell?.sample_count ?? null,
      runId: cell?.run_id ?? null,
    }
  }), [set, matrix])

  if (!matrix?.datasets?.length) return null

  const choose = benchmark => dispatch({ type: 'chooseBenchmark', benchmark })
  const selected = method => set.members.some(member => member.method === method)
  const toggle = cell => {
    if (selected(cell.method)) dispatch({ type: 'removeMember', variant: cell.method })
    else dispatch({ type: 'addMember', member: { method: cell.method, dataset: set.benchmark, label: labels[cell.method] || cell.method } })
  }

  const saveSnapshot = async () => {
    if (!postJson || !rows.length) return
    const saved = await postJson('/api/comparison-snapshots', {
      name: `${set.benchmark} comparison`,
      benchmark: set.benchmark,
      source,
      sampling: matrix.sampling,
      members: rows.map(row => ({
        method: row.method,
        variant: row.method,
        label: row.label,
        run_id: row.runId,
        stages: {},
      })),
    })
    setSavedId(saved?.id || '')
  }

  const exportCsv = () => {
    const text = comparisonCsv({
      benchmark: set.benchmark,
      source,
      sampling: matrix.sampling,
      rows: rows.map(row => ({ ...row, values: row.values })),
      metrics: metrics.map(metric => metric.id),
    })
    const href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = `${set.benchmark}-comparison.csv`
    anchor.click()
    URL.revokeObjectURL(href)
  }

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

      {set.benchmark && (
        <fieldset className="comparison-members">
          <legend>{t('compare.members', { benchmark: set.benchmark })}</legend>
          {membersOf(set.benchmark).map(cell => (
            <label key={cell.method}>
              <input
                type="checkbox"
                checked={selected(cell.method)}
                onChange={() => toggle(cell)}
                aria-label={labels[cell.method] || cell.method}
              />
              {labels[cell.method] || cell.method}
            </label>
          ))}
        </fieldset>
      )}

      {rows.length > 0 && (
        <>
          <ComparisonTable benchmark={set.benchmark} metrics={metrics} rows={rows} t={t} />
          <div className="comparison-actions">
            <button type="button" onClick={exportCsv}>{t('compare.exportCsv')}</button>
            {liveEvaluation && (
              <button type="button" onClick={saveSnapshot}>{t('compare.saveSnapshot')}</button>
            )}
            {savedId && <span role="status">{t('compare.saved', { id: savedId })}</span>}
          </div>
        </>
      )}
    </section>
  )
}
