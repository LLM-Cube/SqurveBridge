import { useId, useRef, useState } from 'react'

// A method keeps its colour in every chart: its place in the full, stable method list, never its rank.
export const METHOD_ORDER = ['c3sql', 'dinsql', 'finsql', 'resdsql', 'e-sql', 'sede', 'unisar', 'gpt-baseline']
const slotOf = id => {
  const index = METHOD_ORDER.indexOf(id)
  return index < 0 ? 8 : index + 1
}
const colorOf = id => `var(--series-${slotOf(id)})`
const INK = 'var(--viz-ink, #2e2618)'
const MUTED = 'var(--viz-ink-muted, #6b6152)'
const SURFACE = 'var(--viz-surface, #fbf8f1)'
const GRID = 'rgb(92 72 48 / 14%)'
const ACCENT = 'var(--flow-claude, #8a5c22)'

const ratio = value => `${(value * 100).toFixed(1)}%`

/** Hover tooltip shared by the marks of one chart; keyboard focus shows it too. */
function useTip() {
  const ref = useRef(null)
  const [tip, setTip] = useState(null)
  const at = (event, text, focus) => {
    const box = ref.current.getBoundingClientRect()
    if (focus) {
      const mark = event.currentTarget.getBoundingClientRect()
      setTip({ x: mark.left - box.left + mark.width / 2, y: mark.top - box.top, text })
    } else setTip({ x: event.clientX - box.left, y: event.clientY - box.top, text })
  }
  const mark = text => ({
    onMouseEnter: event => at(event, text), onMouseMove: event => at(event, text), onMouseLeave: () => setTip(null),
    onFocus: event => at(event, text, true), onBlur: () => setTip(null), tabIndex: 0,
  })
  return { ref, tip, mark }
}

function Frame({ chart, label, legend, children }) {
  return (
    <figure className="showcase-chart" ref={chart.ref}>
      {legend}
      <div className="showcase-chart-plot" role="img" aria-label={label}>{children}</div>
      {chart.tip ? <div className="showcase-tooltip" role="tooltip" style={{ left: chart.tip.x, top: chart.tip.y }}>{chart.tip.text}</div> : null}
    </figure>
  )
}

/** One swatch per method (always for two or more); invalid runs wear the same hatching they have in the plot. */
export function Legend({ methods, t }) {
  return (
    <ul className="showcase-legend" aria-label={t('showcase.metrics.legend')}>
      {methods.map(({ id, valid }) => (
        <li key={id} data-method={id} data-valid={valid}>
          <i className={valid ? '' : 'hatched'} style={{ '--c': colorOf(id) }} aria-hidden="true" />
          {id}{valid ? '' : ` · ${t('showcase.metrics.invalidShort')}`}
        </li>
      ))}
    </ul>
  )
}

