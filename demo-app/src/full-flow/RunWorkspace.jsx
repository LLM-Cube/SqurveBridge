import DashboardMetrics from '../DashboardMetrics.jsx'
import { useEffect, useMemo, useState } from 'react'
import {
  configKey,
  connectionKeyOf,
  resolveFocusedConfig,
  withConnectionKeys,
} from './model.js'
import LiveRun from './LiveRun.jsx'
import { experimentCells, variantLabel } from './variantModel.js'
import { planPayload, planSummary } from './experimentPlan.js'

export const RUN_PHASES = [
  'ready',
  'loadingData',
  'buildingWorkflow',
  'generatingSql',
  'executingSql',
  'evaluating',
  'completed',
  'failed',
]

const MAX_BATCH_TARGETS = 24

export const INITIAL_RUN_STATE = {
  phase: 'ready',
  sql: '',
  trace: [],
  result: null,
  error: '',
  busy: false,
  context: null,
}

export function summarizeRunProgress(log, job) {
  // Prefer server progress computed from the full run.log (UI log is a truncated tail).
  const server = job?.progress
  if (server && typeof server.percent === 'number') {
    return {
      currentStage: server.current_stage || '准备中',
      started: Number(server.started) || 0,
      completed: Number(server.completed) || 0,
      total: Number(server.total) || 1,
      percent: Math.min(100, Math.max(0, Number(server.percent) || 0)),
    }
  }

  const text = String(log || '')
  const target = Number(job?.sample_limit) || 0
  const started = new Set([...text.matchAll(/开始处理样本\s+(\S+)/g)].map(match => match[1]))
  const finished = new Set([...text.matchAll(/样本\s+(\S+)\s+处理完成/g)].map(match => match[1]))
  const stageHits = [...text.matchAll(/样本\s+(\S+)\s+@\s+(\S+)/g)]
  for (const match of stageHits) started.add(match[1])
  let currentStage = stageHits.at(-1)?.[2] || '准备中'
  const reportMatch = text.match(/评估结果[^\n]*\n\s*样本\s+(\d+)\s*条/)
  const reportTotal = reportMatch ? Number(reportMatch[1]) : 0
  if (reportTotal) currentStage = '评估完成'

  const total = target || reportTotal || Math.max(started.size, finished.size, 1)
  let completed = finished.size
  if (job?.status === 'completed' || reportTotal) {
    completed = Math.max(completed, reportTotal || total)
    if (job?.status === 'completed') currentStage = '评估完成'
  }
  completed = Math.min(completed, total)
  const startedCount = Math.max(started.size, completed)
  const percent = job?.status === 'completed'
    ? 100
    : Math.min(100, Math.round((completed / total) * 100))
  return { currentStage, started: startedCount, completed, total, percent }
}

const sensitivePatterns = [
  [/\b(authorization\s*:\s*)?bearer\s+[^\s;,]+/gi, (_, authorization = '') => `${authorization}Bearer [redacted]`],
  [/\b([a-z0-9_]*(?:api_key|access_token|auth_token|secret_key|client_secret|password))\s*=\s*[^\s;,]+/gi, '$1=[redacted]'],
  [/\b(api[\s_-]*key)\s*[:=]\s*[^\s;,]+/gi, '$1: [redacted]'],
  [/\b((?:openai|deepseek|dashscope|qwen|anthropic|gemini|groq|mistral)\s+(?:api\s+)?key)\s*[:=]\s*[^\s;,]+/gi, '$1: [redacted]'],
  [/\b(?:sk[_-]?live[_-]?|sk[_-]?test[_-]?|sk-|hf_|ghp_|github_pat_|AIza)[A-Za-z0-9_-]{6,}\b/g, '[redacted]'],
]

export function sanitizeRunError(error) {
  const fallback = 'Request failed'
  return sensitivePatterns.reduce(
    (message, [pattern, replacement]) => message.replace(pattern, replacement),
    String(error?.message || fallback),
  )
}

