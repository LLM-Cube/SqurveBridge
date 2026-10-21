import { useEffect, useMemo, useState } from 'react'
import { costScatter, diffCandidates } from './resultsModel.js'

const WIDTH = 560
const HEIGHT = 260
const PAD = { top: 16, right: 132, bottom: 36, left: 48 }
const LABEL_GAP = 13

/** Accuracy vs one cost. One hue for every point; identity comes from its direct label. */
export function CostScatter({ matrix, metricId, metricLabel, t }) {
  const [costKey, setCostKey] = useState('latency_s')
  const plot = costScatter(matrix, metricId, costKey)
  const yValues = plot.points.map(point => point.y)
  const yMin = plot.unit ? Math.max(0, Math.floor((Math.min(...yValues, 1) - 0.05) * 10) / 10) : 0
  const yMax = plot.yMax || 1
  const xMax = plot.xMax || 1
  const innerWidth = WIDTH - PAD.left - PAD.right
  const innerHeight = HEIGHT - PAD.top - PAD.bottom
  const px = x => PAD.left + (x / xMax) * innerWidth
  const py = y => PAD.top + (1 - (y - yMin) / Math.max(yMax - yMin, 1e-9)) * innerHeight

  // Push labels apart vertically so close points stay readable.
  const placed = [...plot.points]
    .map(point => ({ ...point, cx: px(point.x), cy: py(point.y) }))
    .sort((a, b) => a.cy - b.cy)
  for (let index = 1; index < placed.length; index += 1) {
    placed[index].ly = Math.max(placed[index].cy, (placed[index - 1].ly ?? placed[index - 1].cy) + LABEL_GAP)
  }
  if (placed[0]) placed[0].ly = placed[0].cy

  const format = value => (plot.unit ? `${Math.round(value * 100)}%` : value.toFixed(2))
  const xTicks = [0, 0.5, 1].map(ratio => ratio * xMax)
  const yTicks = [yMin, (yMin + yMax) / 2, yMax]

  return (
    <figure className="results-scatter" data-testid="results-scatter">
      <figcaption>
        <span>{t('results.scatterCaption', { metric: metricLabel })}</span>
        <label>
          <span>{t('results.cost')}</span>
          <select value={costKey} onChange={event => setCostKey(event.target.value)} aria-label={t('results.cost')}>
            <option value="latency_s">{t('results.costLatency')}</option>
            <option value="tokens_total">{t('results.costTokens')}</option>
          </select>
        </label>
      </figcaption>
      {plot.points.length === 0 ? (
        <p className="results-note">{t('results.scatterEmpty')}</p>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={t('results.scatterCaption', { metric: metricLabel })}>
          {yTicks.map(tick => (
            <g key={`y${tick}`}>
              <line className="results-grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={py(tick)} y2={py(tick)} />
              <text className="results-axis" x={PAD.left - 6} y={py(tick) + 4} textAnchor="end">{format(tick)}</text>
            </g>
          ))}
          {xTicks.map(tick => (
            <text key={`x${tick}`} className="results-axis" x={px(tick)} y={HEIGHT - PAD.bottom + 16} textAnchor="middle">
              {costKey === 'latency_s' ? `${tick.toFixed(0)}s` : Math.round(tick).toLocaleString()}
            </text>
          ))}
          <text className="results-axis" x={PAD.left + innerWidth / 2} y={HEIGHT - 4} textAnchor="middle">
            {costKey === 'latency_s' ? t('results.costLatency') : t('results.costTokens')}
          </text>
          {placed.map(point => (
            <g key={point.label}>
              <title>{`${point.label} · ${format(point.y)} · ${costKey === 'latency_s' ? `${point.x.toFixed(1)}s` : point.x}`}</title>
              <circle className="results-point" cx={point.cx} cy={point.cy} r="5" />
              <circle cx={point.cx} cy={point.cy} r="12" fill="transparent" />
              {point.ly !== point.cy && (
                <line className="results-leader" x1={point.cx + 6} y1={point.cy} x2={point.cx + 10} y2={point.ly} />
              )}
              <text className="results-point-label" x={point.cx + 12} y={point.ly + 4}>{point.label}</text>
            </g>
          ))}
        </svg>
      )}
      {plot.omitted.length > 0 && (
        <p className="results-note">{t('results.scatterOmitted', { cells: plot.omitted.join(', ') })}</p>
      )}
    </figure>
  )
}

const CATEGORIES = ['a_only', 'b_only', 'both_wrong', 'both_right', 'unknown']

