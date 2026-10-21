import { useEffect, useMemo, useRef, useState } from 'react'
import SchemaTree from './SchemaTree.jsx'
import SqlEditor from './SqlEditor.jsx'
import {
  buildCsv,
  buildPiAnalysisPrompt,
  computeSchemaHits,
  csvFileName,
  exampleQuestions,
  formatElapsedMs,
  loadQuestionHistory,
  plannedQueryStages,
  pushQuestionHistory,
  schemaToCompletion,
  stagesFromTrace,
} from './queryModel.js'

const PLACEHOLDER_STEP_MS = 900
const labelSlug = value => String(value || '').trim().toLowerCase().replace(/[_\s]+/g, '-')

const stageStatusLabel = (status, t) => (
  status === 'failed'
    ? t('query.stageFailed')
    : status === 'done'
      ? t('query.stageDone')
      : status === 'queued'
        ? t('query.stageQueued')
        : t('query.stageRunning')
)

function StageChip({ stage, active, detailOpen, totalElapsed, onOpenDetail, t }) {
  const status = stage.status
  const share = status === 'done' && stage.elapsedMs != null && totalElapsed > 0
    ? Math.max(2, Math.round((stage.elapsedMs / totalElapsed) * 100))
    : null
  const sublabel = status === 'done' && stage.elapsedMs != null
    ? formatElapsedMs(stage.elapsedMs)
    : stageStatusLabel(status, t)
  return (
    <button
      type="button"
      className={[
        'query-stage-chip',
        `is-${status}`,
        active ? 'is-active' : '',
        detailOpen ? 'is-open' : '',
      ].filter(Boolean).join(' ')}
      aria-pressed={detailOpen}
      onClick={onOpenDetail}
    >
      <span className="query-stage-mark" aria-hidden="true">
        {status === 'done' ? '✓' : status === 'failed' ? '×' : ''}
      </span>
      <span className="query-stage-copy">
        <b>{stage.actor}</b>
        <small>{sublabel}</small>
      </span>
      {share != null && (
        <span
          className="query-stage-share"
          style={{ width: `${share}%` }}
          title={t('query.stageShare', { percent: share })}
          aria-hidden="true"
        />
      )}
    </button>
  )
}

