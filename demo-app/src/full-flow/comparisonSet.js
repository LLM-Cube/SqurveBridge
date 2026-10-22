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
      return { ...set, members: [...set.members, member] }
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
        best: row.method === bestRow,
      }
    }),
  }
}

const csvCell = value => {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/** The comparison as CSV, with the benchmark and sampling in a leading comment line so numbers can be traced. */
export function comparisonCsv({ benchmark, source, sampling, rows, metrics }) {
  const header = ['method', 'label', ...metrics, 'sample_count', 'run_id']
  const body = rows.map(row => [
    row.method,
    row.label,
    ...metrics.map(metric => row.values?.[metric] ?? null),
    row.sampleCount ?? null,
    row.runId ?? null,
  ].map(csvCell).join(','))
  const meta = `# benchmark=${benchmark} source=${source} limit=${sampling.limit} mode=${sampling.mode} seed=${sampling.seed}`
  return [meta, header.join(','), ...body].join('\n')
}
