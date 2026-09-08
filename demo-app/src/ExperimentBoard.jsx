import { useEffect, useMemo, useState } from 'react'

const METHOD_COLORS = ['#3f9e68', '#2e7fa3', '#b07a1f', '#c04a40', '#6a55d0', '#3f7d7a']
const QUALITY_METRICS = [
  { id: 'ex', label: 'EX' },
  { id: 'em', label: 'EM' },
  { id: 'sf1', label: 'SF1' },
  { id: 'ves', label: 'VES' },
  { id: 'rves', label: 'RVES' },
]
const TABLE_METRICS = ['ex', 'em', 'sf1', 'sc', 'ves', 'rves']
const CF1_KEYS = ['select', 'where', 'group', 'order', 'join', 'keywords', 'iuen']

const metricAverage = value => (typeof value === 'number' ? value : value?.avg ?? (value?.status === 'available' ? value?.value : null))

const normalizeScoreBundle = scores => {
  if (scores?.schema_version !== 2) return scores
  const source = scores.aggregate || {}
  const aliases = { ex: 'ex_at_1', em: 'structural_match', sc: 'self_consistency' }
  const aggregate = { ...source }
  TABLE_METRICS.forEach(metric => {
    const cell = source[aliases[metric] || metric]
    if (cell) aggregate[metric] = {
      ...cell,
      avg: cell.status === 'available' ? cell.value : null,
      valid: cell.denominator,
      total: scores.samples?.length,
      pass_count: cell.numerator,
    }
  })
  return { ...scores, aggregate, sample_count: scores.samples?.length ?? 0, scope: scores.candidate_protocol }
}
const percent = (value, digits = 1) => (value == null || Number.isNaN(value) ? '—' : `${(value * 100).toFixed(digits)}%`)
const pp = (value, digits = 1) => {
  if (value == null || Number.isNaN(value)) return '—'
  const scaled = value * 100
  return `${scaled > 0 ? '+' : ''}${scaled.toFixed(digits)} pp`
}
const num = (value, digits = 2) => (value == null || Number.isNaN(value) ? '—' : Number(value).toFixed(digits))
const compact = value => (value == null ? '—' : Intl.NumberFormat('en', { notation: 'compact' }).format(value))
const compactNumber = value => (value == null ? 'Unavailable' : Intl.NumberFormat('en', { notation: 'compact' }).format(value))
const seconds = value => (value == null ? 'Unavailable' : `${Number(value).toFixed(2)} s`)
const colorFor = index => METHOD_COLORS[index % METHOD_COLORS.length]

const diagnosticEntries = aggregate => {
  const entries = []
  Object.entries(aggregate?.cf1 || {}).forEach(([name, value]) => entries.push([name.replace('cf1_', ''), metricAverage(value), 'percent']))
  Object.entries(aggregate?.fd || {}).forEach(([name, value]) => entries.push([`FD ${name}`, value?.mean, 'number']))
  Object.entries(aggregate?.pipeline || {}).forEach(([group, values]) => Object.entries(values || {}).forEach(([name, value]) => {
    if (typeof value === 'number') {
      entries.push([
        `${group} · ${name.replaceAll('_', ' ')}`,
        value,
        name.includes('rate') || name.includes('accuracy') || name.includes('gain') || name.includes('loss') ? 'percent' : 'number',
      ])
    }
  }))
  return entries
}
const methodIndex = (runs, method) => Math.max(0, runs.findIndex(run => run.method === method))

function polar(cx, cy, radius, angleDeg) {
  const radians = ((angleDeg - 90) * Math.PI) / 180
  return [cx + radius * Math.cos(radians), cy + radius * Math.sin(radians)]
}

/** Zoom the axis into the observed band so small gaps become visually large. */
function amplifyScaler(values, { padRatio = 0.45, minSpan = 0.04, clamp01 = true } = {}) {
  const nums = values.filter(value => typeof value === 'number' && !Number.isNaN(value))
  if (!nums.length) return { scale: () => 0, floor: 0, ceil: 1, span: 1 }
  let floor = Math.min(...nums)
  let ceil = Math.max(...nums)
  const span = Math.max(ceil - floor, minSpan)
  const pad = span * padRatio
  floor -= pad
  ceil += pad
  if (clamp01) {
    floor = Math.max(0, floor)
    ceil = Math.min(1, ceil)
  }
  if (ceil - floor < minSpan) {
    const mid = (floor + ceil) / 2
    floor = mid - minSpan / 2
    ceil = mid + minSpan / 2
  }
  return {
    floor,
    ceil,
    span: ceil - floor,
    scale: value => {
      if (value == null || Number.isNaN(value)) return 0
      return Math.max(0, Math.min(1, (value - floor) / (ceil - floor)))
    },
  }
}

function tokenAvailable(run) {
  const token = run?.token || {}
  return Boolean(token.total_tokens || token.avg_per_sample || token.total_calls)
}

function buildInsights(runs, context) {
  if (!runs.length) return []
  const ranked = [...runs].sort((a, b) => (metricAverage(b.aggregate?.ex) ?? -1) - (metricAverage(a.aggregate?.ex) ?? -1))
  const leader = ranked[0]
  const trailer = ranked[ranked.length - 1]
  const leaderEx = metricAverage(leader.aggregate?.ex)
  const trailerEx = metricAverage(trailer.aggregate?.ex)
  const lines = [
    `On ${context}, ${leader.method} leads EX at ${percent(leaderEx)} (${leader.aggregate?.ex?.pass_count ?? '—'}/${leader.sample_count ?? '—'}).`,
  ]
  if (ranked.length > 1 && leaderEx != null && trailerEx != null) {
    lines.push(`EX spread vs last place (${trailer.method}) is ${pp(leaderEx - trailerEx)} — amplified below with zoomed axes.`)
  }
  const latencies = ranked.map(run => ({ method: run.method, mean: run.latency?.mean_s })).filter(item => item.mean != null)
  if (latencies.length > 1) {
    const fastest = [...latencies].sort((a, b) => a.mean - b.mean)[0]
    const slowest = [...latencies].sort((a, b) => b.mean - a.mean)[0]
    lines.push(`Latency: ${fastest.method} is fastest at ${num(fastest.mean)} s mean; ${slowest.method} is ${num(slowest.mean / Math.max(fastest.mean, 1e-6), 2)}× slower (${num(slowest.mean)} s).`)
  }
  const emLeader = [...ranked].sort((a, b) => (metricAverage(b.aggregate?.em) ?? -1) - (metricAverage(a.aggregate?.em) ?? -1))[0]
  if (emLeader && emLeader.method !== leader.method) {
    lines.push(`Exact-match champion differs: ${emLeader.method} leads EM at ${percent(metricAverage(emLeader.aggregate?.em))} while ${leader.method} leads EX.`)
  }
  const missingTokens = ranked.filter(run => !tokenAvailable(run)).map(run => run.method)
  if (missingTokens.length) {
    lines.push(`Token counters are zero/unavailable in these score bundles (${missingTokens.join(', ')}); cost view falls back to latency and stage timing.`)
  }
  return lines
}

