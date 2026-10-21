/** View models for the cross-benchmark comparison: pure functions from the
 * results matrix response to what the heatmap and bar chart draw.
 */

export const HEAT_STEPS = 7

const ORDER = ['ex', 'em', 'sf1', 'ves', 'rves', 'sc']

export function metricOptions(matrix) {
  const metrics = Array.isArray(matrix?.metrics) ? matrix.metrics : []
  const rank = id => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length)
  return [...metrics].sort((a, b) => rank(a.id) - rank(b.id))
}

const cellAt = (matrix, method, dataset) =>
  (matrix?.cells || []).find(item => item.method === method && item.dataset === dataset)

const comparableValue = (cell, metricId) => {
  if (cell?.status !== 'ok') return null
  const value = cell.values?.[metricId]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Unit metrics (all values within 0–1) read as percentages; others as numbers. */
function scaleFor(matrix, metricId) {
  const values = (matrix?.cells || []).map(cell => comparableValue(cell, metricId)).filter(value => value !== null)
  const unit = values.every(value => value >= 0 && value <= 1)
  return { unit, max: unit ? 1 : Math.max(...values, 0) }
}

const formatValue = (value, scale) => (scale.unit ? `${(value * 100).toFixed(1)}%` : value.toFixed(2))

export function heatmapRows(matrix, metricId) {
  const scale = scaleFor(matrix, metricId)
  return (matrix?.methods || []).map(method => ({
    method,
    cells: (matrix?.datasets || []).map(dataset => {
      const cell = cellAt(matrix, method, dataset)
      const status = cell?.status || 'missing'
      const value = comparableValue(cell, metricId)
      if (value === null) {
        const reason = cell?.not_computable?.[metricId]
        const shown = status === 'ok' ? 'not_computable' : status
        return {
          dataset,
          status: shown,
          value: null,
          step: null,
          text: shown === 'not_computable' ? '—' : shown,
          tooltip: [method, dataset, reason || shown].join(' · '),
          runId: cell?.run_id || null,
        }
      }
      const ratio = scale.max > 0 ? value / scale.max : 0
      return {
        dataset,
        status: 'ok',
        value,
        step: Math.min(HEAT_STEPS - 1, Math.floor(ratio * HEAT_STEPS)),
        text: formatValue(value, scale),
        tooltip: [method, dataset, formatValue(value, scale), cell.sample_count ? `n=${cell.sample_count}` : '']
          .filter(Boolean).join(' · '),
        runId: cell.run_id || null,
      }
    }),
  }))
}

/** 1-based categorical slot from the method's place in the full, stable list. */
export function seriesSlot(method, allMethods) {
  const index = (allMethods || []).indexOf(method)
  return index < 0 ? null : index + 1
}

export function groupedBars(matrix, metricId, allMethods) {
  const scale = scaleFor(matrix, metricId)
  return (matrix?.datasets || []).map(dataset => {
    const bars = []
    const absent = []
    for (const method of matrix?.methods || []) {
      const cell = cellAt(matrix, method, dataset)
      const value = comparableValue(cell, metricId)
      if (value === null) {
        absent.push({ method, status: cell?.status === 'ok' ? 'not_computable' : (cell?.status || 'missing') })
      } else {
        bars.push({ method, value, text: formatValue(value, scale), slot: seriesSlot(method, allMethods) })
      }
    }
    return { dataset, bars, absent, max: scale.max }
  })
}
