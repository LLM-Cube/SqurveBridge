import { useEffect, useMemo, useState } from 'react'

const METRIC_KEYS = ['ex', 'em', 'sf1', 'ves', 'rves']

const percent = value => (value == null || Number.isNaN(Number(value)) ? '—' : `${(Number(value) * 100).toFixed(1)}%`)
const compact = value => (value == null ? '—' : Intl.NumberFormat('en', { notation: 'compact' }).format(value))
const bytes = value => {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

const metricValue = (metrics, key) => {
  if (!metrics || typeof metrics !== 'object') return null
  const direct = metrics[key]
  if (typeof direct === 'number') return direct
  if (direct && typeof direct === 'object' && typeof direct.avg === 'number') return direct.avg
  const aggregate = metrics.aggregate
  if (aggregate && typeof aggregate === 'object') {
    const nested = aggregate[key]
    if (typeof nested === 'number') return nested
    if (nested && typeof nested === 'object' && typeof nested.avg === 'number') return nested.avg
  }
  return null
}

const barWidth = value => {
  if (value == null || Number.isNaN(Number(value))) return 0
  return Math.max(0, Math.min(100, Math.round(Number(value) * 100)))
}

function renderMarkdown(text) {
  const lines = String(text || '').split(/\r?\n/)
  const blocks = []
  let list = null
  const flushList = () => {
    if (list?.length) {
      blocks.push(<ul key={`ul-${blocks.length}`}>{list.map((item, index) => <li key={index}>{item}</li>)}</ul>)
    }
    list = null
  }
  lines.forEach((line, index) => {
    if (/^\s*[-*]\s+/.test(line)) {
      list = list || []
      list.push(line.replace(/^\s*[-*]\s+/, ''))
      return
    }
    flushList()
    if (!line.trim()) {
      blocks.push(<div key={`sp-${index}`} className="md-space" />)
    } else if (line.startsWith('### ')) {
      blocks.push(<h4 key={index}>{line.slice(4)}</h4>)
    } else if (line.startsWith('## ')) {
      blocks.push(<h3 key={index}>{line.slice(3)}</h3>)
    } else if (line.startsWith('# ')) {
      blocks.push(<h2 key={index}>{line.slice(2)}</h2>)
    } else if (line.startsWith('```')) {
      blocks.push(<pre key={index} className="md-code">{line}</pre>)
    } else {
      blocks.push(<p key={index}>{line}</p>)
    }
  })
  flushList()
  return blocks
}

function MetricBoard({ metrics, label, primaryKey = 'ex' }) {
  const cards = METRIC_KEYS.map(key => ({
    key,
    short: key.toUpperCase(),
    name: label(`archive.metric.${key}`, key.toUpperCase()),
    hint: label(`archive.metricHint.${key}`, ''),
    value: metricValue(metrics, key),
  }))
  const primary = cards.find(item => item.key === primaryKey) || cards[0]
  const others = cards.filter(item => item.key !== primary?.key)

  return (
    <div className="archive-metric-board" data-testid="archive-metric-board">
      <div className="archive-metric-hero">
        <div>
          <span>{primary?.name || 'Execution accuracy'}</span>
          <strong>{percent(primary?.value)}</strong>
          {primary?.hint ? <small>{primary.hint}</small> : null}
        </div>
        <div className="archive-metric-hero-track" aria-hidden="true">
          <i style={{ width: `${barWidth(primary?.value)}%` }} />
        </div>
      </div>
      <ul className="archive-metric-bars">
        {others.map(card => (
          <li key={card.key}>
            <div className="archive-metric-bar-label">
              <strong>{card.short}</strong>
              <span>{card.name}</span>
            </div>
            <div className="archive-metric-bar-track" aria-hidden="true">
              <i style={{ width: `${barWidth(card.value)}%` }} />
            </div>
            <b>{percent(card.value)}</b>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ScoresOverview({ data, label }) {
  const metrics = data?.metrics || data?.aggregate || data
  const sampleCount = data?.sample_count
  const token = data?.token || data?.aggregate?.token || {}
  const actors = Array.isArray(data?.config_snapshot?.actors) ? data.config_snapshot.actors : []

  return (
    <div className="archive-scores-overview" data-testid="archive-scores-overview">
      <MetricBoard metrics={metrics} label={label} />
      <dl className="archive-fact-grid">
        <div>
          <dt>{label('archive.samples', 'Samples')}</dt>
          <dd>{sampleCount ?? '—'}</dd>
        </div>
        <div>
          <dt>{label('archive.tokens', 'Tokens')}</dt>
          <dd>{compact(token.total_tokens)}</dd>
        </div>
        <div>
          <dt>{label('archive.split', 'Split')}</dt>
          <dd>{data?.split || '—'}</dd>
        </div>
        <div>
          <dt>{label('archive.scope', 'Scope')}</dt>
          <dd>{data?.scope || '—'}</dd>
        </div>
      </dl>
      {actors.length ? (
        <div className="archive-actor-pipeline">
          <h4>{label('archive.pipeline', 'Actor pipeline')}</h4>
          <ol>
            {actors.map(actor => (
              <li key={`${actor.task_id}-${actor.actor_class}`}>
                <strong>{actor.actor_class || actor.task_id}</strong>
                <span>{actor.task_type || actor.task_id}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  )
}

function FileViewer({ file, onClose, label }) {
  const [showRaw, setShowRaw] = useState(false)
  const filePath = file?.path || ''

  useEffect(() => {
    setShowRaw(false)
  }, [filePath])

  if (!file) {
    return (
      <div className="archive-viewer empty-viewer">
        <strong>{label('archive.selectFile', 'Select a file above')}</strong>
        <span>{label('archive.selectFileDetail', 'Open scores.json, weakness profiles, reports, or config snapshots.')}</span>
      </div>
    )
  }

  const isScores = file.name === 'scores.json' && file.json && typeof file.json === 'object'

  return (
    <section className="archive-viewer">
      <header>
        <div>
          <span>{file.name}</span>
          <small>{file.path} · {bytes(file.size_bytes)}{file.truncated ? ' · truncated' : ''}</small>
        </div>
        <div className="archive-viewer-actions">
          {isScores ? (
            <button
              className="button compact secondary"
              type="button"
              onClick={() => setShowRaw(value => !value)}
            >
              {showRaw
                ? label('archive.showStructured', 'Show summary')
                : label('archive.showRaw', 'Show raw JSON')}
            </button>
          ) : null}
          <button className="button compact secondary" type="button" onClick={onClose}>
            {label('archive.closeFile', 'Close')}
          </button>
        </div>
      </header>
      {isScores && !showRaw ? (
        <ScoresOverview data={file.json} label={label} />
      ) : file.kind === 'markdown' ? (
        <div className="md-body">{renderMarkdown(file.content)}</div>
      ) : file.kind === 'json' && file.json != null ? (
        <pre className="raw-json">{JSON.stringify(file.json, null, 2)}</pre>
      ) : (
        <pre className="raw-json">{file.content}</pre>
      )}
    </section>
  )
}

export default function Archive({
  api,
  Status,
  PageHeading,
  Empty,
  onOpenInVisualize,
  onExpandRun,
  embedded = false,
  allowFileContent = true,
  mode = 'full',
  runId = '',
  t,
}) {
  const browseMode = mode === 'browse'
  const detailMode = mode === 'detail'
  const label = (key, fallback) => {
    if (typeof t === 'function') {
      const value = t(key)
      if (value != null && value !== '' && value !== key) return value
    }
    return fallback
  }
  const [query, setQuery] = useState('')
  const [dataset, setDataset] = useState('')
  const [method, setMethod] = useState('')
  const [source, setSource] = useState('')
  const [catalog, setCatalog] = useState({ runs: [], filters: { datasets: [], methods: [], sources: [] }, total: 0 })
  const [selectedId, setSelectedId] = useState(runId || '')
  const [detail, setDetail] = useState(null)
  const [filePayload, setFilePayload] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (runId) setSelectedId(runId)
  }, [runId])

  const loadCatalog = async () => {
    setBusy(true)
    setError('')
    try {
      const params = new URLSearchParams()
      if (query.trim()) params.set('q', query.trim())
      if (dataset) params.set('dataset', dataset)
      if (method) params.set('method', method)
      if (source) params.set('source', source)
      const data = await api(`/api/archive?${params}`)
      const next = {
        runs: Array.isArray(data?.runs) ? data.runs : [],
        filters: data?.filters || { datasets: [], methods: [], sources: [] },
        total: Number.isFinite(data?.total) ? data.total : (Array.isArray(data?.runs) ? data.runs.length : 0),
      }
      setCatalog(next)
      if (detailMode) return
      if (browseMode) return
      if (selectedId && !next.runs.some(run => run.run_id === selectedId)) {
        setSelectedId(next.runs[0]?.run_id || '')
      } else if (!selectedId && next.runs[0]) {
        setSelectedId(next.runs[0].run_id)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (detailMode) return undefined
    const timer = setTimeout(loadCatalog, 120)
    return () => clearTimeout(timer)
  }, [query, dataset, method, source, detailMode])

  useEffect(() => {
    if (browseMode || !selectedId) {
      setDetail(null)
      setFilePayload(null)
      return undefined
    }
    let active = true
    const load = async () => {
      try {
        const data = await api(`/api/archive/${encodeURIComponent(selectedId)}`)
        if (!active) return
        setDetail(data)
        setFilePayload(null)
        const preferred = data.files?.find(item => item.name === 'scores.json')
          || data.files?.find(item => item.kind === 'markdown')
          || data.files?.[0]
        if (allowFileContent && preferred) {
          const file = await api(`/api/archive/${encodeURIComponent(selectedId)}/files/${preferred.path}`)
          if (active) setFilePayload(file)
        }
      } catch (err) {
        if (active) setError(err.message)
      }
    }
    load()
    return () => { active = false }
  }, [selectedId, allowFileContent, browseMode])

  const openFile = async path => {
    if (!allowFileContent || !selectedId || !path) return
    setError('')
    try {
      const file = await api(`/api/archive/${encodeURIComponent(selectedId)}/files/${path}`)
      setFilePayload(file)
    } catch (err) {
      setError(err.message)
    }
  }

  const groupedFiles = useMemo(() => {
    const groups = { reports: [], scores: [], configs: [], other: [] }
    for (const file of detail?.files || []) {
      if (file.kind === 'markdown' || file.name.includes('report') || file.name.endsWith('.log')) groups.reports.push(file)
      else if (file.name.includes('score') || file.name.includes('token') || file.name.includes('meta-evo')) groups.scores.push(file)
      else if (file.name.includes('config') || file.name.includes('task')) groups.configs.push(file)
      else groups.other.push(file)
    }
    return groups
  }, [detail])

  const samplingLabel = detail?.sampling
    ? `${detail.sampling.mode || '—'}${detail.sampling.limit ? ` · ${detail.sampling.limit}` : ''}${detail.sampling.seed != null ? ` · seed ${detail.sampling.seed}` : ''}`
    : '—'

  const openRun = run => {
    const id = run?.run_id
    if (!id) return
    if (onExpandRun) {
      onExpandRun(id)
      return
    }
    setSelectedId(id)
  }

  return (
    <div
      className={[
        'workspace archive-workspace',
        embedded ? 'archive-workspace-embedded' : '',
        browseMode ? 'archive-workspace-browse' : '',
        detailMode ? 'archive-workspace-detail' : '',
      ].filter(Boolean).join(' ')}
      data-mode={mode}
    >
      {!embedded && !detailMode && <PageHeading
        eyebrow="Experiment archive"
        title="Artifacts library"
        status={<Status tone={catalog.total ? 'success' : 'neutral'}>{catalog.total} runs indexed</Status>}
      />}

      {!detailMode && (
        <section className="tool-panel archive-filters">
          <div className="panel-title">
            <div>
              <span>{label('archive.findTitle', 'Find experiment data')}</span>
              <small>{label('archive.findDetail', 'Search verified public evidence, local artifacts, and demo-run score bundles')}</small>
            </div>
            <button className="button compact secondary" disabled={busy} onClick={loadCatalog}>
              {busy ? label('archive.scanning', 'Scanning…') : label('archive.refresh', 'Refresh')}
            </button>
          </div>
          <div className="archive-filter-grid">
            <label className="field">
              <span>{label('archive.search', 'Search')}</span>
              <input value={query} onChange={event => setQuery(event.target.value)} placeholder="run id · method · dataset · filename" />
            </label>
            <label className="field">
              <span>{label('archive.dataset', 'Dataset')}</span>
              <select value={dataset} onChange={event => setDataset(event.target.value)}>
                <option value="">{label('archive.allDatasets', 'All datasets')}</option>
                {(catalog.filters?.datasets || []).map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <label className="field">
              <span>{label('archive.method', 'Method')}</span>
              <select value={method} onChange={event => setMethod(event.target.value)}>
                <option value="">{label('archive.allMethods', 'All methods')}</option>
                {(catalog.filters?.methods || []).map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <label className="field">
              <span>{label('archive.source', 'Source')}</span>
              <select value={source} onChange={event => setSource(event.target.value)}>
                <option value="">{label('archive.allSources', 'All sources')}</option>
                {(catalog.filters?.sources || []).map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          </div>
          {error && <p className="error-banner">{error}</p>}
        </section>
      )}

      <div className={`archive-layout${browseMode ? ' archive-layout-browse' : ''}${detailMode ? ' archive-layout-detail' : ''}`}>
        {!detailMode && (
          <section className="tool-panel archive-list">
            <div className="panel-title">
              <div>
                <span>{label('archive.runs', 'Runs')}</span>
                <small>{catalog.total} {label('archive.matching', 'matching')}</small>
              </div>
            </div>
            <div className="archive-run-list">
              {catalog.runs?.length ? catalog.runs.map(run => {
                const ex = metricValue(run.metrics, 'ex')
                return (
                  <article
                    key={run.run_id}
                    className={[
                      'archive-run-card',
                      selectedId === run.run_id ? 'active' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    <button
                      type="button"
                      className="archive-run-select"
                      onClick={() => openRun(run)}
                    >
                      <div className="archive-run-head">
                        <b>{run.method || 'unknown'} / {run.dataset || 'unknown'}</b>
                        {run.stages && Object.keys(run.stages).length ? (
                          <small className="archive-run-variant">
                            {label('archive.variant', 'Variant')} · {Object.entries(run.stages).map(([stage, actor]) => `${stage}→${actor}`).join(', ')}
                          </small>
                        ) : null}
                        <strong className="archive-run-ex">{percent(ex)}</strong>
                      </div>
                      <small className="archive-run-id">{run.run_id}</small>
                      <div className="archive-run-score" aria-hidden="true">
                        <i style={{ width: `${barWidth(ex)}%` }} />
                      </div>
                      <div className="archive-run-meta">
                        <span>EX {percent(ex)}</span>
                        <span>{run.sample_count ?? '—'} {label('archive.samplesShort', 'samples')}</span>
                        <span>{run.source}</span>
                      </div>
                    </button>
                    {browseMode && onExpandRun ? (
                      <button
                        type="button"
                        className="button compact primary archive-run-expand"
                        onClick={() => onExpandRun(run.run_id)}
                      >
                        {label('evidence.expandRun', 'Open run')}
                      </button>
                    ) : null}
                  </article>
                )
              }) : (
                <Empty
                  title={label('archive.noRuns', 'No archived runs')}
                  detail={label('archive.noRunsDetail', 'Verified public bundles live under evidence/reported-results; local evaluations write into workspace/artifacts and workspace/sessions/evaluations.')}
                />
              )}
            </div>
          </section>
        )}

        {!browseMode && (
          <div className="archive-detail">
            {!detail ? (
              <section className="tool-panel board-empty">
                <Empty
                  title={label('archive.selectRun', 'Select a run')}
                  detail={label('archive.selectRunDetail', 'Browse score bundles, markdown weakness profiles, and JSON reports.')}
                />
              </section>
            ) : (
              <>
                <section className="tool-panel archive-summary">
                  <div className="panel-title">
                    <div>
                      <span>{detail.method} on {detail.dataset}</span>
                      <small>{detail.run_id}</small>
                    </div>
                    <div className="archive-summary-actions">
                      {onOpenInVisualize && (
                        <button
                          type="button"
                          className="button compact primary"
                          onClick={() => onOpenInVisualize(detail.run_id)}
                        >
                          {label('evidence.expandCharts', label('archive.openInVisualize', 'Expand charts'))}
                        </button>
                      )}
                      <Status tone="success">{detail.source}</Status>
                    </div>
                  </div>

                  <MetricBoard metrics={detail.metrics} label={label} />

                  <dl className="archive-fact-grid archive-summary-facts">
                    <div>
                      <dt>{label('archive.samples', 'Samples')}</dt>
                      <dd>{detail.sample_count ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>{label('archive.tokens', 'Tokens')}</dt>
                      <dd>{compact(detail.token?.total_tokens)}</dd>
                    </div>
                    <div>
                      <dt>{label('archive.split', 'Split')}</dt>
                      <dd>{detail.split || '—'}</dd>
                    </div>
                    <div>
                      <dt>{label('archive.scope', 'Scope')}</dt>
                      <dd>{detail.scope || '—'}</dd>
                    </div>
                    <div>
                      <dt>{label('archive.sampling', 'Sampling')}</dt>
                      <dd>{samplingLabel}</dd>
                    </div>
                    <div>
                      <dt>{label('archive.timestamp', 'Timestamp')}</dt>
                      <dd>{detail.timestamp || '—'}</dd>
                    </div>
                  </dl>
                </section>

                <div className="archive-reader">
                  <section className="tool-panel archive-files">
                    <div className="panel-title">
                      <div>
                        <span>{label('archive.files', 'Files')}</span>
                        <small>{label('archive.filesDetail', 'Select an artifact to preview below')}</small>
                      </div>
                    </div>
                    <div className="archive-file-groups">
                      {Object.entries(groupedFiles).map(([group, files]) => files.length ? (
                        <div key={group} className="archive-file-group">
                          <header>{group}</header>
                          <div className="archive-file-chips">
                            {files.map(file => (
                              <button
                                key={file.path}
                                type="button"
                                className={filePayload?.path === file.path ? 'active' : ''}
                                disabled={!allowFileContent}
                                onClick={() => openFile(file.path)}
                              >
                                <span>{file.name}</span>
                                <small>{file.kind} · {bytes(file.size_bytes)}</small>
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : null)}
                    </div>
                  </section>
                  <div className="tool-panel archive-viewer-shell">
                    {allowFileContent
                      ? <FileViewer file={filePayload} onClose={() => setFilePayload(null)} label={label} />
                      : (
                        <div className="archive-viewer empty-viewer">
                          <strong>{label('archive.sanitizedOnly', 'Sanitized summary only')}</strong>
                          <span>{label('archive.sanitizedDetail', 'Raw archive files stay unavailable in the public hosted demo.')}</span>
                          <MetricBoard metrics={detail.metrics} label={label} />
                        </div>
                      )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