export const databaseIdFor = (databases, focusedDatabase) =>
  (databases || []).find(
    database => String(database.id).toLowerCase() === String(focusedDatabase).toLowerCase()
      || String(database.benchmark || '').toLowerCase() === String(focusedDatabase).toLowerCase(),
  )?.id || ''

const actorNames = config => (config?.stages || [])
  .map(stage => stage.actor)
  .filter(Boolean)

const jobTone = status => {
  if (status === 'completed') return 'completed'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  if (status === 'running' || status === 'resuming' || status === 'starting') return 'current'
  return 'pending'
}

const isActiveJob = job => ['queued', 'running', 'resuming', 'starting'].includes(job?.status)
const QUEUED_PREFIX = 'queued:'
const isLaunchedJob = job => Boolean(job?.job_id) && !String(job.job_id).startsWith(QUEUED_PREFIX)

/** One row per experiment cell: launched cells keep their known job detail, queued ones get a placeholder. */
export function jobsFromExperiment(experiment, previousJobs = []) {
  if (!experiment?.cells) return previousJobs
  const known = new Map(previousJobs.map(job => [job.job_id, job]))
  return experiment.cells.map(cell => {
    if (!cell.job_id) {
      return {
        job_id: `${QUEUED_PREFIX}${cell.cell_id}`,
        cell_id: cell.cell_id,
        method: cell.method,
        dataset: cell.dataset,
        label: cell.label,
        status: cell.status,
        queue_position: cell.queue_position,
        error_code: cell.error_code,
      }
    }
    const previous = known.get(cell.job_id) || { job_id: cell.job_id, method: cell.method, dataset: cell.dataset }
    return {
      ...previous,
      cell_id: cell.cell_id,
      queue_position: cell.queue_position,
      label: cell.label,
      status: TERMINAL_JOB_STATUSES.has(previous.status) && !TERMINAL_JOB_STATUSES.has(cell.status)
        ? previous.status
        : cell.status,
      error_code: cell.error_code,
    }
  })
}

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/** Merge a detail poll into local job state without regressing terminal status or dumping the log. */
export function mergeJobDetail(job, detail) {
  if (!job || !detail) return job
  if (detail.job_id && detail.job_id !== job.job_id) return job
  const incoming = detail.status ?? job.status
  const keepTerminal = TERMINAL_JOB_STATUSES.has(job.status) && isActiveJob({ status: incoming })
  const status = keepTerminal ? job.status : incoming
  return {
    ...job,
    status,
    progress: keepTerminal ? (job.progress ?? detail.progress) : (detail.progress ?? job.progress),
    checkpoint_present: detail.checkpoint_present ?? job.checkpoint_present,
    resumable: detail.resumable ?? job.resumable,
    resume_count: detail.resume_count ?? job.resume_count,
    max_resume_attempts: detail.max_resume_attempts ?? job.max_resume_attempts,
    result: detail.result ?? job.result,
    artifact: detail.artifact ?? job.artifact,
    run_id: detail.run_id ?? job.run_id,
  }
}

export const isResumableJob = job => Boolean(
  (job?.cell_id && !isLaunchedJob(job) && ['failed', 'cancelled'].includes(job?.status))
  || job?.resumable
  || (['failed', 'cancelled'].includes(job?.status) && job?.checkpoint_present),
)

const phaseFromJobs = jobs => {
  if (!jobs.length) return 'ready'
  if (jobs.some(isActiveJob)) return 'evaluating'
  if (jobs.some(job => job.status === 'failed' || job.status === 'cancelled')) return 'failed'
  if (jobs.every(job => job.status === 'completed')) return 'completed'
  return 'ready'
}

const reproduceCommand = (dataset, method, { resume = false } = {}) => (
  resume
    ? `python reproduce/run.py ${dataset} ${method} --resume`
    : `python reproduce/run.py ${dataset} ${method}`
)

const jobResumeLabel = (job, t) => {
  const count = Number(job?.resume_count) || 0
  const max = Number(job?.max_resume_attempts)
  if (!count && !job?.checkpoint_present && !job?.resumable) return ''
  if (Number.isFinite(max) && max >= 0) {
    return t('run.resumeMeta', { count, max })
  }
  return t('run.resumeCount', { count })
}