/** Diagonal hatching in each method's colour: the mark of a run that is not a method result. */
function Hatches({ uid, methods }) {
  return (
    <defs>
      {methods.map(({ id }) => (
        <pattern key={id} id={`${uid}-h-${id}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill={SURFACE} />
          <rect width="2.4" height="6" fill={colorOf(id)} />
        </pattern>
      ))}
    </defs>
  )
}

const fillOf = (uid, method) => (method.valid ? colorOf(method.id) : `url(#${uid}-h-${method.id})`)

function barPath(x, y, w, h) {
  const r = Math.min(4, h, w / 2)
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}

/** Grouped columns: one group per category, one bar per method; EX bars may carry an interval whisker. */
export function GroupedColumns({ label, groups, methods, t, tipLabel }) {
  const uid = useId().replace(/:/g, '')
  const chart = useTip()
  const W = 700, H = 280, m = { l: 44, r: 10, t: 22, b: 40 }
  const pw = W - m.l - m.r, ph = H - m.t - m.b
  const y = value => m.t + ph * (1 - value)
  const groupWidth = pw / groups.length
  const barWidth = Math.min(28, (groupWidth - 24) / methods.length - 2)
  const spread = methods.length * (barWidth + 2) - 2
  return (
    <Frame chart={chart} label={label} legend={<Legend methods={methods} t={t} />}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" focusable="false">
        <Hatches uid={uid} methods={methods} />
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <g key={tick}>
            <line x1={m.l} x2={W - m.r} y1={y(tick)} y2={y(tick)} stroke={GRID} strokeWidth="1" />
            <text x={m.l - 8} y={y(tick) + 4} textAnchor="end" fontSize="10.5" fill={MUTED}>{tick.toFixed(2)}</text>
          </g>
        ))}
        {groups.map((group, gi) => {
          const x0 = m.l + gi * groupWidth + (groupWidth - spread) / 2
          return (
            <g key={group.key} data-group={group.key}>
              {methods.map((method, mi) => {
                const bar = group.bars[method.id]
                if (!bar || bar.value === null || bar.value === undefined) return null
                const x = x0 + mi * (barWidth + 2)
                const height = Math.max(ph * bar.value, 0.5)
                const top = y(bar.value)
                return (
                  <g key={method.id} {...chart.mark(`${method.id} · ${group.label} ${bar.value.toFixed(3)}${bar.text ? ` · ${bar.text}` : ''}${method.valid ? '' : ` · ${t('showcase.metrics.invalidShort')}`}`)}
                    data-method={method.id} data-metric={group.key} data-value={bar.value} data-valid={method.valid}>
                    <path d={barPath(x, top, barWidth, height)} fill={fillOf(uid, method)} stroke={method.valid ? 'none' : colorOf(method.id)} strokeWidth="1" />
                    {bar.low !== undefined && bar.high !== undefined ? (
                      <g stroke={INK} strokeWidth="1.2">
                        <line x1={x + barWidth / 2} x2={x + barWidth / 2} y1={y(bar.high)} y2={y(bar.low)} />
                        <line x1={x + barWidth / 2 - 3} x2={x + barWidth / 2 + 3} y1={y(bar.high)} y2={y(bar.high)} />
                        <line x1={x + barWidth / 2 - 3} x2={x + barWidth / 2 + 3} y1={y(bar.low)} y2={y(bar.low)} />
                      </g>
                    ) : null}
                    {group.labelled ? (
                      <text x={x + barWidth / 2} y={(bar.high !== undefined ? y(bar.high) : top) - 5} textAnchor="middle" fontSize="10.5" fill={INK}>{bar.value.toFixed(2)}</text>
                    ) : null}
                  </g>
                )
              })}
              <text x={m.l + gi * groupWidth + groupWidth / 2} y={H - 18} textAnchor="middle" fontSize="11.5" fill={INK}>{group.label}</text>
              {group.sub ? <text x={m.l + gi * groupWidth + groupWidth / 2} y={H - 5} textAnchor="middle" fontSize="10" fill={MUTED}>{group.sub}</text> : null}
            </g>
          )
        })}
        <line x1={m.l} x2={W - m.r} y1={y(0)} y2={y(0)} stroke={MUTED} strokeWidth="1" />
      </svg>
      {tipLabel ? <p className="showcase-chart-note">{tipLabel}</p> : null}
    </Frame>
  )
}

/** Accuracy against cost: one point per method, labelled directly. */
export function CostScatter({ label, points, methods, xLabel, yLabel, t }) {
  const uid = useId().replace(/:/g, '')
  const chart = useTip()
  const W = 700, H = 300, m = { l: 56, r: 28, t: 18, b: 46 }
  const pw = W - m.l - m.r, ph = H - m.t - m.b
  const xmax = Math.max(...points.map(point => point.x)) * 1.12 || 1
  const x = value => m.l + (pw * value) / xmax
  const y = value => m.t + ph * (1 - value)
  return (
    <Frame chart={chart} label={label} legend={<Legend methods={methods} t={t} />}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" focusable="false">
        <Hatches uid={uid} methods={methods} />
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <g key={tick}>
            <line x1={m.l} x2={W - m.r} y1={y(tick)} y2={y(tick)} stroke={GRID} />
            <text x={m.l - 8} y={y(tick) + 4} textAnchor="end" fontSize="10.5" fill={MUTED}>{tick.toFixed(2)}</text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <text key={tick} x={x(xmax * tick)} y={H - 24} textAnchor="middle" fontSize="10.5" fill={MUTED}>{Math.round((xmax * tick) / 1000)}k</text>
        ))}
        <text x={m.l + pw / 2} y={H - 6} textAnchor="middle" fontSize="11" fill={MUTED}>{xLabel}</text>
        <text transform={`translate(12 ${m.t + ph / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill={MUTED}>{yLabel}</text>
        {points.map(point => {
          const method = methods.find(item => item.id === point.id)
          const cx = x(point.x), cy = y(point.y)
          const left = cx > W * 0.72
          return (
            <g key={point.id} {...chart.mark(`${point.id} · ${yLabel} ${point.y.toFixed(3)} · ${xLabel} ${Math.round(point.x)}${method.valid ? '' : ` · ${t('showcase.metrics.invalidShort')}`}`)}
              data-method={point.id} data-valid={method.valid}>
              <circle cx={cx} cy={cy} r="8" fill={fillOf(uid, method)} stroke={method.valid ? SURFACE : colorOf(point.id)} strokeWidth={method.valid ? 2 : 1.5} />
              <text x={cx + (left ? -14 : 14)} y={cy + 4} textAnchor={left ? 'end' : 'start'} fontSize="11.5" fill={INK}>{point.id}</text>
            </g>
          )
        })}
        <line x1={m.l} x2={W - m.r} y1={y(0)} y2={y(0)} stroke={MUTED} />
      </svg>
    </Frame>
  )
}

/** P50 to P95 per method on one seconds axis. */
export function LatencyRange({ label, rows, methods, t }) {
  const uid = useId().replace(/:/g, '')
  const chart = useTip()
  const W = 700, rowH = 38, m = { l: 90, r: 40, t: 12, b: 30 }
  const H = m.t + rows.length * rowH + m.b
  const pw = W - m.l - m.r
  const xmax = Math.max(...rows.map(row => row.p95)) * 1.1 || 1
  const x = value => m.l + (pw * value) / xmax
  return (
    <Frame chart={chart} label={label} legend={<Legend methods={methods} t={t} />}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" focusable="false">
        <Hatches uid={uid} methods={methods} />
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <g key={tick}>
            <line x1={x(xmax * tick)} x2={x(xmax * tick)} y1={m.t} y2={H - m.b} stroke={GRID} />
            <text x={x(xmax * tick)} y={H - 10} textAnchor="middle" fontSize="10.5" fill={MUTED}>{(xmax * tick).toFixed(0)}s</text>
          </g>
        ))}
        {rows.map((row, i) => {
          const method = methods.find(item => item.id === row.id)
          const cy = m.t + i * rowH + rowH / 2
          return (
            <g key={row.id} {...chart.mark(`${row.id} · P50 ${row.p50.toFixed(1)}s · P95 ${row.p95.toFixed(1)}s`)} data-method={row.id} data-valid={method.valid}>
              <text x={m.l - 10} y={cy + 4} textAnchor="end" fontSize="11.5" fill={INK}>{row.id}</text>
              <line x1={x(row.p50)} x2={x(row.p95)} y1={cy} y2={cy} stroke={colorOf(row.id)} strokeWidth="3" strokeLinecap="round" strokeDasharray={method.valid ? undefined : '1 6'} />
              <circle cx={x(row.p50)} cy={cy} r="6" fill={fillOf(uid, method)} stroke={method.valid ? SURFACE : colorOf(row.id)} strokeWidth="2" />
              <circle cx={x(row.p95)} cy={cy} r="6" fill={SURFACE} stroke={colorOf(row.id)} strokeWidth="2.4" />
              <text x={x(row.p95) + 12} y={cy + 4} fontSize="10.5" fill={MUTED}>{row.p50.toFixed(0)}s → {row.p95.toFixed(0)}s</text>
            </g>
          )
        })}
      </svg>
      <p className="showcase-chart-note">{t('showcase.metrics.latencyKey')}</p>
    </Frame>
  )
}

/** Rows by methods, one hue light to dark by value; missing cells are hatched grey, not zero. */
export function Heatmap({ label, rows, methods, t }) {
  const chart = useTip()
  const cell = { w: 112, h: 28 }
  const left = 168
  const W = left + methods.length * cell.w + 8
  const H = 34 + rows.length * (cell.h + 2) + 6
  return (
    <Frame chart={chart} label={label} legend={<Legend methods={methods} t={t} />}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W }} focusable="false">
        <defs>
          <pattern id="sm-empty" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="#efe9de" /><rect width="1.6" height="6" fill="#dcd2bf" />
          </pattern>
        </defs>
        {methods.map((method, i) => (
          <text key={method.id} x={left + i * cell.w + cell.w / 2} y="20" textAnchor="middle" fontSize="11.5" fill={method.valid ? INK : MUTED} fontWeight="600">{method.id}</text>
        ))}
        {rows.map((row, r) => {
          const y = 30 + r * (cell.h + 2)
          return (
            <g key={row.key}>
              <text x={left - 10} y={y + cell.h / 2 + 4} textAnchor="end" fontSize="11" fill={INK}>{row.label}</text>
              {methods.map((method, i) => {
                const item = row.cells[method.id]
                const x = left + i * cell.w
                if (!item || item.value === null || item.value === undefined) {
                  return (
                    <g key={method.id} {...chart.mark(`${row.label} · ${method.id} · ${t('showcase.metrics.noData')}`)} data-method={method.id} data-row={row.key} data-empty="true">
                      <rect x={x + 1} y={y} width={cell.w - 2} height={cell.h} rx="4" fill="url(#sm-empty)" />
                    </g>
                  )
                }
                const dark = item.value > 0.78
                return (
                  <g key={method.id} {...chart.mark(`${row.label} · ${method.id} · ${item.tip}${method.valid ? '' : ` · ${t('showcase.metrics.invalidShort')}`}`)}
                    data-method={method.id} data-row={row.key} data-value={item.value} data-valid={method.valid} opacity={method.valid ? 1 : 0.5}>
                    <rect x={x + 1} y={y} width={cell.w - 2} height={cell.h} rx="4" fill={`color-mix(in srgb, ${ACCENT} ${Math.round(10 + 78 * item.value)}%, #fff)`} />
                    <text x={x + cell.w / 2} y={y + cell.h / 2 + 4} textAnchor="middle" fontSize="11.5" fill={dark ? '#fff' : INK}>{item.text}</text>
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
    </Frame>
  )
}

/** Error roots per method as small multiples sharing one count scale; the method keeps its colour. */
export function ErrorPanels({ label, panels, methods, t }) {
  const uid = useId().replace(/:/g, '')
  const chart = useTip()
  const max = Math.max(1, ...panels.flatMap(panel => panel.rows.map(row => row.count)))
  const rowH = 22, labelW = 150, W = 330
  return (
    <Frame chart={chart} label={label} legend={<Legend methods={methods} t={t} />}>
      <div className="showcase-panels">
        {panels.map(panel => {
          const method = methods.find(item => item.id === panel.id)
          const H = Math.max(rowH, panel.rows.length * rowH) + 8
          return (
            <div key={panel.id} className="showcase-panel-mini" data-method={panel.id}>
              <h4 style={{ color: colorOf(panel.id) }}>{panel.id}{method.valid ? '' : ` · ${t('showcase.metrics.invalidShort')}`}</h4>
              {panel.rows.length ? (
                <svg viewBox={`0 0 ${W} ${H}`} width="100%" focusable="false">
                  <Hatches uid={uid} methods={[method]} />
                  {panel.rows.map((row, i) => {
                    const w = ((W - labelW - 62) * row.count) / max
                    return (
                      <g key={row.name} {...chart.mark(`${panel.id} · ${row.name} · ${row.count} (${ratio(row.pct)})`)} data-method={panel.id} data-error={row.name} data-value={row.count}>
                        <text x={labelW - 8} y={i * rowH + 15} textAnchor="end" fontSize="11" fill={INK}>{row.name}</text>
                        <path d={`M${labelW},${i * rowH + 4}H${labelW + w - 4}Q${labelW + w},${i * rowH + 4} ${labelW + w},${i * rowH + 8}V${i * rowH + 14}Q${labelW + w},${i * rowH + 18} ${labelW + w - 4},${i * rowH + 18}H${labelW}Z`}
                          fill={fillOf(uid, method)} stroke={method.valid ? 'none' : colorOf(panel.id)} />
                        <text x={labelW + w + 6} y={i * rowH + 15} fontSize="11" fill={MUTED}>{row.count} · {Math.round(row.pct * 100)}%</text>
                      </g>
                    )
                  })}
                </svg>
              ) : <p className="showcase-chart-note">{t('showcase.metrics.noErrors')}</p>}
            </div>
          )
        })}
      </div>
    </Frame>
  )
}