function RadarChart({ runs, amplify = true }) {
  const size = 340
  const cx = size / 2
  const cy = size / 2
  const radius = 112
  const levels = [0.25, 0.5, 0.75, 1]
  const angleStep = 360 / QUALITY_METRICS.length
  const scalers = Object.fromEntries(QUALITY_METRICS.map(metric => {
    const values = runs.map(run => metricAverage(run.aggregate?.[metric.id]))
    return [metric.id, amplify ? amplifyScaler(values) : { scale: v => Math.max(0, Math.min(1, v ?? 0)), floor: 0, ceil: 1 }]
  }))

  return (
    <div className="board-chart">
      <div className="chart-caption">Zoomed radar · each axis scaled to the observed band so gaps expand</div>
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Amplified SQL quality radar">
        {levels.map(level => {
          const ring = QUALITY_METRICS.map((_, index) => polar(cx, cy, radius * level, index * angleStep).join(',')).join(' ')
          return <polygon key={level} points={ring} className="radar-grid" />
        })}
        {QUALITY_METRICS.map((metric, index) => {
          const [x, y] = polar(cx, cy, radius, index * angleStep)
          const [lx, ly] = polar(cx, cy, radius + 24, index * angleStep)
          const scaler = scalers[metric.id]
          return (
            <g key={metric.id}>
              <line x1={cx} y1={cy} x2={x} y2={y} className="radar-axis" />
              <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle" className="radar-label">{metric.label}</text>
              <text x={polar(cx, cy, radius * 0.18, index * angleStep)[0]} y={polar(cx, cy, radius * 0.18, index * angleStep)[1]} textAnchor="middle" dominantBaseline="middle" className="radar-band">{percent(scaler.floor, 0)}</text>
            </g>
          )
        })}
        {runs.map((run, index) => {
          const points = QUALITY_METRICS.map((metric, metricIndex) => {
            const value = metricAverage(run.aggregate?.[metric.id])
            return polar(cx, cy, radius * scalers[metric.id].scale(value), metricIndex * angleStep).join(',')
          }).join(' ')
          return (
            <g key={run.run_id || run.method}>
              <polygon points={points} fill={colorFor(index)} fillOpacity="0.14" stroke={colorFor(index)} strokeWidth="2.4" />
              {QUALITY_METRICS.map((metric, metricIndex) => {
                const value = metricAverage(run.aggregate?.[metric.id])
                const [x, y] = polar(cx, cy, radius * scalers[metric.id].scale(value), metricIndex * angleStep)
                return <circle key={`${run.method}-${metric.id}`} cx={x} cy={y} r="3.4" fill={colorFor(index)} />
              })}
            </g>
          )
        })}
      </svg>
      <div className="board-legend">
        {runs.map((run, index) => <span key={run.run_id || run.method}><i style={{ background: colorFor(index) }} />{run.method}</span>)}
      </div>
    </div>
  )
}

