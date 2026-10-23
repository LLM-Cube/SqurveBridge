/** The comparison set: the methods (and variants) compared on one benchmark.
 * Pure transitions; the comparison view renders the set and its results.
 */

// A table of more than a handful of columns stops being readable side by side.
export const MAX_COMPARISON_MEMBERS = 6

export const initialComparison = () => ({ benchmark: null, members: [] })

export function comparisonReducer(set, action) {
  switch (action.type) {
    case 'chooseBenchmark':
      // A different benchmark is a different question, so the members do not carry over.
      if (action.benchmark === set.benchmark) return set
      return { benchmark: action.benchmark, members: [] }
    case 'addMember': {
      const { member } = action
      if (member.dataset !== set.benchmark) return set
      if (set.members.some(item => item.method === member.method)) return set
      if (set.members.length >= MAX_COMPARISON_MEMBERS) return set
      return { ...set, ...(set.members.length === 0 && action.context ? { context: action.context } : {}), members: [...set.members, member] }
    }
    case 'removeMember':
      return { ...set, members: set.members.filter(item => item.method !== action.variant) }
    default:
      return set
  }
}

const isNumber = value => typeof value === 'number' && Number.isFinite(value)

/** Each member's value for one metric, the best value, and every gap to it in points.
 * A metric a member did not compute is missing for that member, never zero.
 */
export function compareMetric(rows, metricId) {
  const values = rows.map(row => (isNumber(row.values?.[metricId]) ? row.values[metricId] : null))
  const measured = values.filter(value => value !== null)
  const best = measured.length ? Math.max(...measured) : null
  const bestRow = best === null ? null : rows[values.indexOf(best)].method
  return {
    best: bestRow,
    rows: rows.map((row, index) => {
      const value = values[index]
      return {
        method: row.method,
        value,
        gap: value === null ? null : Math.round((value - best) * 1000) / 10,
        best: value !== null && value === best,
      }
    }),
  }
}

/** A comparison is pinned to its source/cohort and to the runs selected by the user. */
export function comparisonContext(matrix, source) {
  return JSON.stringify([source, matrix?.experiment_id || null, matrix?.split || 'dev', matrix?.sampling?.limit, matrix?.sampling?.mode, matrix?.sampling?.mode === 'random' ? matrix?.sampling?.seed : null])
}

export function comparisonRows(set, matrix, source) {
  const contextChanged = set.context && set.context !== comparisonContext(matrix, source)
  const cells = (matrix?.cells || []).filter(cell => cell.dataset === set.benchmark)
  const selectedCells = cells.filter(cell => set.members.some(member => member.method === cell.method) && cell.status === 'ok')
  const unverified = selectedCells.length > 1 && selectedCells.some(cell => !cell.sample_hash)
  const hashes = cells.filter(cell => set.members.some(member => member.method === cell.method) && cell.status === 'ok').map(cell => cell.sample_hash).filter(Boolean)
  const unaligned = new Set(hashes).size > 1
  return set.members.map(member => {
    const cell = cells.find(cell => cell.method === member.method)
    const changed = member.runId && member.runId !== cell?.run_id
    const comparable = !contextChanged && !changed && !unaligned && !unverified && cell?.status === 'ok'
    return { method: member.method, label: member.label || matrix?.labels?.[member.method] || member.method,
      values: comparable ? cell.values || {} : {}, cost: comparable ? cell.cost || {} : {},
      sampleCount: cell?.sample_count ?? null, runId: member.runId || cell?.run_id || null,
      exVariant: cell?.ex_variant || 'custom', stages: cell?.stages || {}, metricReasons: cell?.not_computable || {}, comparable,
      reason: contextChanged ? 'compare.sourceChanged' : changed ? 'compare.runChanged' : unaligned ? 'compare.unaligned' : unverified ? 'compare.unverified' : comparable ? null : `compare.${({ running: 'running', failed: 'notFinished', cancelled: 'notFinished', unaligned: 'unaligned' })[cell?.status] || 'noData'}`,
    }
  })
}

const csvCell = value => {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/** The comparison as CSV. A leading comment names the benchmark, split and sampling so numbers can be traced,
 * and each row says whether the member was comparable (aligned to the same questions) or not.
 */
export function comparisonCsv({ benchmark, source, split, sampling, rows, metrics }) {
  const header = ['method', 'label', ...metrics, 'tokens_total', 'latency_s', 'sample_count', 'run_id', 'comparable']
  const body = rows.map(row => [
    row.method,
    row.label,
    ...metrics.map(metric => row.values?.[metric] ?? null),
    row.cost?.tokens_total ?? null,
    row.cost?.latency_s ?? null,
    row.sampleCount ?? null,
    row.runId ?? null,
    row.comparable === false ? 'no' : 'yes',
  ].map(csvCell).join(','))
  const meta = `# benchmark=${benchmark} source=${source} split=${split ?? ''} limit=${sampling.limit} mode=${sampling.mode} seed=${sampling.seed}`
  return [meta, header.join(','), ...body].join('\n')
}
