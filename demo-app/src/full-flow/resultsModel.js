/** View models for the cross-benchmark comparison: pure functions from the
 * results matrix response to what the heatmap and bar chart draw.
 */

import { DATABASES, METHODS, configKey } from './model.js'

export const HEAT_STEPS = 7

const ORDER = ['ex', 'em', 'sf1', 'ves', 'rves', 'sc']

export function metricOptions(matrix) {
  const metrics = Array.isArray(matrix?.metrics) ? matrix.metrics : []
  const rank = id => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length)
  return [...metrics].sort((a, b) => rank(a.id) - rank(b.id))
}

/** A column's display name: the server labels variants; a plain method is its own label. */
export const labelOf = (matrix, method) => matrix?.labels?.[method] || method

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
    label: labelOf(matrix, method),
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
          tooltip: [labelOf(matrix, method), dataset, reason || shown].join(' · '),
          runId: cell?.run_id || null,
        }
      }
      const ratio = scale.max > 0 ? value / scale.max : 0
      return {
        dataset,
        status: 'ok',
        value,
        step: Math.min(HEAT_STEPS - 1, Math.floor(ratio * HEAT_STEPS)),
        text: formatValue(value, scale) + (metricId === 'ex' ? ` · ${cell?.ex_variant || 'custom'}` : ''),
        tooltip: [labelOf(matrix, method), dataset, formatValue(value, scale), cell.sample_count ? `n=${cell.sample_count}` : '']
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
        absent.push({
          method,
          label: labelOf(matrix, method),
          status: cell?.status === 'ok' ? 'not_computable' : (cell?.status || 'missing'),
        })
      } else {
        bars.push({
          method,
          label: labelOf(matrix, method),
          value,
          text: formatValue(value, scale) + (metricId === 'ex' ? ` · ${cell?.ex_variant || 'custom'}` : ''),
          slot: seriesSlot(method, allMethods),
        })
      }
    }
    return { dataset, bars, absent, max: scale.max }
  })
}

/** Accuracy against one cost (tokens or latency) for every comparable cell that reports it. */
export function costScatter(matrix, metricId, costKey) {
  const points = []
  const omitted = []
  for (const cell of matrix?.cells || []) {
    const label = `${labelOf(matrix, cell.method)} · ${cell.dataset}${metricId === 'ex' ? ` · EX ${cell.ex_variant || 'custom'}` : ''}`
    const y = comparableValue(cell, metricId)
    const x = cell?.cost?.[costKey]
    if (y === null || typeof x !== 'number' || !Number.isFinite(x)) {
      if (cell?.status !== 'missing') omitted.push(label)
      continue
    }
    points.push({ label, method: cell.method, dataset: cell.dataset, x, y, runId: cell.run_id || null })
  }
  const scale = scaleFor(matrix, metricId)
  return {
    points,
    omitted,
    xMax: Math.max(0, ...points.map(point => point.x)),
    yMax: scale.unit ? 1 : Math.max(0, ...points.map(point => point.y)),
    unit: scale.unit,
  }
}

/** Per benchmark, the comparable runs a per-question diff can pair up. */
export function diffCandidates(matrix) {
  const byDataset = {}
  for (const cell of matrix?.cells || []) {
    if (cell?.status !== 'ok' || !cell.run_id) continue
    ;(byDataset[cell.dataset] ||= []).push({ method: cell.method, label: labelOf(matrix, cell.method), runId: cell.run_id })
  }
  return Object.fromEntries(Object.entries(byDataset).filter(([, runs]) => runs.length > 1))
}

/** Compose labels for a config's method and dataset ids, or null when unknown. */
export function displayNamesFor(methodId, datasetId) {
  const method = METHODS.find(name => configKey(name, 'x') === configKey(methodId, 'x'))
  const database = DATABASES.find(name => configKey('x', name) === configKey('x', datasetId))
  return method && database ? { method, database } : null
}