const percentLabel = value => {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}


const metricAverage = block => {
  if (block == null) return null
  if (typeof block === 'number') return block
  if (typeof block === 'object' && typeof block.avg === 'number') return block.avg
  return null
}

export function resolveJobArtifact(job) {
  const result = job?.result
  const artifact = job?.artifact
  if (!result && !artifact) return null
  const metrics = result?.metrics || {}
  const aggregate = artifact?.aggregate || {}
  const keys = ['ex', 'em', 'sf1', 'ves', 'rves']
  const metricCards = keys.map(key => ({
    key,
    label: key.toUpperCase(),
    value: metrics[key] ?? metricAverage(aggregate[key]),
  }))
  const componentF1 = Object.entries(result?.component_f1 || {})
    .map(([name, value]) => ({ name, value: Number(value) }))
    .filter(item => Number.isFinite(item.value))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)
  const errorRoots = Array.isArray(result?.error_roots)
    ? result.error_roots.slice(0, 6)
    : Object.entries(artifact?.errors || {})
      .map(([name, value]) => ({
        name,
        count: value?.count,
        rate: value?.pct ?? value?.rate,
      }))
      .sort((a, b) => (Number(b.count) || 0) - (Number(a.count) || 0))
      .slice(0, 6)
  const stages = Array.isArray(result?.stages) ? result.stages : []
  const token = result?.token || artifact?.token || {}
  const latency = artifact?.latency || {}
  return {
    runId: result?.run_id || artifact?.run_id || job?.run_id || job?.job_id,
    method: result?.method || artifact?.method || job?.method,
    dataset: result?.dataset || artifact?.dataset || job?.dataset,
    split: result?.split || artifact?.split || '—',
    sampleCount: result?.sample_count ?? artifact?.sample_count ?? job?.sample_limit,
    artifactRef: artifact?.artifact_ref || '',
    metricCards,
    componentF1,
    errorRoots,
    stages,
    token,
    latency,
  }
}