function ResultsTable({ result, onCopyCell, t }) {
  const [sort, setSort] = useState(null)
  useEffect(() => { setSort(null) }, [result])

  const rows = useMemo(() => {
    if (!result?.rows) return []
    if (!sort) return result.rows
    const { index, direction } = sort
    const factor = direction === 'asc' ? 1 : -1
    return [...result.rows].sort((a, b) => {
      const left = a[index]
      const right = b[index]
      if (left == null && right == null) return 0
      if (left == null) return 1
      if (right == null) return -1
      const leftNumber = Number(left)
      const rightNumber = Number(right)
      if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber) && String(left).trim() !== '' && String(right).trim() !== '') {
        return (leftNumber - rightNumber) * factor
      }
      return String(left).localeCompare(String(right)) * factor
    })
  }, [result, sort])

  if (!result || !Array.isArray(result.columns)) return null
  const cycleSort = index => {
    setSort(current => {
      if (!current || current.index !== index) return { index, direction: 'asc' }
      if (current.direction === 'asc') return { index, direction: 'desc' }
      return null
    })
  }

  return (
    <div className="query-results-scroll" data-testid="query-results-table">
      <table>
        <thead>
          <tr>
            {result.columns.map((column, index) => {
              const state = sort?.index === index ? sort.direction : ''
              return (
                <th key={`${column}-${index}`} aria-sort={state === 'asc' ? 'ascending' : state === 'desc' ? 'descending' : 'none'}>
                  <button
                    type="button"
                    aria-label={t('query.sortColumn', { name: column })}
                    onClick={() => cycleSort(index)}
                  >
                    {column}
                    <i aria-hidden="true">{state === 'asc' ? '▲' : state === 'desc' ? '▼' : ''}</i>
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((value, cellIndex) => (
                <td
                  key={cellIndex}
                  className={typeof value === 'number' ? 'is-number' : value == null ? 'is-null' : ''}
                >
                  <button
                    type="button"
                    aria-label={t('query.copyCell')}
                    onClick={() => onCopyCell(value)}
                  >
                    {value == null ? <em>{t('query.nullValue')}</em> : String(value)}
                  </button>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Pipeline-transparent interactive query workspace: schema tree on the left,
 * question → pipeline stages → editable SQL → results flowing down the right.
 */
export default function QueryWorkspace({
  databases = [],
  capabilities,
  focusedConfig,
  focusedMethod,
  focusedDatabase,
  sqlAuth,
  credentialMode = 'session',
  onConfigureSql,
  postJson,
  api,
  adoptedSql = null,
  prefill = null,
  onAdoptedSqlHandled,
  onAskPi,
  t,
  Editor = SqlEditor,
}) {
  const [selectedDb, setSelectedDb] = useState('')
  const [schemaCache, setSchemaCache] = useState({})
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState(() => loadQuestionHistory())
  const [mode, setMode] = useState('direct')
  const [generator, setGenerator] = useState('')
  const [skeletonIndex, setSkeletonIndex] = useState(1)
  const [actorSelections, setActorSelections] = useState({})
  const [workflowSource, setWorkflowSource] = useState('skeleton')
  const [phase, setPhase] = useState('idle')
  const [pipelineStages, setPipelineStages] = useState([])
  const [activeStageIndex, setActiveStageIndex] = useState(0)
  const [stageDetailId, setStageDetailId] = useState(null)
  const [sql, setSql] = useState('')
  const [baselineSql, setBaselineSql] = useState('')
  const [result, setResult] = useState(null)
  const [genError, setGenError] = useState('')
  const [execError, setExecError] = useState('')
  const [executing, setExecuting] = useState(false)
  const [hitsDismissed, setHitsDismissed] = useState(false)
  const [schemaCollapsed, setSchemaCollapsed] = useState(false)
  const [toast, setToast] = useState(null)
  const [editorFlash, setEditorFlash] = useState(false)
  const abortRef = useRef(null)
  const editorRef = useRef(null)
  const toastTimerRef = useRef(null)
  const flashTimerRef = useRef(null)
  const questionRef = useRef(null)
  const stagesRef = useRef(null)
  const resultsRef = useRef(null)

  const actorsByType = capabilities?.actors || {}
  const workflows = capabilities?.workflows || []
  const generators = actorsByType.generator || []
  const composeActors = useMemo(
    () => (focusedConfig?.stages || []).map(stage => stage.actor).filter(Boolean),
    [focusedConfig],
  )
  const skeleton = workflows[skeletonIndex] || []
  const credentialReady = Boolean(sqlAuth?.configured)
  const schemaEntry = schemaCache[selectedDb]
  const schemaTables = schemaEntry?.status === 'ready' ? schemaEntry.tables : []
  const completionSchema = useMemo(() => schemaToCompletion(schemaTables), [schemaTables])
  const hits = useMemo(() => computeSchemaHits(schemaTables, sql), [schemaTables, sql])
  const hasRun = phase !== 'idle' || sql !== ''
  const sqlEdited = sql !== baselineSql && baselineSql !== ''

  useEffect(() => () => {
    abortRef.current?.abort()
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
  }, [])

  useEffect(() => {
    if (!generator && generators.length) setGenerator(generators.includes('DINSQLGenerator') ? 'DINSQLGenerator' : generators[0])
  }, [generators, generator])

  useEffect(() => {
    const defaults = Object.fromEntries(
      Object.entries(actorsByType).map(([type, items]) => [type, items[0] || '']),
    )
    setActorSelections(current => ({ ...defaults, ...current }))
  }, [capabilities])

  useEffect(() => {
    if (composeActors.length) {
      setMode(current => (current === 'direct' ? 'workflow' : current))
      setWorkflowSource('compose')
    }
  }, [composeActors.join('|')])

  useEffect(() => {
    if (selectedDb || databases.length === 0) return
    const preferred = labelSlug(focusedDatabase)
    const match = databases.find(item => item.benchmark === preferred) || databases[0]
    if (match) setSelectedDb(match.id)
  }, [databases, selectedDb, focusedDatabase])

  useEffect(() => {
    if (!selectedDb || schemaCache[selectedDb]) return
    let active = true
    setSchemaCache(current => ({ ...current, [selectedDb]: { status: 'loading' } }))
    api(`/api/databases/${encodeURIComponent(selectedDb)}/schema`)
      .then(data => {
        if (!active) return
        setSchemaCache(current => ({
          ...current,
          [selectedDb]: { status: 'ready', tables: data.tables || [] },
        }))
      })
      .catch(error => {
        if (!active) return
        setSchemaCache(current => ({
          ...current,
          [selectedDb]: { status: 'error', error: error.message },
        }))
      })
    return () => { active = false }
  }, [selectedDb, api])

  useEffect(() => {
    if (!prefill?.id) return
    if (databases.some(item => item.id === prefill.dbId)) setSelectedDb(prefill.dbId)
    if (prefill.question) setQuestion(prefill.question)
  }, [prefill?.id])

  useEffect(() => {
    if (!adoptedSql?.id) return
    setSql(adoptedSql.sql || '')
    setBaselineSql(adoptedSql.sql || '')
    setResult(null)
    setExecError('')
    setGenError('')
    setEditorFlash(true)
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
    flashTimerRef.current = setTimeout(() => setEditorFlash(false), 1400)
    onAdoptedSqlHandled?.()
  }, [adoptedSql?.id])

  useEffect(() => {
    if (phase !== 'generating' || pipelineStages.length === 0) return undefined
    const timer = setInterval(() => {
      setActiveStageIndex(current => Math.min(current + 1, pipelineStages.length - 1))
    }, PLACEHOLDER_STEP_MS)
    return () => clearInterval(timer)
  }, [phase, pipelineStages.length])

  useEffect(() => {
    if (!stageDetailId) return undefined
    const onKey = event => {
      if (event.key === 'Escape') setStageDetailId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stageDetailId])

  useEffect(() => {
    if (phase === 'generating') stagesRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  }, [phase])

  useEffect(() => {
    if (result || execError) resultsRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  }, [result, execError])

  const showToast = text => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    setToast({ id: Date.now(), text })
    toastTimerRef.current = setTimeout(() => setToast(null), 1600)
  }

  const copyText = async (text, message) => {
    try {
      await navigator.clipboard?.writeText(String(text ?? ''))
      showToast(message)
    } catch {
      showToast(t('query.copyFailed'))
    }
  }

  const requestActors = mode === 'workflow'
    ? (workflowSource === 'compose' && composeActors.length
      ? composeActors
      : skeleton.map(type => actorSelections[type]).filter(Boolean))
    : []

  const run = async (overrideQuestion) => {
    const normalized = (typeof overrideQuestion === 'string' ? overrideQuestion : question).trim()
    if (!normalized || !selectedDb || !credentialReady || phase === 'generating' || phase === 'executing') return
    const planned = plannedQueryStages({ mode, generator, actors: requestActors })
    setPhase('generating')
    setGenError('')
    setExecError('')
    setResult(null)
    setStageDetailId(null)
    setHitsDismissed(false)
    setPipelineStages(planned.map(stage => ({ ...stage, status: 'queued' })))
    setActiveStageIndex(0)
    setHistory(pushQuestionHistory(normalized))
    const controller = new AbortController()
    abortRef.current = controller
    const payload = { question: normalized, db_id: selectedDb, mode }
    if (mode === 'workflow') payload.actors = requestActors
    else payload.generator = generator
    if (credentialMode === 'local' && sqlAuth?.provider) {
      payload.provider = sqlAuth.provider
      if (sqlAuth.model) payload.model = sqlAuth.model
    }
    try {
      const data = await postJson('/api/query', payload, { signal: controller.signal })
      const traced = stagesFromTrace(data.trace)
      setPipelineStages(traced.length ? traced : planned.map(stage => ({ ...stage, status: 'done' })))
      setSql(data.sql || '')
      setBaselineSql(data.sql || '')
      setPhase('executing')
      try {
        const execution = await postJson(
          '/api/execute',
          { db_id: selectedDb, sql: data.sql },
          { signal: controller.signal },
        )
        setResult(execution)
        setPhase('done')
      } catch (error) {
        if (controller.signal.aborted) { setPhase('done'); return }
        setExecError(error.message)
        setPhase('done')
      }
    } catch (error) {
      if (controller.signal.aborted) {
        setPhase('idle')
        setPipelineStages([])
        return
      }
      setGenError(error.message)
      setPipelineStages(current => current.map((stage, index) => ({
        ...stage,
        status: index === Math.min(activeStageIndex, current.length - 1) ? 'failed' : stage.status === 'queued' ? 'queued' : stage.status,
      })))
      setPhase('error')
    } finally {
      abortRef.current = null
    }
  }

  const cancel = () => {
    abortRef.current?.abort()
  }

  const execute = async () => {
    if (!sql.trim() || !selectedDb || executing) return
    setExecuting(true)
    setExecError('')
    try {
      const execution = await postJson('/api/execute', { db_id: selectedDb, sql })
      setResult(execution)
      setHitsDismissed(false)
    } catch (error) {
      setExecError(error.message)
      setResult(null)
    } finally {
      setExecuting(false)
    }
  }

  const exportCsv = () => {
    if (!result) return
    const csv = buildCsv(result.columns, result.rows)
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = csvFileName(selectedDb)
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(href)
  }

  const askPi = () => {
    onAskPi?.(buildPiAnalysisPrompt({
      question,
      dbId: selectedDb,
      sql,
      stages: pipelineStages,
      error: execError || genError,
      rowCount: result?.row_count ?? null,
    }))
  }

  const insertIdentifier = name => {
    if (editorRef.current?.insert) editorRef.current.insert(name)
    else setSql(current => (current ? `${current} ${name}` : name))
  }

  const executeStage = hasRun && (phase !== 'idle' || result || execError) ? {
    id: 'stage-execute',
    actor: t('query.stageExecute'),
    stage: 'sqlite',
    status: phase === 'executing' || executing
      ? 'running'
      : execError
        ? 'failed'
        : result
          ? 'done'
          : 'queued',
    elapsedMs: result?.elapsed_ms ?? null,
  } : null

  const displayStages = useMemo(() => {
    const base = phase === 'generating'
      ? pipelineStages.map((stage, index) => ({
        ...stage,
        status: index < activeStageIndex ? 'done' : index === activeStageIndex ? 'running' : 'queued',
      }))
      : pipelineStages
    return executeStage ? [...base, executeStage] : base
  }, [phase, pipelineStages, activeStageIndex, executeStage])

  const totalElapsed = useMemo(
    () => displayStages.reduce((sum, stage) => sum + (stage.elapsedMs || 0), 0),
    [displayStages],
  )
  const detailStage = displayStages.find(stage => stage.id === stageDetailId) || null
  const liveMessage = phase === 'generating'
    ? `${pipelineStages[Math.min(activeStageIndex, pipelineStages.length - 1)]?.actor || ''} ${t('query.stageRunning')}`
    : phase === 'executing' || executing
      ? t('query.executing')
      : phase === 'done' && result
        ? t('query.resultsSummary', { count: result.row_count, elapsed: result.elapsed_ms })
        : ''

  const firstTable = schemaTables[0]
  const examples = useMemo(
    () => (firstTable ? exampleQuestions(firstTable, t) : []),
    [firstTable, t],
  )
  const focusedPairLabel = focusedMethod && focusedDatabase
    ? t('query.useComposePipeline', { method: focusedMethod, database: focusedDatabase })
    : ''
  const runBlockedReason = !credentialReady
    ? t('query.runBlockedCredential')
    : !selectedDb
      ? t('query.runBlockedDatabase')
      : !question.trim()
        ? t('query.runBlockedQuestion')
        : ''

  return (
    <section id="query" className="flow-module flow-glass query-workspace" data-testid="query-workspace">
      <header className="flow-module-header">
        <div>
          <span>{t('process.query')}</span>
          <h2>{t('query.title')}</h2>
          <p>{t('query.description')}</p>
        </div>
        <div className="query-credential" data-testid="query-credential">
          <span>{t('query.credential')}</span>
          {credentialReady
            ? <b>{sqlAuth.provider}{sqlAuth.model ? ` / ${sqlAuth.model}` : ''}</b>
            : <b className="is-missing">{t('query.credentialMissing')}</b>}
        </div>
      </header>

      <div className={`query-layout ${schemaCollapsed ? 'is-schema-collapsed' : ''}`}>
        <SchemaTree
          databases={databases}
          selectedDb={selectedDb}
          onSelectDb={setSelectedDb}
          schema={schemaEntry}
          hits={hits}
          hitsDismissed={hitsDismissed}
          onDismissHits={() => setHitsDismissed(true)}
          onInsert={insertIdentifier}
          collapsed={schemaCollapsed}
          onToggleCollapsed={() => setSchemaCollapsed(current => !current)}
          t={t}
        />

        <div className="query-flow">
          {!credentialReady && (
            <div className="query-locked" data-testid="query-locked">
              <span className="query-locked-icon" aria-hidden="true">🔒</span>
              <div>
                <b>{t('query.lockedTitle')}</b>
                <p>{t(credentialMode === 'local' ? 'query.lockedLocalDetail' : 'query.lockedHostedDetail')}</p>
              </div>
              <button type="button" onClick={onConfigureSql}>{t('query.lockedAction')}</button>
            </div>
          )}

          <div className="query-question">
            <div className="query-question-field">
              <label>
                <span>{t('query.questionLabel')}</span>
                <textarea
                  ref={questionRef}
                  value={question}
                  rows={3}
                  disabled={!credentialReady}
                  placeholder={t('query.questionPlaceholder')}
                  aria-label={t('query.questionLabel')}
                  aria-describedby={runBlockedReason ? 'query-run-blocked-reason' : undefined}
                  onChange={event => setQuestion(event.target.value)}
                  onKeyDown={event => {
                    if (event.key !== 'Enter') return
                    if (event.shiftKey) return
                    if (event.nativeEvent?.isComposing || event.isComposing) return
                    event.preventDefault()
                    run()
                  }}
                />
              </label>
              <div className="query-question-actions">
                {history.length > 0 && (
                  <select
                    className="query-history"
                    value=""
                    aria-label={t('query.history')}
                    onChange={event => { if (event.target.value) setQuestion(event.target.value) }}
                  >
                    <option value="">{t('query.history')}</option>
                    {history.map(item => <option key={item} value={item}>{item}</option>)}
                  </select>
                )}
                {phase === 'generating' ? (
                  <button type="button" className="query-run is-cancel" onClick={cancel}>
                    {t('query.cancel')}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="query-run"
                    disabled={Boolean(runBlockedReason) || phase === 'executing' || executing}
                    aria-busy={phase === 'executing' || executing}
                    title={runBlockedReason || t('query.runHint')}
                    onClick={() => run()}
                  >
                    {phase === 'executing' || executing ? t('query.executing') : t('query.run')}
                  </button>
                )}
                {phase !== 'generating' && runBlockedReason && (
                  <small className="query-run-blocked" id="query-run-blocked-reason">{runBlockedReason}</small>
                )}
              </div>
            </div>

            <div className="query-mode-row">
              <span className="query-mode-caption">{t('query.modeLabel')}</span>
              <div className="query-mode" role="group" aria-label={t('query.modeLabel')}>
                <button
                  type="button"
                  className={mode === 'direct' ? 'active' : ''}
                  aria-pressed={mode === 'direct'}
                  title={t('query.modeDirectHint')}
                  onClick={() => setMode('direct')}
                >
                  {t('query.modeDirect')}
                </button>
                <button
                  type="button"
                  className={mode === 'workflow' ? 'active' : ''}
                  aria-pressed={mode === 'workflow'}
                  title={t('query.modeWorkflowHint')}
                  onClick={() => setMode('workflow')}
                >
                  {t('query.modeWorkflow')}
                </button>
              </div>

              {mode === 'direct' && generators.length > 0 && (
                <label className="query-inline-select">
                  <span>{t('query.generator')}</span>
                  <select value={generator} onChange={event => setGenerator(event.target.value)}>
                    {generators.map(item => <option key={item} value={item}>{item}</option>)}
                  </select>
                </label>
              )}

              {mode === 'workflow' && composeActors.length > 0 && (
                <button
                  type="button"
                  className={`query-compose-chip ${workflowSource === 'compose' ? 'active' : ''}`}
                  aria-pressed={workflowSource === 'compose'}
                  onClick={() => setWorkflowSource('compose')}
                >
                  {focusedPairLabel}
                </button>
              )}

              {mode === 'workflow' && (
                <label className="query-inline-select" title={t('query.skeletonHint')}>
                  <span>{t('query.skeleton')}</span>
                  <select
                    aria-label={t('query.skeleton')}
                    value={workflowSource === 'compose' ? '' : String(skeletonIndex)}
                    onChange={event => {
                      if (event.target.value === '') return
                      setSkeletonIndex(Number(event.target.value))
                      setWorkflowSource('skeleton')
                    }}
                  >
                    {workflowSource === 'compose' && <option value="">{t('query.composeInherited')}</option>}
                    {workflows.map((item, index) => (
                      <option key={item.join('-')} value={index}>{item.join(' → ')}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <p className="query-mode-hint">
              {mode === 'direct' ? t('query.modeDirectHint') : t('query.modeWorkflowHint')}
            </p>

            {mode === 'workflow' && workflowSource === 'compose' && composeActors.length > 0 && (
              <div className="query-actor-chips" data-testid="query-compose-actors">
                {composeActors.map((actor, index) => (
                  <span key={`${actor}-${index}`}><i>{index + 1}</i>{actor}</span>
                ))}
                <small>{t('query.composeInheritedHint')}</small>
              </div>
            )}

            {mode === 'workflow' && workflowSource === 'skeleton' && (
              <div className="query-actor-selects">
                {skeleton.map(type => (
                  <label key={type} className="query-inline-select">
                    <span>{type}</span>
                    <select
                      value={actorSelections[type] || ''}
                      aria-label={t('query.actorFor', { type })}
                      onChange={event => setActorSelections(current => ({ ...current, [type]: event.target.value }))}
                    >
                      {(actorsByType[type] || []).map(item => <option key={item} value={item}>{item}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            )}
          </div>

          {!hasRun && (
            <div className="query-empty" data-testid="query-empty">
              <b>{t('query.emptyTitle')}</b>
              <ol className="query-steps">
                <li className={selectedDb ? 'is-done' : 'is-current'}>
                  <i aria-hidden="true">{selectedDb ? '✓' : '1'}</i>
                  <div>
                    <span>{t('query.emptyStepDatabase')}</span>
                    {selectedDb && <small>{selectedDb}</small>}
                  </div>
                </li>
                <li className={question.trim() ? 'is-done' : selectedDb ? 'is-current' : ''}>
                  <i aria-hidden="true">{question.trim() ? '✓' : '2'}</i>
                  <div>
                    <span>{t('query.emptyStepQuestion')}</span>
                  </div>
                </li>
                <li className={selectedDb && question.trim() && credentialReady ? 'is-current' : ''}>
                  <i aria-hidden="true">3</i>
                  <div>
                    <span>{t('query.emptyStepPipeline')}</span>
                    {Boolean(selectedDb && question.trim() && credentialReady) && (
                      <small>{t('query.stepReadyHint')}</small>
                    )}
                  </div>
                </li>
              </ol>
              {credentialReady && examples.length > 0 && (
                <div className="query-examples">
                  <span>{t('query.examples')}</span>
                  {examples.map(example => (
                    <button
                      key={example}
                      type="button"
                      title={t('query.exampleHint')}
                      onClick={() => {
                        setQuestion(example)
                        if (selectedDb) run(example)
                        else questionRef.current?.focus?.()
                      }}
                    >
                      {example}
                    </button>
                  ))}
                  <small className="query-examples-hint">{t('query.exampleHint')}</small>
                </div>
              )}
            </div>
          )}

          {hasRun && displayStages.length > 0 && (
            <div className="query-stages" data-testid="query-stages" ref={stagesRef}>
              <header>
                <span>{t('query.stagesEyebrow')}</span>
              </header>
              <div className="query-stage-strip">
                {displayStages.map((stage, index) => (
                  <StageChip
                    key={stage.id}
                    stage={stage}
                    active={phase === 'generating' && index === Math.min(activeStageIndex, pipelineStages.length - 1)}
                    detailOpen={stageDetailId === stage.id}
                    totalElapsed={totalElapsed}
                    onOpenDetail={() => setStageDetailId(current => (current === stage.id ? null : stage.id))}
                    t={t}
                  />
                ))}
              </div>
              {detailStage && (
                <div className="query-stage-detail" data-testid="query-stage-detail">
                  <header>
                    <b>{t('query.stageDetails')}</b>
                    <button type="button" onClick={() => setStageDetailId(null)}>{t('query.closeDetails')}</button>
                  </header>
                  <dl>
                    <div><dt>Actor</dt><dd>{detailStage.actor}</dd></div>
                    {detailStage.stage && <div><dt>Stage</dt><dd>{detailStage.stage}</dd></div>}
                    <div>
                      <dt>Status</dt>
                      <dd>{stageStatusLabel(detailStage.status, t)}</dd>
                    </div>
                    {detailStage.elapsedMs != null && (
                      <div><dt>Elapsed</dt><dd>{formatElapsedMs(detailStage.elapsedMs)}</dd></div>
                    )}
                    {detailStage.elapsedMs != null && totalElapsed > 0 && (
                      <div><dt>%</dt><dd>{t('query.stageShare', { percent: Math.round((detailStage.elapsedMs / totalElapsed) * 100) })}</dd></div>
                    )}
                  </dl>
                </div>
              )}
            </div>
          )}

          {genError && (
            <div className="query-error" role="alert">
              <p>{genError}</p>
              <button type="button" onClick={askPi}>{t('query.askPi')}</button>
            </div>
          )}

          {hasRun && (
            <div className={`query-sql-panel ${editorFlash ? 'is-flash' : ''}`} data-testid="query-sql-panel">
              <header>
                <span>{t('query.sqlEyebrow')}</span>
                <b>{t('query.sqlTitle')}</b>
                {sqlEdited && <i className="query-sql-edited">{t('query.sqlModified')}</i>}
                <div className="query-sql-actions">
                  {sqlEdited && (
                    <button type="button" onClick={() => setSql(baselineSql)}>{t('query.sqlReset')}</button>
                  )}
                  <button type="button" onClick={() => copyText(sql, t('query.sqlCopied'))}>{t('query.sqlCopy')}</button>
                  <button type="button" onClick={askPi}>{t('query.askPi')}</button>
                  <button
                    type="button"
                    className="query-execute"
                    disabled={!sql.trim() || !selectedDb || executing || phase === 'generating'}
                    onClick={execute}
                  >
                    {executing ? t('query.executing') : t('query.execute')}
                  </button>
                </div>
              </header>
              <Editor
                ref={editorRef}
                value={sql}
                onChange={setSql}
                onSubmit={execute}
                schema={completionSchema}
                placeholder={t('query.sqlPlaceholder')}
                ariaLabel={t('query.sqlTitle')}
                disabled={phase === 'generating'}
              />
            </div>
          )}

          {hasRun && (result || execError) && (
            <div className="query-results" data-testid="query-results" ref={resultsRef}>
              <header>
                <span>{t('query.resultsEyebrow')}</span>
                {result && (
                  <b>{t('query.resultsSummary', { count: result.row_count, elapsed: result.elapsed_ms })}</b>
                )}
                {result?.truncated && <i className="query-truncated">{t('query.resultsTruncated')}</i>}
                {result && (
                  <button type="button" className="query-export" onClick={exportCsv}>
                    {t('query.exportCsv')}
                  </button>
                )}
              </header>
              {execError ? (
                <div className="query-error" role="alert">
                  <p>{execError}</p>
                  <button type="button" onClick={askPi}>{t('query.askPi')}</button>
                </div>
              ) : (
                <ResultsTable
                  result={result}
                  onCopyCell={value => copyText(value == null ? 'NULL' : value, t('query.cellCopied'))}
                  t={t}
                />
              )}
            </div>
          )}
        </div>
      </div>

      <span className="query-live-region" role="status" aria-live="polite">{liveMessage}</span>
      {toast && <div className="query-toast" role="status">{toast.text}</div>}
    </section>
  )
}