function DeltaHeatmap({ runs }) {
  const leader = Object.fromEntries(TABLE_METRICS.map(metric => {
    const values = runs.map(run => metricAverage(run.aggregate?.[metric])).filter(value => value != null)
    return [metric, values.length ? Math.max(...values) : null]
  }))

  return (
    <div className="results-table-wrap">
      <table className="results-table board-table delta-table">
        <thead>
          <tr>
            <th>Method</th>
            {TABLE_METRICS.map(metric => <th key={metric}>{metric.toUpperCase()} Δ</th>)}
          </tr>
        </thead>
        <tbody>
          {runs.map((run, index) => (
            <tr key={run.run_id || run.method}>
              <th><b><i style={{ background: colorFor(index) }} />{run.method}</b></th>
              {TABLE_METRICS.map(metric => {
                const value = metricAverage(run.aggregate?.[metric])
                const delta = value == null || leader[metric] == null ? null : value - leader[metric]
                const intensity = delta == null ? 0 : Math.min(1, Math.abs(delta) / 0.2)
                const bg = delta == null ? undefined : delta === 0
                  ? 'rgba(142,225,174,.18)'
                  : delta < 0
                    ? `rgba(223,103,94,${0.12 + intensity * 0.45})`
                    : `rgba(142,225,174,${0.12 + intensity * 0.35})`
                return <td key={metric} style={{ background: bg }} className={delta === 0 ? 'best-cell' : ''}>{delta == null ? '—' : delta === 0 ? 'best' : pp(delta)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="chart-caption">Heatmap vs best method on each metric · red = deficit, green = lead</p>
    </div>
  )
}

function GapBars({ runs, metric = 'ex', label = 'EX' }) {
  const ranked = [...runs].sort((a, b) => (metricAverage(b.aggregate?.[metric]) ?? -1) - (metricAverage(a.aggregate?.[metric]) ?? -1))
  const best = metricAverage(ranked[0]?.aggregate?.[metric])
  const width = 520
  const rowH = 34
  const height = 28 + ranked.length * rowH
  const left = 110
  const right = 70
  const plotW = width - left - right
  const maxGap = Math.max(...ranked.map(run => Math.abs((metricAverage(run.aggregate?.[metric]) ?? best) - best)), 0.01)

  return (
    <div className="board-chart wide">
      <div className="chart-caption">{label} gap to leader · bar length = absolute pp difference</div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} gap bars`}>
        {ranked.map((run, index) => {
          const value = metricAverage(run.aggregate?.[metric])
          const gap = value == null || best == null ? 0 : best - value
          const barW = (Math.abs(gap) / maxGap) * plotW
          const y = 18 + index * rowH
          const color = colorFor(methodIndex(runs, run.method))
          return (
            <g key={run.method}>
              <text x={left - 10} y={y + 12} textAnchor="end" className="bar-label">{run.method}</text>
              <rect x={left} y={y} width={Math.max(barW, gap === 0 ? 4 : 0)} height={18} rx="3" fill={color} opacity={gap === 0 ? 0.95 : 0.85} />
              <text x={left + Math.max(barW, 8) + 8} y={y + 13} className="bar-tick">{gap === 0 ? 'leader' : pp(-gap)}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function LatencyBars({ runs }) {
  const values = runs.map(run => run.latency?.mean_s).filter(value => value != null)
  if (!values.length) return <EmptyBlock title="No latency samples" detail="act_elapsed_s was not present in these score bundles." />
  const max = Math.max(...values)
  const width = 520
  const rowH = 38
  const height = 24 + runs.length * rowH
  const left = 110
  const plotW = 320

  return (
    <div className="board-chart wide">
      <div className="chart-caption">Mean sample latency · FinSQL is ~2× faster on this bundle</div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Latency comparison">
        {runs.map((run, index) => {
          const mean = run.latency?.mean_s
          const p95 = run.latency?.p95_s
          const y = 16 + index * rowH
          const barW = mean == null ? 0 : (mean / max) * plotW
          return (
            <g key={run.method}>
              <text x={left - 10} y={y + 14} textAnchor="end" className="bar-label">{run.method}</text>
              <rect x={left} y={y} width={barW} height={20} rx="3" fill={colorFor(index)} />
              <text x={left + barW + 8} y={y + 14} className="bar-tick">{mean == null ? '—' : `${num(mean)} s · p95 ${num(p95)}`}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function ComponentF1Bars({ runs }) {
  const width = 600
  const height = 250
  const padding = { top: 16, right: 28, bottom: 36, left: 44 }
  const plotW = width - padding.left - padding.right
  const plotH = height - padding.top - padding.bottom
  const groupW = plotW / CF1_KEYS.length
  const barW = Math.min(14, (groupW * 0.72) / Math.max(runs.length, 1))
  const values = runs.flatMap(run => CF1_KEYS.map(key => metricAverage(run.aggregate?.cf1?.[`cf1_${key}`])))
  const scaler = amplifyScaler(values, { padRatio: 0.25, minSpan: 0.05 })

  return (
    <div className="board-chart board-chart-f1">
      <div className="chart-caption">Component F1 · zoomed y-axis ({percent(scaler.floor, 0)}–{percent(scaler.ceil, 0)})</div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Component F1 bars" preserveAspectRatio="xMidYMid meet">
        {[0, 0.5, 1].map(tick => {
          const y = padding.top + plotH * (1 - tick)
          const label = scaler.floor + scaler.span * tick
          return (
            <g key={tick}>
              <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} className="bar-grid" />
              <text x={padding.left - 8} y={y} textAnchor="end" dominantBaseline="middle" className="bar-tick">{percent(label, 0)}</text>
            </g>
          )
        })}
        {CF1_KEYS.map((key, metricIndex) => {
          const gx = padding.left + groupW * metricIndex + groupW / 2
          return (
            <g key={key}>
              <text x={gx} y={height - 12} textAnchor="middle" className="bar-label">{key}</text>
              {runs.map((run, runIndex) => {
                const value = metricAverage(run.aggregate?.cf1?.[`cf1_${key}`])
                const h = plotH * scaler.scale(value)
                const x = gx - (runs.length * barW) / 2 + runIndex * barW
                return <rect key={`${run.method}-${key}`} x={x} y={padding.top + plotH - h} width={barW - 1.5} height={Math.max(h, 0)} fill={colorFor(runIndex)} rx="2" />
              })}
            </g>
          )
        })}
      </svg>
      <div className="board-legend">
        {runs.map((run, index) => <span key={run.method}><i style={{ background: colorFor(index) }} />{run.method}</span>)}
      </div>
    </div>
  )
}

function FeatureMatrix({ runs }) {
  const features = [...new Set(runs.flatMap(run => Object.keys(run.by_sql_feature || {})))]
    .filter(feature => runs.some(run => (run.by_sql_feature?.[feature]?.count || 0) > 0))
    .sort()
  if (!features.length) return <EmptyBlock title="No SQL-feature slices" detail="by_sql_feature was empty in these bundles." />
  return (
    <div className="results-table-wrap">
      <table className="results-table board-table feature-heat">
        <thead>
          <tr>
            <th>SQL feature</th>
            {runs.map((run, index) => <th key={run.method}><i style={{ background: colorFor(index) }} />{run.method}<small>n · EX</small></th>)}
            <th>Δ EX max-min</th>
          </tr>
        </thead>
        <tbody>
          {features.map(feature => {
            const exValues = runs.map(run => run.by_sql_feature?.[feature]?.ex).filter(value => value != null)
            const spread = exValues.length ? Math.max(...exValues) - Math.min(...exValues) : null
            return (
              <tr key={feature}>
                <th>{feature}</th>
                {runs.map(run => {
                  const cell = run.by_sql_feature?.[feature]
                  const ex = cell?.ex
                  const intensity = ex == null ? 0 : ex
                  return <td key={run.method} style={{ background: ex == null ? undefined : `rgba(142,225,174,${0.05 + intensity * 0.35})` }}>{cell?.count || 0} · {percent(ex)}</td>
                })}
                <td className={spread != null && spread >= 0.15 ? 'hot-gap' : ''}>{spread == null ? '—' : pp(spread).replace('+', '')}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ErrorCompare({ runs }) {
  const names = [...new Set(runs.flatMap(run => Object.keys(run.errors || {})))]
    .sort((a, b) => {
      const total = name => runs.reduce((sum, run) => sum + (run.errors?.[name]?.count || 0), 0)
      return total(b) - total(a) || a.localeCompare(b)
    })
    .slice(0, 8)
  if (!names.length) return <EmptyBlock title="No classified errors" detail="error_root_distribution is empty." />
  return (
    <div className="error-compare">
      {names.map(name => {
        const max = Math.max(...runs.map(run => run.errors?.[name]?.count || 0), 1)
        return (
          <div key={name} className="error-row">
            <span>{name.replaceAll('_', ' ')}</span>
            <div>
              {runs.map((run, index) => {
                const count = run.errors?.[name]?.count || 0
                return <em key={run.method} title={`${run.method}: ${count}`} style={{ width: `${(count / max) * 100}%`, background: colorFor(index) }}><b>{count || ''}</b></em>
              })}
            </div>
          </div>
        )
      })}
      <div className="board-legend">
        {runs.map((run, index) => <span key={run.method}><i style={{ background: colorFor(index) }} />{run.method}</span>)}
      </div>
    </div>
  )
}

function ScenarioCards({ runs }) {
  const scenarios = [...new Set(runs.flatMap(run => Object.keys(run.by_scenario || {})))].sort()
  if (!scenarios.length) return null
  return (
    <div className="scenario-grid">
      {scenarios.map(scenario => {
        const values = runs.map(run => ({
          method: run.method,
          ex: run.by_scenario?.[scenario]?.ex,
          count: run.by_scenario?.[scenario]?.count,
        }))
        const exes = values.map(item => item.ex).filter(value => typeof value === 'number')
        const best = exes.length ? Math.max(...exes) : null
        return (
          <article key={scenario}>
            <header>{scenario.replaceAll('_', ' ')}</header>
            {values.map(item => {
              const delta = item.ex == null || best == null ? null : item.ex - best
              return (
                <div key={item.method} className="scenario-row">
                  <span><i style={{ background: colorFor(methodIndex(runs, item.method)) }} />{item.method}</span>
                  <b>{percent(item.ex)}</b>
                  <small>{delta === 0 ? 'best' : pp(delta)} · n={item.count ?? '—'}</small>
                </div>
              )
            })}
          </article>
        )
      })}
    </div>
  )
}

function AgentReport({ runs, configs, dataset, insights }) {
  return (
    <section className="tool-panel board-panel report-panel">
      <div className="panel-title">
        <div>
          <span>Agent implementation report</span>
          <small>Native Actor workflow · bottlenecks · stage signals from score bundles</small>
        </div>
      </div>
      <div className="insight-list">
        {insights.map(line => <p key={line}>{line}</p>)}
      </div>
      <div className="agent-report-grid">
        {runs.map((run, index) => {
          const config = configs.find(item => item.dataset === dataset && item.method === run.method)
          const stages = run.workflow?.aggregate?.stage_summary || {}
          const bottlenecks = run.workflow?.aggregate?.bottleneck_distribution || {}
          const workflowPath = (run.workflow?.workflows || []).map(item => (item.stages || []).join(' → ')).filter(Boolean)
          const totalBottleneck = Object.values(bottlenecks).reduce((sum, value) => sum + (Number(value) || 0), 0) || 1
          return (
            <article key={run.run_id || run.method} className="agent-card">
              <header>
                <b><i style={{ background: colorFor(index) }} />{run.method}</b>
                <code>{run.run_id}</code>
              </header>
              <div className="agent-meta">
                <span>Config</span><strong>{config?.config_path || 'artifact-only'}</strong>
                <span>Provider</span><strong>{config ? `${config.provider}/${config.model}` : 'from score bundle'}</strong>
                <span>Workflow</span><strong>{workflowPath.join(' · ') || (config?.stages || []).map(stage => stage.actor || stage.type || stage.id).join(' → ') || '—'}</strong>
              </div>
              <div className="bottleneck-strip" aria-label={`${run.method} bottleneck`}>
                {Object.entries(bottlenecks).map(([name, count]) => (
                  <i key={name} style={{ flexGrow: count }} title={`${name}: ${count}`}>
                    <span>{count / totalBottleneck >= 0.12 ? `${name} ${count}` : ''}</span>
                  </i>
                ))}
              </div>
              <div className="stage-report">
                {Object.entries(stages).map(([id, stage]) => (
                  <div key={id}>
                    <div className="stage-head">
                      <b>{stage.actor_class || id}</b>
                      <small>{stage.task_type || id}</small>
                    </div>
                    <div className="stage-counts">
                      <span>{stage.status_counts?.pass ?? 0} pass</span>
                      <span>{stage.status_counts?.fail ?? 0} fail</span>
                      <span>{stage.status_counts?.observed ?? 0} observed</span>
                    </div>
                    <dl>
                      {Object.entries(stage.signals || {}).slice(0, 6).map(([name, value]) => (
                        <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{typeof value === 'number' ? num(value, 3) : String(value)}</dd></div>
                      ))}
                      {Object.entries(run.latency?.by_stage || {}).filter(([stageId]) => stageId === id || stageId.includes(id) || id.includes(stageId)).slice(0, 2).map(([stageId, value]) => (
                        <div key={`lat-${stageId}`}><dt>{stageId} latency</dt><dd>{num(value.mean_s)} s mean · p95 {num(value.p95_s)}</dd></div>
                      ))}
                    </dl>
                  </div>
                ))}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function FormalAggregate({ runs }) {
  const metrics = ['ex', 'em', 'sf1', 'sc', 'ves', 'rves']
  return (
    <>
      <div className="results-table-wrap">
        <table className="results-table">
          <thead>
            <tr>
              <th>Method</th>
              {metrics.map(metric => <th key={metric}>{metric.toUpperCase()}</th>)}
              <th>Valid</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr key={run.run_id}>
                <th><b>{run.method}</b><small>{run.scope} · n={run.sample_count}</small></th>
                {metrics.map(metric => <td key={metric}>{percent(metricAverage(run.aggregate?.[metric]))}</td>)}
                <td>{run.aggregate?.ex?.valid ?? 'Unavailable'} / {run.aggregate?.ex?.total ?? run.sample_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="diagnostic-grid">
        {runs.map(run => (
          <section key={run.run_id}>
            <h3>{run.method} component and pipeline diagnostics</h3>
            <div>
              {diagnosticEntries(run.aggregate).map(([label, value, kind]) => (
                <span key={label}>
                  <small>{label}</small>
                  <b>{kind === 'percent' ? percent(value) : value == null ? 'Unavailable' : Number(value).toFixed(3)}</b>
                </span>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  )
}

function FormalWorkflow({ runs }) {
  return (
    <div className="workflow-comparison">
      {runs.map(run => {
        const summary = run.workflow?.aggregate?.stage_summary || {}
        const stages = Object.keys(summary).length
          ? summary
          : Object.fromEntries(Object.entries(run.stage_metrics || {}).map(([id, stage]) => [id, { ...stage, status_counts: {}, actor_class: stage.actor_class }]))
        const bottlenecks = run.workflow?.aggregate?.bottleneck_distribution || {}
        const total = Object.values(bottlenecks).reduce((sum, value) => sum + (Number(value) || 0), 0)
        return (
          <section key={run.run_id}>
            <header>
              <div>
                <b>{run.method}</b>
                <small>{run.workflow?.workflows?.map(item => item.stages?.join(' → ')).join(' · ') || 'Workflow trace unavailable'}</small>
              </div>
              <code>{run.run_id}</code>
            </header>
            <div className="bottleneck-strip" aria-label={`${run.method} bottleneck distribution`}>
              {Object.entries(bottlenecks).map(([name, count]) => (
                <i key={name} style={{ flexGrow: count }} title={`${name}: ${count}`}>
                  <span>{total && count / total >= 0.12 ? name : ''}</span>
                </i>
              ))}
            </div>
            <div className="stage-grid">
              {Object.entries(stages).map(([id, stage]) => (
                <article key={id}>
                  <div>
                    <b>{stage.actor_class || id}</b>
                    <small>{stage.task_type || id}</small>
                  </div>
                  <span className="stage-counts">
                    <i>{stage.status_counts?.pass || 0} pass</i>
                    <i>{stage.status_counts?.fail || 0} fail</i>
                    <i>{stage.status_counts?.observed || 0} observed</i>
                  </span>
                  <dl>
                    {Object.entries(stage.metrics || {}).map(([name, value]) => (
                      <div key={name}>
                        <dt>{name.replaceAll('_', ' ')}</dt>
                        <dd>{typeof value === 'number' ? Number(value).toFixed(3) : 'Unavailable'}</dd>
                      </div>
                    ))}
                  </dl>
                </article>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function FormalSql({ runs }) {
  const features = [...new Set(runs.flatMap(run => Object.keys(run.by_sql_feature || {})))]
    .filter(feature => runs.some(run => run.by_sql_feature?.[feature]?.count))
    .sort()
  return (
    <>
      <div className="results-table-wrap">
        <table className="results-table feature-table">
          <thead>
            <tr>
              <th>SQL feature</th>
              {runs.map(run => <th key={run.run_id || run.method}>{run.method}<small>count · EX · SF1</small></th>)}
            </tr>
          </thead>
          <tbody>
            {features.map(feature => (
              <tr key={feature}>
                <th>{feature}</th>
                {runs.map(run => {
                  const value = run.by_sql_feature?.[feature]
                  return <td key={run.run_id || run.method}>{value?.count || 0} · {percent(value?.ex)} · {percent(value?.sf1)}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="qvt-grid">
        {runs.map(run => (
          <section key={run.run_id || run.method}>
            <h3>{run.method} QVT consistency</h3>
            {run.qvt?.eligible_groups ? (
              <dl>
                <div><dt>Eligible groups</dt><dd>{run.qvt.eligible_groups}</dd></div>
                <div><dt>Flip rate</dt><dd>{percent(run.qvt.flip_rate)}</dd></div>
                <div><dt>Stable groups</dt><dd>{percent(run.qvt.stable_group_rate)}</dd></div>
                <div><dt>Group EX</dt><dd>{percent(run.qvt.avg_group_exec_acc)}</dd></div>
              </dl>
            ) : <span>Not eligible on this sample</span>}
          </section>
        ))}
      </div>
    </>
  )
}

function FormalRuntime({ runs }) {
  const errorNames = [...new Set(runs.flatMap(run => Object.keys(run.errors || {})))].sort()
  return (
    <>
      <div className="results-table-wrap">
        <table className="results-table">
          <thead>
            <tr>
              <th>Method</th>
              <th>Tokens / sample</th>
              <th>Prompt</th>
              <th>Completion</th>
              <th>Latency mean</th>
              <th>P50</th>
              <th>P95</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr key={run.run_id || run.method}>
                <th>{run.method}</th>
                <td>{compactNumber(run.token?.avg_per_sample)}</td>
                <td>{compactNumber(run.token?.total_prompt_tokens)}</td>
                <td>{compactNumber(run.token?.total_completion_tokens)}</td>
                <td>{seconds(run.latency?.mean_s)}</td>
                <td>{seconds(run.latency?.p50_s)}</td>
                <td>{seconds(run.latency?.p95_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="runtime-grid">
        {runs.map(run => (
          <section key={run.run_id || run.method}>
            <h3>{run.method} token and stage latency</h3>
            <div className="runtime-rows">
              {Object.entries(run.token?.by_step || {}).map(([name, value]) => (
                <div key={`token-${name}`}><span>{name}</span><b>{compactNumber(value.total_tokens)} tokens</b></div>
              ))}
              {Object.entries(run.latency?.by_stage || {}).map(([name, value]) => (
                <div key={`latency-${name}`}><span>{name}</span><b>{seconds(value.mean_s)} mean · {seconds(value.p95_s)} p95</b></div>
              ))}
            </div>
          </section>
        ))}
      </div>
      <div className="results-table-wrap">
        <table className="results-table error-table">
          <thead>
            <tr>
              <th>Error root</th>
              {runs.map(run => <th key={run.run_id || run.method}>{run.method}</th>)}
            </tr>
          </thead>
          <tbody>
            {errorNames.length ? errorNames.map(name => (
              <tr key={name}>
                <th>{name.replaceAll('_', ' ')}</th>
                {runs.map(run => <td key={run.run_id || run.method}>{run.errors?.[name]?.count || 0} · {percent(run.errors?.[name]?.pct)}</td>)}
              </tr>
            )) : (
              <tr><td colSpan={runs.length + 1}>No classified execution failures</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}

function FormalResults({ runs, context, alignment, Status }) {
  const [view, setView] = useState('aggregate')
  const tabs = [
    ['aggregate', 'Aggregate'],
    ['workflow', 'Stage & Workflow'],
    ['sql', 'SQL Feature & QVT'],
    ['runtime', 'Runtime & Errors'],
  ]
  return (
    <section className="tool-panel evaluation-results board-panel board-panel-wide">
      <div className="results-head">
        <div>
          <span>Formal experiment results</span>
          <small>{context}</small>
        </div>
        <Status tone={alignment?.aligned ? 'success' : runs.length ? 'warning' : 'neutral'}>
          {alignment?.aligned ? `${alignment.sample_count} samples aligned` : `${runs.length} methods loaded`}
        </Status>
      </div>
      <div className="results-tabs" role="tablist" aria-label="Formal result views">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={view === id} className={view === id ? 'active' : ''} onClick={() => setView(id)}>{label}</button>
        ))}
      </div>
      <div className="results-body">
        {view === 'aggregate' && <FormalAggregate runs={runs} />}
        {view === 'workflow' && <FormalWorkflow runs={runs} />}
        {view === 'sql' && <FormalSql runs={runs} />}
        {view === 'runtime' && <FormalRuntime runs={runs} />}
      </div>
    </section>
  )
}

function ScoreBundlePanel({ runs }) {
  const [openId, setOpenId] = useState('')
  return (
    <section className="tool-panel board-panel">
      <div className="panel-title">
        <div>
          <span>Original score bundles</span>
          <small>Raw aggregate metrics · validity · token counters · run identity</small>
        </div>
      </div>
      <div className="score-card-grid">
        {runs.map((run, index) => {
          const token = run.token || {}
          const open = openId === run.run_id
          return (
            <article key={run.run_id || run.method} className="score-card">
              <header>
                <b><i style={{ background: colorFor(index) }} />{run.method}</b>
                <StatusPill tone={tokenAvailable(run) ? 'success' : 'warning'}>{tokenAvailable(run) ? 'tokens present' : 'tokens=0'}</StatusPill>
              </header>
              <code>{run.run_id}</code>
              <div className="score-metrics">
                {TABLE_METRICS.map(metric => {
                  const block = run.aggregate?.[metric] || {}
                  return (
                    <div key={metric}>
                      <span>{metric.toUpperCase()}</span>
                      <b>{percent(metricAverage(block))}</b>
                      <small>{block.valid ?? '—'}/{block.total ?? run.sample_count ?? '—'} valid{block.note ? ` · ${block.note}` : ''}</small>
                    </div>
                  )
                })}
              </div>
              <div className="score-runtime">
                <div><span>Tokens total</span><b>{compact(token.total_tokens)}</b></div>
                <div><span>Avg / sample</span><b>{compact(token.avg_per_sample)}</b></div>
                <div><span>Prompt / completion</span><b>{compact(token.total_prompt_tokens)} / {compact(token.total_completion_tokens)}</b></div>
                <div><span>Latency mean / p50 / p95</span><b>{num(run.latency?.mean_s)} / {num(run.latency?.p50_s)} / {num(run.latency?.p95_s)} s</b></div>
                <div><span>Sample hash</span><b>{run.sample_hash || '—'}</b></div>
                <div><span>Source</span><b>{run.source} · {run.scope || '—'}</b></div>
              </div>
              <button className="button compact secondary" onClick={() => setOpenId(open ? '' : run.run_id)}>
                {open ? 'Hide raw aggregate JSON' : 'Show raw aggregate JSON'}
              </button>
              {open && <pre className="raw-json">{JSON.stringify({
                run_id: run.run_id,
                method: run.method,
                dataset: run.dataset,
                split: run.split,
                sample_count: run.sample_count,
                sampling: run.sampling,
                aggregate: run.aggregate,
                token: run.token,
                latency: run.latency,
                errors: run.errors,
                qvt: run.qvt,
              }, null, 2)}</pre>}
            </article>
          )
        })}
      </div>
    </section>
  )
}

function StatusPill({ tone = 'neutral', children }) {
  return <span className={`status status-${tone}`}><i />{children}</span>
}

function EmptyBlock({ title, detail }) {
  return <div className="empty"><strong>{title}</strong>{detail && <span>{detail}</span>}</div>
}

function LeaderboardTable({ runs }) {
  const ranked = [...runs].sort((a, b) => (metricAverage(b.aggregate?.ex) ?? -1) - (metricAverage(a.aggregate?.ex) ?? -1))
  const best = Object.fromEntries(TABLE_METRICS.map(metric => [
    metric,
    Math.max(...ranked.map(run => metricAverage(run.aggregate?.[metric]) ?? -Infinity)),
  ]))
  return (
    <div className="results-table-wrap">
      <table className="results-table board-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Method</th>
            {TABLE_METRICS.map(metric => <th key={metric}>{metric.toUpperCase()}</th>)}
            <th>Latency</th>
            <th>Tokens</th>
            <th>n</th>
          </tr>
        </thead>
        <tbody>
          {ranked.map((run, index) => (
            <tr key={run.run_id || run.method}>
              <td><span className="rank-pill" style={{ borderColor: colorFor(methodIndex(runs, run.method)) }}>{index + 1}</span></td>
              <th>
                <b><i style={{ background: colorFor(methodIndex(runs, run.method)) }} />{run.method}</b>
                <small>{run.scope || run.sampling?.mode || 'run'} · {run.run_id}</small>
              </th>
              {TABLE_METRICS.map(metric => {
                const value = metricAverage(run.aggregate?.[metric])
                const isBest = value != null && value === best[metric] && Number.isFinite(best[metric])
                return <td key={metric} className={isBest ? 'best-cell' : ''}>{percent(value)}</td>
              })}
              <td>{run.latency?.mean_s == null ? '—' : `${num(run.latency.mean_s)} s`}</td>
              <td>{tokenAvailable(run) ? compact(run.token?.avg_per_sample) : '0 / n/a'}</td>
              <td>{run.sample_count ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HostedEvaluationControls({ enabled, children }) {
  if (enabled) return children
  return <div className="hosted-readonly-note">
    Recorded benchmark evidence is available here. Start new reproduce evaluations from a local SqurveBridge checkout.
  </div>
}

export default function ExperimentBoard({
  capabilities,
  liveEvaluation = true,
  api,
  postJson,
  Status,
  PageHeading,
  Empty,
  viewOnly = false,
  initialDataset,
  initialMethods,
  initialSplit,
  initialSampleLimit,
  initialSampleMode,
  initialSampleSeed,
  archiveRunId = '',
  embedded = false,
}) {
  const configs = capabilities?.reproduce_configs || []
  const benchmarks = useMemo(() => {
    const registered = capabilities?.benchmarks || []
    if (registered.length) {
      return [...registered].sort((a, b) => a.id.localeCompare(b.id)).map(item => ({
        dataset: item.id,
        splits: item.splits || ['dev'],
        defaultSplit: item.default_split || item.splits?.[0] || 'dev',
      }))
    }
    return [...new Set(configs.map(item => item.dataset))].sort().map(dataset => ({
      dataset,
      splits: [...new Set(configs.filter(item => item.dataset === dataset).map(item => item.split))],
      defaultSplit: configs.find(item => item.dataset === dataset)?.split || 'dev',
    }))
  }, [capabilities, configs])

  const [dataset, setDataset] = useState(initialDataset || 'spider')
  const [split, setSplit] = useState(initialSplit || 'dev')
  const [methods, setMethods] = useState(initialMethods?.length ? initialMethods : ['c3sql'])
  const [sampleLimit, setSampleLimit] = useState(initialSampleLimit ?? 100)
  const [sampleMode, setSampleMode] = useState(initialSampleMode || 'random')
  const [sampleSeed, setSampleSeed] = useState(initialSampleSeed ?? 42)
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [comparisonId, setComparisonId] = useState('')
  const [section, setSection] = useState('compare')

  useEffect(() => {
    if (initialDataset) setDataset(initialDataset)
  }, [initialDataset])

  useEffect(() => {
    if (initialMethods?.length) setMethods(initialMethods)
  }, [(initialMethods || []).join('|')])

  useEffect(() => {
    if (initialSampleLimit != null) setSampleLimit(initialSampleLimit)
  }, [initialSampleLimit])

  useEffect(() => {
    if (initialSampleMode) setSampleMode(initialSampleMode)
  }, [initialSampleMode])

  useEffect(() => {
    if (initialSampleSeed != null) setSampleSeed(initialSampleSeed)
  }, [initialSampleSeed])

  const datasetMethods = useMemo(
    () => [...new Set(configs.filter(item => item.dataset === dataset).map(item => item.method))].sort(),
    [configs, dataset],
  )
  const currentBenchmark = benchmarks.find(item => item.dataset === dataset)
  const runnablePairs = methods
    .filter(method => configs.some(item => item.dataset === dataset && item.method === method))
    .map(method => ({ dataset, method }))

  useEffect(() => {
    if (!benchmarks.some(item => item.dataset === dataset)) setDataset(benchmarks[0]?.dataset || 'spider')
  }, [benchmarks.map(item => item.dataset).join('|'), dataset])

  useEffect(() => {
    const nextSplit = currentBenchmark?.defaultSplit || currentBenchmark?.splits?.[0] || 'dev'
    if (!(currentBenchmark?.splits || []).includes(split)) setSplit(nextSplit)
  }, [dataset, currentBenchmark?.defaultSplit, (currentBenchmark?.splits || []).join('|'), split])

  useEffect(() => {
    if (!datasetMethods.length) return
    setMethods(current => {
      const kept = current.filter(method => datasetMethods.includes(method))
      if (kept.length >= 2) return kept
      return datasetMethods.slice(0, Math.min(3, datasetMethods.length))
    })
  }, [dataset, datasetMethods.join('|')])

  const load = async () => {
    if (methods.length < 1 && !archiveRunId) return
    setBusy(true)
    setError('')
    try {
      if (archiveRunId) {
        const detail = await api(`/api/archive/${encodeURIComponent(archiveRunId)}`)
        const scoresFile = detail?.files?.find(item => item.name === 'scores.json')
        const hosted = capabilities?.deployment?.target === 'hf-space'
        let scores = detail?.scores || (hosted ? {
          method: detail?.method,
          dataset: detail?.dataset,
          split: detail?.split,
          sample_count: detail?.sample_count,
          aggregate: detail?.metrics || {},
        } : null)
        if (!hosted && !scores && scoresFile) {
          const file = await api(`/api/archive/${encodeURIComponent(archiveRunId)}/files/${scoresFile.path}`)
          scores = file?.json || null
        }
        if (scores) {
          scores = normalizeScoreBundle(scores)
          setPayload({
            runs: [{
              ...scores,
              run_id: detail.run_id || archiveRunId,
              method: detail.method || scores.method,
              dataset: detail.dataset || scores.dataset,
              split: detail.split || scores.split,
              sample_count: detail.sample_count ?? scores.sample_count,
              aggregate: scores.aggregate || detail.metrics,
              evidence_origin: 'historical-archive',
              source: detail.source,
            }],
            sample_alignment: { aligned: false },
          })
        } else {
          setPayload({ runs: [], sample_alignment: { aligned: false } })
          setError('This archive run does not include a scores.json bundle.')
        }
        return
      }
      const params = new URLSearchParams({
        dataset,
        split,
        methods: methods.join(','),
        sample_mode: sampleMode,
        sample_limit: String(sampleLimit),
        sample_seed: String(sampleSeed),
      })
      const requests = [api(`/api/comparisons/latest/results?${params}`)]
      if (comparisonId) requests.push(api(`/api/comparisons/${comparisonId}/results`).catch(() => null))
      const [artifacts, session] = await Promise.all(requests)
      const selected = artifacts?.runs?.length ? artifacts : session?.runs?.length ? session : artifacts
      setPayload(selected)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { load() }, [dataset, split, methods.join(','), sampleMode, sampleLimit, sampleSeed, comparisonId, archiveRunId])

  const toggleMethod = method => {
    setMethods(current => {
      if (current.includes(method)) return current.length <= 1 ? current : current.filter(item => item !== method)
      return current.length >= 6 ? current : [...current, method]
    })
  }

  const runComparison = async () => {
    if (runnablePairs.length < 2) {
      setError('Select at least two methods that already have reproduce configs on this dataset.')
      return
    }
    setRunning(true)
    setError('')
    try {
      const data = await postJson('/api/comparisons', {
        pairs: runnablePairs,
        sample_limit: sampleLimit,
        sample_mode: sampleMode,
        sample_seed: sampleSeed,
      })
      setComparisonId(data.comparison_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  const runs = payload?.runs || []
  const alignment = payload?.sample_alignment
  const context = `${dataset} · ${split} · ${sampleMode} ${sampleLimit}${sampleMode === 'random' ? ` · seed ${sampleSeed}` : ''}`
  const insights = useMemo(() => buildInsights(runs, context), [runs, context])
  const tabs = [
    ['compare', 'Amplified compare'],
    ['formal', 'Formal tables'],
    ['diagnose', 'Errors & features'],
    ['report', 'Agent report'],
    ['scores', 'Raw scores'],
  ]

  return (
    <div className={`workspace board-workspace${embedded ? ' board-workspace-embedded' : ''}`}>
      {!embedded && <PageHeading
        eyebrow="Experiment board"
        title="Method comparison"
        status={<Status tone={alignment?.aligned ? 'success' : runs.length ? 'warning' : 'neutral'}>{alignment?.aligned ? `${alignment.sample_count} samples aligned` : runs.length ? `${runs.length} methods loaded` : 'awaiting artifacts'}</Status>}
      />}

      {!archiveRunId && <section className="tool-panel board-controls">
        <div className="panel-title">
          <div>
            <span>{viewOnly ? 'Visualization focus' : 'Comparison setup'}</span>
            <small>{viewOnly ? 'Browse evaluation and generated-data charts' : 'One dataset · multiple methods · shared sample protocol'}</small>
          </div>
          <Status tone={busy ? 'running' : 'neutral'}>{busy ? 'loading' : context}</Status>
        </div>

        <div className="board-setup">
          <div className="board-setup-row">
            <label className="field">
              <span>Dataset</span>
              <select value={dataset} onChange={event => setDataset(event.target.value)}>
                {benchmarks.map(item => <option key={item.dataset} value={item.dataset}>{item.dataset}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Split</span>
              <select value={split} onChange={event => setSplit(event.target.value)}>
                {(currentBenchmark?.splits || [split]).map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <div className="field">
              <span>Sample size</span>
              <div className="chip-row">
                {[20, 50, 100, 200].map(value => (
                  <button key={value} className={sampleLimit === value ? 'active' : ''} onClick={() => setSampleLimit(value)}>{value}</button>
                ))}
              </div>
            </div>
            <div className="field">
              <span>Sampling</span>
              <div className="sampling-inline">
                <div className="segmented">
                  <button className={sampleMode === 'slice' ? 'active' : ''} onClick={() => setSampleMode('slice')}>Slice</button>
                  <button className={sampleMode === 'random' ? 'active' : ''} onClick={() => setSampleMode('random')}>Random</button>
                </div>
                {sampleMode === 'random' && (
                  <label className="seed-field">
                    <span>Seed</span>
                    <input type="number" value={sampleSeed} onChange={event => setSampleSeed(Number(event.target.value))} />
                  </label>
                )}
              </div>
            </div>
          </div>

          <div className="method-picker">
            <div className="method-picker-head">
              <span>Methods on {dataset}</span>
              <small>{methods.length} / 6 selected</small>
            </div>
            <div className="method-chip-grid">
              {datasetMethods.length ? datasetMethods.map(method => (
                <button key={method} className={methods.includes(method) ? 'active' : ''} onClick={() => toggleMethod(method)} aria-pressed={methods.includes(method)}>{method}</button>
              )) : <Empty title="No registered methods" detail="Integrate a method-dataset reproduce config first." />}
            </div>
          </div>

          <div className="board-actions">
            <button className="button secondary compact" disabled={busy} onClick={load}>{busy ? 'Refreshing…' : 'Refresh artifacts'}</button>
            {!viewOnly && <HostedEvaluationControls enabled={liveEvaluation}>
              <button className="button primary compact" disabled={running || runnablePairs.length < 2} onClick={runComparison}>
                {running ? 'Starting evaluation…' : `Run ${runnablePairs.length} methods on ${dataset}`}
              </button>
            </HostedEvaluationControls>}
          </div>
        </div>
        {error && <p className="error-banner">{error}</p>}
      </section>}

      {archiveRunId && error && <p className="error-banner">{error}</p>}
      {archiveRunId && <div className="archive-focus-banner"><Status tone="success">Archive run · {archiveRunId}</Status></div>}

      {!runs.length ? (
        <section className="tool-panel board-empty">
          <Empty
            title={viewOnly ? 'No evaluation charts yet' : 'No aligned score bundle for this selection'}
            detail={viewOnly
              ? 'Run evaluations on Board, or open a score bundle from Archive.'
              : 'Pick methods with matching artifacts, or run a fresh multi-method evaluation.'}
          />
        </section>
      ) : (
        <div className="board-body">
          <section className="insight-banner">
            <div className="insight-kicker">Reading</div>
            <ol>
              {insights.map(line => <li key={line}>{line}</li>)}
            </ol>
          </section>

          <div className="board-tabs" role="tablist" aria-label="Board sections">
            {tabs.map(([id, label], index) => (
              <button key={id} role="tab" aria-selected={section === id} className={section === id ? 'active' : ''} onClick={() => setSection(id)}>
                <i>{String(index + 1).padStart(2, '0')}</i>{label}
              </button>
            ))}
          </div>

          {section === 'compare' && (
            <div className="board-stack">
              <div className="board-hero">
                <section className="tool-panel board-panel">
                  <div className="panel-title"><div><span>Zoomed quality radar</span><small>Axes cropped to observed range</small></div></div>
                  <RadarChart runs={runs} />
                </section>
                <section className="tool-panel board-panel">
                  <div className="panel-title"><div><span>EX gap to leader</span><small>Absolute percentage-point deficit</small></div></div>
                  <GapBars runs={runs} metric="ex" label="EX" />
                </section>
              </div>

              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>Leaderboard</span><small>Quality · latency · token availability</small></div></div>
                <LeaderboardTable runs={runs} />
              </section>

              <div className="board-split">
                <section className="tool-panel board-panel">
                  <div className="panel-title"><div><span>Delta heatmap</span><small>Every metric vs best method</small></div></div>
                  <DeltaHeatmap runs={runs} />
                </section>
                <section className="tool-panel board-panel">
                  <div className="panel-title"><div><span>Latency contrast</span><small>Mean / p95 from per-sample act time</small></div></div>
                  <LatencyBars runs={runs} />
                </section>
              </div>

              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>Component F1</span><small>Structural gaps amplified</small></div></div>
                <ComponentF1Bars runs={runs} />
              </section>

              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>Scenario slices</span><small>Hard subsets where methods diverge</small></div></div>
                <ScenarioCards runs={runs} />
              </section>
            </div>
          )}

          {section === 'formal' && (
            <div className="board-stack">
              <FormalResults runs={runs} context={context} alignment={alignment} Status={Status} />
            </div>
          )}

          {section === 'diagnose' && (
            <div className="board-stack">
              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>Error attribution</span><small>Top roots · bar length normalized per row</small></div></div>
                <ErrorCompare runs={runs} />
              </section>
              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>SQL feature matrix</span><small>Per-feature EX · highlight large spreads</small></div></div>
                <FeatureMatrix runs={runs} />
              </section>
              <section className="tool-panel board-panel board-panel-wide">
                <div className="panel-title"><div><span>EM gap to leader</span><small>Exact-match differences are larger than EX</small></div></div>
                <GapBars runs={runs} metric="em" label="EM" />
              </section>
            </div>
          )}

          {section === 'report' && (
            <div className="board-stack">
              <AgentReport runs={runs} configs={configs} dataset={dataset} insights={insights} />
            </div>
          )}

          {section === 'scores' && (
            <div className="board-stack">
              <ScoreBundlePanel runs={runs} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