/** Question-by-question comparison of two runs on one benchmark, with replay in Query. */
export function DiffPanel({ api, matrix, onReplay, t }) {
  const candidates = useMemo(() => diffCandidates(matrix), [matrix])
  const datasets = Object.keys(candidates)
  const [dataset, setDataset] = useState(datasets[0] || '')
  const runs = candidates[dataset] || []
  const [aId, setAId] = useState('')
  const [bId, setBId] = useState('')
  const [category, setCategory] = useState('a_only')
  const [diff, setDiff] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!datasets.includes(dataset)) setDataset(datasets[0] || '')
  }, [datasets.join('|')])

  useEffect(() => {
    setAId(runs[0]?.runId || '')
    setBId(runs[1]?.runId || '')
  }, [dataset, runs.map(run => run.runId).join('|')])

  useEffect(() => {
    if (!api || !aId || !bId || aId === bId) {
      setDiff(null)
      return undefined
    }
    let active = true
    setError('')
    const params = new URLSearchParams({ a: aId, b: bId, category, limit: '50' })
    api(`/api/results/diff?${params}`)
      .then(data => { if (active) setDiff(Array.isArray(data?.rows) ? data : null) })
      .catch(err => { if (active) setError(err.message || t('results.loadFailed')) })
    return () => { active = false }
  }, [api, aId, bId, category])

  if (!datasets.length) return null
  const methodOf = runId => runs.find(run => run.runId === runId)?.method || runId
  const aMethod = methodOf(aId)
  const bMethod = methodOf(bId)
  const mark = value => (value === true ? '✓' : value === false ? '✗' : '?')

  return (
    <section className="results-diff" data-testid="results-diff">
      <header>
        <h4>{t('results.diffTitle')}</h4>
        <p>{t('results.diffDescription')}</p>
      </header>
      <div className="results-controls">
        <label>
          <span>{t('results.diffBenchmark')}</span>
          <select value={dataset} onChange={event => setDataset(event.target.value)}>
            {datasets.map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>
          <span>A</span>
          <select aria-label={t('results.diffMethodA')} value={aId} onChange={event => setAId(event.target.value)}>
            {runs.map(run => <option key={run.runId} value={run.runId}>{run.method}</option>)}
          </select>
        </label>
        <label>
          <span>B</span>
          <select aria-label={t('results.diffMethodB')} value={bId} onChange={event => setBId(event.target.value)}>
            {runs.map(run => <option key={run.runId} value={run.runId}>{run.method}</option>)}
          </select>
        </label>
      </div>
      {error && <p className="error-banner" role="alert">{error}</p>}
      {diff && (
        <>
          <div className="results-diff-summary" role="group" aria-label={t('results.diffFilter')}>
            {CATEGORIES.filter(name => name !== 'unknown' || diff.summary.unknown).map(name => (
              <button
                key={name}
                type="button"
                aria-pressed={category === name}
                className={category === name ? 'active' : ''}
                onClick={() => setCategory(name)}
              >
                {t(`results.diff.${name}`, { a: aMethod, b: bMethod })}
                <b>{diff.summary[name]}</b>
              </button>
            ))}
          </div>
          {diff.rows.length === 0 ? (
            <p className="results-note">{t('results.diffEmpty')}</p>
          ) : (
            <div className="results-diff-table">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{t('results.diffQuestion')}</th>
                    <th scope="col">{aMethod}</th>
                    <th scope="col">{bMethod}</th>
                    <th scope="col"><span className="visually-hidden">{t('results.diffReplay')}</span></th>
                  </tr>
                </thead>
                <tbody>
                  {diff.rows.map(row => (
                    <tr key={row.instance_id}>
                      <td>
                        <span>{row.question || row.instance_id}</span>
                        <small>{[row.instance_id, row.db_id].filter(Boolean).join(' · ')}</small>
                      </td>
                      <td title={row.a.sql || ''}>{mark(row.a.correct)}</td>
                      <td title={row.b.sql || ''}>{mark(row.b.correct)}</td>
                      <td>
                        {row.db_id && row.question && (
                          <span className="results-diff-replay">
                            {[aMethod, bMethod].map(method => (
                              <button
                                key={method}
                                type="button"
                                onClick={() => onReplay?.({ method, dataset, db_id: row.db_id, question: row.question })}
                              >
                                {t('results.diffReplayWith', { method })}
                              </button>
                            ))}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {diff.total > diff.rows.length && (
                <p className="results-note">{t('results.diffMore', { shown: diff.rows.length, total: diff.total })}</p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
