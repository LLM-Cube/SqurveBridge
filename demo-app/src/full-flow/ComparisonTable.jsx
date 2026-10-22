import { compareMetric } from './comparisonSet.js'

const formatValue = value => (value === null ? '—' : (value * 100).toFixed(1) + '%')
const formatGap = gap => (gap === null ? '—' : `${gap > 0 ? '+' : ''}${gap.toFixed(1)}`)

/** The members of a comparison set side by side: one row each, one column per metric.
 * Each metric marks its best member and shows every other gap in points.
 */
export default function ComparisonTable({ benchmark, metrics, rows, t }) {
  const ranked = metrics.map(metric => ({ metric, result: compareMetric(rows, metric.id) }))
  return (
    <table className="comparison-table" aria-label={t('compare.tableLabel', { benchmark })}>
      <thead>
        <tr>
          <th scope="col">{t('compare.member')}</th>
          {ranked.map(({ metric }) => <th key={metric.id} scope="col">{metric.label}</th>)}
          <th scope="col">{t('compare.samples')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const isBest = ranked.some(({ result }) => result.best === row.method)
          return (
            <tr
              key={row.method}
              aria-label={row.label}
              data-best={String(isBest)}
            >
              <th scope="row">{row.label}</th>
              {ranked.map(({ metric, result }) => {
                const cell = result.rows.find(item => item.method === row.method)
                return (
                  <td key={metric.id} data-best={String(cell?.best === true)}>
                    <span>{formatValue(cell?.value ?? null)}</span>
                    {cell?.gap !== null && cell?.gap !== undefined && !cell.best
                      ? <small> {formatGap(cell.gap)}</small>
                      : null}
                  </td>
                )
              })}
              <td>{row.sampleCount ?? '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
