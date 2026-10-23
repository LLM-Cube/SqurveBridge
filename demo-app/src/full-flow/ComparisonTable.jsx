import { compareMetric } from './comparisonSet.js'

const formatValue = value => (value === null ? '—' : (value * 100).toFixed(1) + '%')
const formatGap = gap => (gap === null ? '—' : `${gap > 0 ? '+' : ''}${gap.toFixed(1)}`)

/** The members of a comparison side by side: one row each, one column per metric.
 * Each metric marks its best member and shows every other gap in points. A member
 * that cannot be compared shows its reason in place of values.
 */
export default function ComparisonTable({ benchmark, label, metrics, rows, onOpenRun, t }) {
  const ranked = metrics.map(metric => ({ metric, result: compareMetric(rows, metric.id) }))
  return (
    <div className="comparison-table-wrap"><table className="comparison-table" aria-label={label || t('compare.tableLabel', { benchmark })}>
      <thead>
        <tr>
          <th scope="col">{t('compare.member')}</th>
          {ranked.map(({ metric }) => <th key={metric.id} scope="col">{metric.label}</th>)}
          <th scope="col">{t('compare.samples')}</th>
          <th scope="col">{t('compare.tokens')}</th>
          <th scope="col">{t('compare.latency')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const isBest = ranked.some(({ result }) => result.best === row.method)
          return (
            <tr key={row.method} aria-label={row.label} data-best={String(isBest)}>
              <th scope="row">{row.label} <small>EX · {row.exVariant || 'custom'}</small></th>
              {row.reason ? (
                <td colSpan={ranked.length || 1}><span className="comparison-reason">{t(row.reason)}</span></td>
              ) : ranked.map(({ metric, result }) => {
                const cell = result.rows.find(item => item.method === row.method)
                return (
                  <td key={metric.id} data-best={String(cell?.best === true)}>
                    {row.runId && onOpenRun ? <button type="button" onClick={() => onOpenRun(row.runId)}>{formatValue(cell?.value ?? null)}</button> : <span>{formatValue(cell?.value ?? null)}</span>}
                    {row.metricReasons?.[metric.id] && <small>{String(row.metricReasons[metric.id])}</small>}
                    {cell?.gap !== null && cell?.gap !== undefined && !cell.best
                      ? <small> {formatGap(cell.gap)}</small>
                      : null}
                  </td>
                )
              })}
              <td>{row.sampleCount ?? '—'}</td>
              <td>{row.cost?.tokens_total ?? '—'}</td>
              <td>{row.cost?.latency_s == null ? '—' : `${Number(row.cost.latency_s).toFixed(1)}s`}</td>
            </tr>
          )
        })}
      </tbody>
    </table></div>
  )
}