function RunArtifactPanel({ job, t }) {
  const artifact = resolveJobArtifact(job)
  if (!artifact) return null
  return (
    <section className="run-artifact-panel" data-testid="run-artifact-panel">
      <header className="run-artifact-head">
        <div>
          <span>{t('run.artifactTitle')}</span>
          <strong>{artifact.method} / {artifact.dataset}</strong>
          <small>{artifact.runId}</small>
        </div>
        <div className="run-artifact-meta">
          <span>{t('run.artifactSplit')} <b>{artifact.split}</b></span>
          <span>{t('run.artifactSamples')} <b>{artifact.sampleCount ?? '—'}</b></span>
          {artifact.artifactRef ? <span><b>{artifact.artifactRef}</b></span> : null}
        </div>
      </header>

      <DashboardMetrics runs={[{
        ...job.artifact, ...job.result,
        run_id: artifact.runId, method: artifact.method, dataset: artifact.dataset,
        split: artifact.split, sample_count: artifact.sampleCount,
        aggregate: {
          ...job.artifact?.aggregate,
          ...Object.fromEntries(artifact.metricCards.map(card => [card.key, { avg: card.value }])),
          cf1: Object.fromEntries(artifact.componentF1.map(item => [`cf1_${item.name}`, { avg: item.value }])),
        },
        token: artifact.token, latency: artifact.latency,
        errors: Object.fromEntries(artifact.errorRoots.map(item => [item.name, { count: item.count, pct: item.rate }])),
      }]} t={t} />

      {artifact.stages.length ? (
        <div className="run-artifact-stages">
          <h4>{t('run.artifactStages')}</h4>
          <ol>
            {artifact.stages.map(stage => (
              <li key={stage.name || stage.task_type}>
                <strong>{stage.name || stage.task_type || 'stage'}</strong>
                <small>{stage.task_type || ''}</small>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  )
}

function describeTarget(connection, configs, databases) {
  const config = resolveFocusedConfig(configs, connection.method, connection.database)
  const liveId = databaseIdFor(databases, connection.database)
  return {
    ...connection,
    key: connection.key || connectionKeyOf(connection),
    config,
    liveId,
    configBacked: Boolean(config),
    runnable: Boolean(config),
  }
}

// With the live view, the per-job list, progress and raw log are kept for debugging, folded away.
function BatchMonitorFrame({ live, summary, children }) {
  if (!live) return children
  return <details className="run-batch-details"><summary>{summary}</summary>{children}</details>
}

export default function RunWorkspace({
  selectedConnections = [],
  configs = [],
  focusedMethod,
  focusedDatabase,
  onFocusConnection,
  databases,
  sampleLimit,
  sampleMode,
  sampleSeed,
  postJson,
  api,
  onRunStateChange,
  onExperimentChange,
  adoptExperimentId = '',
  liveEvaluation = false,
  variants = [],
  plan,
  credentialConfigured,
  onConfigureSql,
  onSubmission,
  onExperimentSnapshot,
  onEditPlan,
  onViewComparison,
  t,
}) {
  const connections = useMemo(
    () => withConnectionKeys(selectedConnections).map(item => describeTarget(item, configs, databases)),
    [selectedConnections, configs, databases],
  )
  const focusedKey = configKey(focusedMethod, focusedDatabase)

  const [jobs, setJobs] = useState([])
  const [experiment, setExperiment] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Cells the server refused, with the reason for each, so the user can fix them on the Compose page.
  const [rejections, setRejections] = useState([])
  const [selectedJobId, setSelectedJobId] = useState('')
  const [log, setLog] = useState('')
  const progress = summarizeRunProgress(log, jobs.find(job => job.job_id === selectedJobId))

  const primaryTarget = connections.find(item => item.key === focusedKey)
    || connections[0]
    || null
  const runTargets = connections
    .filter(item => item.runnable)
  const draft = plan || {
    cells: experimentCells(selectedConnections.map(item => ({ method: item.method, dataset: item.database })), variants),
    sampling: { limit: sampleLimit, mode: sampleMode, seed: sampleSeed },
  }
  const summary = planSummary(draft, { configs, credentialConfigured })
  const primaryConfig = primaryTarget?.config || null
  const actors = useMemo(() => actorNames(primaryConfig), [primaryConfig])
  const selectedJob = jobs.find(job => job.job_id === selectedJobId) || null
  const resumableJobs = jobs.filter(isResumableJob)
  const commandPreview = (() => {
    if (selectedJob && isResumableJob(selectedJob)) {
      return reproduceCommand(selectedJob.dataset, selectedJob.method, { resume: true })
    }
    if (runTargets.length === 1) {
      return reproduceCommand(runTargets[0].config.dataset, runTargets[0].config.method)
    }
    if (runTargets.length > 1) {
      return `python reproduce/run.py <dataset> <method> × ${runTargets.length}`
    }
    return 'python reproduce/run.py <dataset> <method>'
  })()

  const publishRunState = (phase, additions = {}) => {
    const context = primaryTarget
      ? Object.freeze({
        method: primaryTarget.method,
        database: primaryTarget.database,
        db_id: primaryTarget.liveId || null,
        config_path: primaryConfig?.config_path || null,
        actors: Object.freeze([...actors]),
        command: commandPreview,
      })
      : null
    onRunStateChange?.({
      ...INITIAL_RUN_STATE,
      phase,
      busy: phase === 'evaluating' || busy,
      context,
      error: additions.error || '',
      ...additions,
    })
  }

  const activeJobs = jobs.filter(isActiveJob)
  const runnable = Boolean(
    liveEvaluation
    && summary.runnable
    && !busy
    && !activeJobs.length,
  )
  const canResume = Boolean(liveEvaluation && resumableJobs.length && !busy && !activeJobs.length)

  const refreshJobs = async current => {
    if (!api || !current.length) return current
    return Promise.all(current.map(async job => {
      if (!isLaunchedJob(job)) return job
      try {
        return { ...job, ...await api(`/api/evaluations/${job.job_id}`) }
      } catch {
        return job
      }
    }))
  }

  useEffect(() => {
    if (!api || adoptExperimentId || experiment?.experiment_id) return undefined
    let active = true
    api('/api/session').then(data => {
      if (!active) return
      const restored = Array.isArray(data?.jobs) ? data.jobs : []
      setJobs(restored)
      setSelectedJobId(current => current || restored[0]?.job_id || '')
      if (restored.length) publishRunState(phaseFromJobs(restored))
    }).catch(() => {})
    return () => { active = false }
  }, [api, adoptExperimentId, experiment?.experiment_id])

  // An experiment started elsewhere (the Agent, after the user confirmed) is shown here too.
  useEffect(() => {
    if (!api || !adoptExperimentId || experiment?.experiment_id === adoptExperimentId) return undefined
    let active = true
    api(`/api/experiments/${adoptExperimentId}`).then(latest => {
      if (!active || !latest?.cells) return
      const adopted = jobsFromExperiment(latest, [])
      setExperiment(latest)
      onExperimentSnapshot?.(latest)
      setJobs(adopted)
      setSelectedJobId(adopted[0]?.job_id || '')
      publishRunState(phaseFromJobs(adopted))
    }).catch(() => {})
    return () => { active = false }
  }, [api, adoptExperimentId, onExperimentSnapshot])

  useEffect(() => {
    if ((!jobs.some(isActiveJob) && !['running', 'queued'].includes(experiment?.status)) || !api) return undefined
    let active = true
    const timer = setInterval(() => {
      const withCells = experiment?.experiment_id
        ? api(`/api/experiments/${experiment.experiment_id}`)
          .then(latest => {
            if (!active) return jobs
            setExperiment(latest)
            onExperimentSnapshot?.(latest)
            return jobsFromExperiment(latest, jobs)
          })
          .catch(() => jobs)
        : Promise.resolve(jobs)
      withCells.then(refreshJobs).then(next => {
        if (!active) return
        setJobs(next)
        publishRunState(phaseFromJobs(next))
      }).catch(() => {})
    }, 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [jobs, api, experiment?.experiment_id])

  useEffect(() => {
    if (!api || !selectedJobId || String(selectedJobId).startsWith(QUEUED_PREFIX)) {
      setLog('')
      return undefined
    }
    let active = true
    api(`/api/evaluations/${selectedJobId}`).then(detail => {
      if (!active) return
      setLog(detail.log || '')
      setJobs(current => current.map(job => (
        job.job_id === selectedJobId ? mergeJobDetail(job, detail) : job
      )))
    }).catch(err => {
      if (active) setLog(sanitizeRunError(err))
    })
    return () => { active = false }
  }, [api, selectedJobId])

  useEffect(() => {
    const selected = jobs.find(job => job.job_id === selectedJobId)
    if (selected?.log) setLog(selected.log)
  }, [jobs, selectedJobId])

  const runConfigs = async () => {
    if (!runnable) return
    setBusy(true)
    setError('')
    setRejections([])
    onSubmission?.({ type: 'submitStarted' })
    publishRunState('evaluating')
    try {
      // Every run is an experiment, one cell or many, so Compare & History always has its id.
      const created = await postJson('/api/experiments', planPayload(draft, configs))
      setExperiment(created)
      onExperimentChange?.(created.experiment_id)
      onSubmission?.({ type: 'submitAccepted', experimentId: created.experiment_id })
      onExperimentSnapshot?.(created)
      const nextJobs = jobsFromExperiment(created, [])
      setJobs(nextJobs)
      setSelectedJobId(nextJobs[0]?.job_id || '')
      if (runTargets[0]) onFocusConnection?.(runTargets[0].method, runTargets[0].database)
      publishRunState(phaseFromJobs(nextJobs))
    } catch (err) {
      const message = sanitizeRunError(err)
      setError(message)
      setRejections(Array.isArray(err?.cells) ? err.cells : [])
      onSubmission?.({ type: 'submitRejected', problems: Array.isArray(err?.cells) ? err.cells : [] })
      publishRunState('failed', { error: message })
    } finally {
      setBusy(false)
    }
  }

  const stopRuns = async () => {
    if (!postJson || !activeJobs.length) return
    setBusy(true)
    setError('')
    try {
      let current = jobs
      if (experiment?.experiment_id) {
        const latest = await postJson(`/api/experiments/${experiment.experiment_id}/cancel`, {})
        setExperiment(latest)
        onExperimentSnapshot?.(latest)
        current = jobsFromExperiment(latest, jobs)
      } else {
        await Promise.all(activeJobs.map(job => postJson(`/api/evaluations/${job.job_id}/cancel`, {})))
      }
      const next = await refreshJobs(current)
      setJobs(next)
      publishRunState(phaseFromJobs(next))
    } catch (err) {
      const message = sanitizeRunError(err)
      setError(message)
      publishRunState('failed', { error: message })
    } finally {
      setBusy(false)
    }
  }

  const resumeRuns = async () => {
    if (!postJson || !resumableJobs.length) return
    const targets = selectedJob && isResumableJob(selectedJob)
      ? [selectedJob]
      : resumableJobs
    setBusy(true)
    setError('')
    publishRunState('evaluating')
    try {
      if (experiment?.experiment_id) {
        let latest
        for (const job of targets) {
          latest = await postJson(`/api/experiments/${experiment.experiment_id}/cells/${job.cell_id}/resume`, {})
          setExperiment(latest)
          onExperimentSnapshot?.(latest)
          const next = jobsFromExperiment(latest, [])
          setJobs(next)
          setSelectedJobId(next.find(item => item.cell_id === job.cell_id)?.job_id || next[0]?.job_id || '')
          publishRunState(phaseFromJobs(next))
        }
        return
      }
      const resumed = await Promise.all(
        targets.map(job => postJson(`/api/evaluations/${job.job_id}/resume`, {})),
      )
      const byId = new Map(resumed.map(job => [job.job_id, job]))
      const next = jobs.map(job => byId.get(job.job_id) || job)
      setJobs(next)
      setSelectedJobId(resumed[0]?.job_id || selectedJobId)
      publishRunState(phaseFromJobs(next))
    } catch (err) {
      const message = sanitizeRunError(err)
      setError(message)
      publishRunState('failed', { error: message })
    } finally {
      setBusy(false)
    }
  }

  const cancelCell = async job => {
    if (!experiment?.experiment_id || !job.cell_id || busy) return
    setBusy(true)
    try {
      const latest = await postJson(`/api/experiments/${experiment.experiment_id}/cells/${job.cell_id}/cancel`, {})
      setExperiment(latest)
      onExperimentSnapshot?.(latest)
      const next = jobsFromExperiment(latest, jobs)
      setJobs(next)
      publishRunState(phaseFromJobs(next))
    } catch (err) { setError(sanitizeRunError(err)) }
    finally { setBusy(false) }
  }

  const statusLabel = () => {
    if (busy || jobs.some(isActiveJob)) return t('status.running')
    if (jobs.some(job => job.status === 'cancelled') && !jobs.some(job => job.status === 'failed')) {
      return t('status.cancelled')
    }
    if (jobs.some(job => job.status === 'failed')) return t('status.failed')
    if (jobs.length && jobs.every(job => job.status === 'completed')) return t('status.completed')
    return t('status.ready')
  }

  const liveView = Boolean(liveEvaluation && experiment?.experiment_id && api)

  return <div className="board-section run-workspace">
    <header className="board-section-header">
      <div>
        <h3>{t('board.runSection')}</h3>
      </div>
      <strong>{statusLabel()}</strong>
    </header>

    <div className="run-workspace-layout">
      <section className="run-parameter-console" data-testid="run-parameter-console" aria-labelledby="run-params-title">
        <header>
          <h3 id="run-params-title">{t('run.parameters')}</h3>
          {onEditPlan && <button type="button" className="flow-secondary-action" onClick={onEditPlan}>{t('plan.edit')}</button>}
        </header>

        {!connections.length && <p className="run-empty">{t('run.noConnections')}</p>}
        {!liveEvaluation && <p className="run-batch-note">{t('run.configRunUnavailable')}</p>}
        {summary.reason && <p role="status">{t(`grid.${summary.reason}`)}</p>}
        {summary.reason === 'credentialMissing' && <button type="button" onClick={onConfigureSql}>{t('header.configureLocalApi')}</button>}
        <p>{t('plan.sampling', { limit: sampleLimit, mode: t(sampleMode === 'random' ? 'configure.sampleRandom' : 'configure.sampleSlice'), seed: sampleMode === 'random' ? sampleSeed : '—' })}</p>
        {experiment && <p className="run-observed-plan">{t('plan.observed', { id: experiment.experiment_id, cells: experiment.cells?.length || 0, limit: experiment.sampling?.limit ?? '—', mode: t(experiment.sampling?.mode === 'random' ? 'configure.sampleRandom' : 'configure.sampleSlice'), seed: experiment.sampling?.mode === 'random' ? experiment.sampling.seed : '—' })}</p>}

        <dl className="run-param-summary">
          <div>
            <dt>{t('run.activeTarget')}</dt>
            <dd>{primaryTarget ? `${primaryTarget.method} → ${primaryTarget.database}` : t('status.unavailable')}</dd>
          </div>
          <div>
            <dt>{t('run.composeConnections')}</dt>
            <dd data-testid="run-compose-connections">
              {connections.length
                ? connections.map(item => `${item.method}→${item.database}`).join(', ')
                : t('status.unavailable')}
            </dd>
          </div>
          {variants.length ? (
            <div>
              <dt>{t('variants.runLabel')}</dt>
              <dd data-testid="run-variants">
                {variants.map(item => `${variantLabel(item.method, item.stages)} / ${item.dataset}`).join(', ')}
              </dd>
            </div>
          ) : null}
          <div>
            <dt>{t('run.configPath')}</dt>
            <dd>{primaryConfig?.config_path || t('status.unavailable')}</dd>
          </div>
          <div>
            <dt>{t('run.workflow')}</dt>
            <dd>{actors.length ? actors.join(' → ') : t('status.unavailable')}</dd>
          </div>
          <div>
            <dt>{t('run.configCommand')}</dt>
            <dd data-testid="run-config-command"><code>{commandPreview}</code></dd>
          </div>
        </dl>

        <div className="run-batch-params">
          <p>{t('run.batchTargetCount', { count: runTargets.length, max: MAX_BATCH_TARGETS })}</p>
          <p className="run-plan-summary" data-testid="run-plan-summary">
            {(() => {
              const summary = planSummary({
                cells: [
                  ...runTargets.map(item => ({ method: item.config.method, dataset: item.config.dataset, stages: {} })),
                  ...variants.map(item => ({ method: item.method, dataset: item.dataset, stages: item.stages })),
                ],
                sampling: { limit: sampleLimit },
              })
              return t('plan.summary', {
                cells: summary.cells,
                variants: summary.variants,
                limit: summary.questionsPerCell,
                questions: summary.questions,
              })
            })()}
          </p>
          {!primaryConfig && <p>{t('run.unavailable')}</p>}
        </div>

        <div className="run-action-bar">
          {experiment?.experiment_id && onViewComparison && <button type="button" className="flow-secondary-action" onClick={() => onViewComparison(experiment.experiment_id)}>{t('plan.viewComparison')}</button>}
          <button
            type="button"
            className="run-primary-action"
            disabled={!runnable}
            onClick={runConfigs}
          >
            {t('run.action')}
          </button>
          <button
            type="button"
            className="run-resume-action"
            data-testid="run-resume-action"
            disabled={!canResume}
            onClick={resumeRuns}
          >
            {t('run.resume')}
          </button>
          <button
            type="button"
            className="run-stop-action"
            data-testid="run-stop-action"
            disabled={!liveEvaluation || !activeJobs.length || busy}
            onClick={stopRuns}
          >
            {t('run.stop')}
          </button>
        </div>
      </section>
    </div>

    {liveView ? (
      <LiveRun
        key={experiment.experiment_id} t={t} api={api} experimentId={experiment.experiment_id}
        onFinished={() => onViewComparison?.(experiment.experiment_id)}
        onCancelCell={cellId => {
          const job = jobs.find(item => item.cell_id === cellId)
          if (job) cancelCell(job)
        }}
      />
    ) : null}
    <BatchMonitorFrame live={liveView} summary={t('run.batchDetails')}>
    <div className="run-batch-monitor" data-testid="run-batch-monitor">
      <div className="run-batch-jobs">
        <h3>{t('run.batchJobs')}</h3>
        {!jobs.length
          ? <p className="run-empty">{t('run.batchEmpty')}</p>
          : <ul>
            {jobs.map(job => {
              const resumeLabel = jobResumeLabel(job, t)
              return (
                <li key={job.job_id} data-state={jobTone(job.status)}>
                  <button
                    type="button"
                    className={selectedJobId === job.job_id ? 'active' : ''}
                    onClick={() => setSelectedJobId(job.job_id)}
                  >
                    <span className="run-batch-job-main">
                      <strong>{job.label || job.method} / {job.dataset}</strong>
                      <span className="run-batch-job-status">{job.status}</span>
                    </span>
                    {(resumeLabel || isResumableJob(job)) && (
                      <span className="run-batch-job-meta">
                        {resumeLabel && <small>{resumeLabel}</small>}
                        {isResumableJob(job) && <em>{t('run.checkpointReady')}</em>}
                      </span>
                    )}
                    {job.queue_position != null && <small>{t('plan.queuePosition', { position: job.queue_position })}</small>}
                    {job.error_code && <small>{job.error_code}</small>}
                  </button>
                  {experiment?.experiment_id && isActiveJob(job) && <button type="button" disabled={busy} onClick={() => cancelCell(job)} aria-label={t('plan.cancelCell', { name: job.label || job.method, dataset: job.dataset })}>{t('plan.cancelCellAction')}</button>}
                </li>
              )
            })}
          </ul>}
      </div>
      <div className="run-progress-card">
        <div className="run-progress-heading">
          <div><strong>{progress.percent}%</strong><span>{progress.currentStage}</span></div>
          <small>{progress.completed} / {progress.total} {t('run.sampleUnit')}</small>
        </div>
        <div className="run-progress-track" role="progressbar" aria-valuenow={progress.percent} aria-valuemin="0" aria-valuemax="100">
          <span style={{ width: `${progress.percent}%` }} />
        </div>
        <div className="run-progress-meta">
          <span>{t('run.samplesStarted', { count: progress.started })}</span>
          <span>{selectedJob?.status || t('run.waiting')}</span>
        </div>
        {selectedJob && (selectedJob.checkpoint_present || selectedJob.resume_count > 0) && (
          <p className="run-resume-note" data-testid="run-resume-note">
            {t('run.resumeHint', {
              count: Number(selectedJob.resume_count) || 0,
              max: Number(selectedJob.max_resume_attempts) || 0,
              command: reproduceCommand(selectedJob.dataset, selectedJob.method, { resume: true }),
            })}
          </p>
        )}
        <details className="run-log-details">
          <summary>{t('run.viewDebugLog')}</summary>
          <pre className="run-batch-log" aria-label={t('run.batchLog')}>{log || t('run.batchLogEmpty')}</pre>
        </details>
        {selectedJob?.result || selectedJob?.artifact
          ? <RunArtifactPanel job={selectedJob} t={t} />
          : selectedJob?.status === 'completed'
            ? <p className="run-artifact-pending" data-testid="run-artifact-pending">{t('run.artifactPending')}</p>
            : null}
      </div>
    </div>
    </BatchMonitorFrame>

    {error &&<p className="error-banner" role="alert">{error}</p>}
    {rejections.length > 0 && (
      <ul className="run-rejections" data-testid="run-rejections">
        {rejections.map(item => (
          <li key={`${item.method}/${item.dataset}/${item.error_code || 'stage'}`}>
            {item.method} / {item.dataset}: {item.error_code || (item.stages || []).map(problem => problem.reason).join(', ')}
          </li>
        ))}
      </ul>
    )}
  </div>
}
