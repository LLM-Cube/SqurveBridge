import assert from 'node:assert/strict'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { act, cleanup, render, screen, waitFor } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()

const {
  default: RunWorkspace,
  jobsFromExperiment,
  mergeJobDetail,
  resolveJobArtifact,
  sanitizeRunError,
  summarizeRunProgress,
} = await import('./RunWorkspace.jsx')

const translations = {
  'board.runSection': 'Run & inspect',
  'run.noConnections': 'No connections yet.',
  'run.composeConnections': 'Compose connections',
  'run.parameters': 'Parameter console',
  'run.configRunUnavailable': 'Config-script runs are available in the local demo only.',
  'run.configCommand': 'Config command',
  'run.activeTarget': 'Active target',
  'run.configPath': 'Config path',
  'run.workflow': 'Workflow',
  'run.batchTargetCount': '{count} Compose connections · max {max}',
  'run.batchJobs': 'Config jobs',
  'run.batchEmpty': 'No config jobs yet.',
  'run.batchLog': 'Selected job log',
  'run.batchLogEmpty': 'Waiting for job output…',
  'run.viewDebugLog': 'View debug log',
  'run.artifactTitle': 'Run artifacts',
  'run.artifactPending': 'Score bundle is not available for this completed job yet.',
  'run.artifactSplit': 'Split',
  'run.artifactSamples': 'Samples',
  'run.artifactTokens': 'Tokens',
  'run.artifactLatency': 'Latency',
  'run.artifactComponentF1': 'Component F1',
  'run.artifactErrorRoots': 'Error roots',
  'run.artifactStages': 'Actor stages',
  'run.artifactEmptySlice': 'No slice evidence in this score bundle.',
  'run.action': 'Run config',
  'run.stop': 'Stop run',
  'run.resume': 'Resume run',
  'run.resumeMeta': 'Resume {count}/{max}',
  'run.resumeCount': 'Resumed {count}×',
  'run.checkpointReady': 'Resumable',
  'run.resumeHint': 'Checkpoint ready · resume {count}/{max} · same as {command}',
  'run.sampleUnit': 'samples',
  'plan.summary': '{cells} cells · {variants} variants · {limit} questions each · {questions} questions in total',
  'run.samplesStarted': '{count} samples started',
  'run.waiting': 'Waiting to run',
  'run.unavailable': 'This configuration is unavailable.',
  'run.databaseUnavailable': 'The focused database is not available in the live runtime.',
  'configure.sampleLimit': 'Sample limit',
  'configure.sampleMode': 'Sample mode',
  'configure.sampleSeed': 'Sample seed',
  'configure.sampleSlice': 'Dev slice',
  'configure.sampleRandom': 'Random',
  'inspect.title': 'Result Inspection',
  'inspect.description': 'Inspect evidence returned by the current run.',
  'inspect.sql': 'SQL',
  'inspect.result': 'Result',
  'inspect.trace': 'Trace',
  'inspect.metrics': 'Metrics',
  'inspect.logs': 'Logs',
  'inspect.empty': 'Run a workflow to inspect its artifacts.',
  'inspect.evidenceRequired': 'Evidence is required for this view.',
  'inspect.noSql': 'No SQL was returned.',
  'inspect.noResult': 'No execution result was returned.',
  'inspect.noTrace': 'No trace was returned.',
  'inspect.runContext': 'Run context',
  'inspect.question': 'Question',
  'inspect.artifactRef': 'Artifact ref',
  'inspect.contextDatabase': 'Live database',
  'inspect.contextConfig': 'Configuration',
  'inspect.contextActors': 'Actors',
  'status.ready': 'Ready',
  'status.running': 'Running',
  'status.cancelled': 'Cancelled',
  'status.completed': 'Completed',
  'status.failed': 'Failed',
  'status.unavailable': 'Unavailable',
}

const t = (key, params = {}) => Object.entries(params).reduce(
  (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
  translations[key] || key,
)

const baseConfig = {
  method: 'dinsql',
  dataset: 'spider',
  config_path: 'reproduce/configs/spider/dinsql.json',
  stages: [{ id: 'generate', type: 'GenerateTask', actor: 'DINSQLGenerator' }],
}

function renderRun(overrides = {}) {
  const props = {
    selectedConnections: [{ method: 'DINSQL', database: 'Spider' }],
    configs: [baseConfig],
    focusedMethod: 'DINSQL',
    focusedDatabase: 'Spider',
    onFocusConnection: () => {},
    databases: [{ id: 'Spider' }],
    sampleLimit: 20,
    sampleMode: 'slice',
    sampleSeed: 42,
    onSampleLimitChange: () => {},
    onSampleModeChange: () => {},
    onSampleSeedChange: () => {},
    postJson: async () => ({}),
    api: async () => ({}),
    onRunStateChange: () => {},
    liveEvaluation: true,
    t,
    ...overrides,
  }
  return render(React.createElement(RunWorkspace, props))
}

test.afterEach(cleanup)

test.after(() => {
  unregister()
  closeDom()
})

test('a failed experiment cell resumes through its experiment without losing its identity', async () => {
  const calls = []
  const snapshot = status => ({ experiment_id: 'exp-resume', status, sampling: { limit: 20, mode: 'slice', seed: 42 }, cells: [{ cell_id: 'c1', method: 'dinsql', dataset: 'spider', job_id: 'job-cell', status }] })
  renderRun({
    adoptExperimentId: 'exp-resume',
    api: async path => path === '/api/experiments/exp-resume' ? snapshot('failed') : path === '/api/evaluations/job-cell' ? { job_id: 'job-cell', status: 'failed', checkpoint_present: true, resumable: true } : {},
    postJson: async (path, body) => { calls.push([path, body]); return snapshot('running') },
  })
  await waitFor(() => assert.equal(screen.getByTestId('run-resume-action').disabled, false))
  await userEvent.setup().click(screen.getByTestId('run-resume-action'))
  assert.deepEqual(calls, [['/api/experiments/exp-resume/cells/c1/resume', {}]])
})

test('a late session response cannot replace an adopted experiment', async () => {
  let resolveSession
  const api = async path => path === '/api/session' ? new Promise(resolve => { resolveSession = resolve }) : path === '/api/experiments/exp-adopt' ? {
    experiment_id: 'exp-adopt', status: 'running', cells: [{ cell_id: 'c1', method: 'dinsql', dataset: 'spider', status: 'running', job_id: 'job-adopt' }],
  } : {}
  const { rerender } = renderRun({ api })
  rerender(React.createElement(RunWorkspace, {
    selectedConnections: [{ method: 'DINSQL', database: 'Spider' }], configs: [baseConfig], databases: [],
    api, adoptExperimentId: 'exp-adopt', liveEvaluation: true, t,
  }))
  await waitFor(() => assert.match(screen.getByTestId('run-batch-monitor').textContent, /dinsql \/ spider/))
  await act(async () => resolveSession({ jobs: [{ job_id: 'unrelated-job', method: 'unrelated', dataset: 'other', status: 'completed' }] }))
  assert.doesNotMatch(screen.getByTestId('run-batch-monitor').textContent, /unrelated/)
})

test('an adopted experiment shows the live question view fed by its timeline', async () => {
  const requested = []
  const api = async path => {
    requested.push(path)
    if (path === '/api/session') return { jobs: [] }
    if (path === '/api/experiments/exp-live') {
      return { experiment_id: 'exp-live', status: 'running', cells: [{ cell_id: 'c1', method: 'dinsql', dataset: 'spider', status: 'running', job_id: 'job-live' }] }
    }
    if (path.startsWith('/api/experiments/exp-live/timeline')) {
      return {
        experiment_id: 'exp-live', status: 'running', cursor: 0,
        cells: [{
          cell_id: 'c1', method: 'dinsql', dataset: 'spider', status: 'running', total: 1, cursor: 0,
          questions: [{ index: 0, id: 'dev_0', db: 'pets_1', question: 'Which pets are oldest?', gold_sql: 'SELECT 1',
            run: { state: 'running', stages: [{ name: 'dinsql_generate1', kind: 'sql', text: '', seconds: null, state: 'running', error: null }], pred_sql: '', ex: null, result: null } }],
        }],
      }
    }
    return {}
  }
  const { rerender } = renderRun({ api })
  rerender(React.createElement(RunWorkspace, {
    selectedConnections: [{ method: 'DINSQL', database: 'Spider' }], configs: [baseConfig], databases: [],
    api, adoptExperimentId: 'exp-live', liveEvaluation: true, t,
  }))
  await waitFor(() => screen.getByText('Which pets are oldest?'))
  assert.ok(requested.some(path => path.startsWith('/api/experiments/exp-live/timeline')))
  // The live view leads; the per-job list and raw log are folded away beneath it.
  const folded = screen.getByTestId('run-batch-monitor').closest('details')
  assert.ok(folded && !folded.open)
  assert.equal(folded.querySelector('summary').textContent, 'run.batchDetails')
  const live = screen.getByRole('region', { name: 'run.liveTitle' })
  assert.ok(live.compareDocumentPosition(folded) & Node.DOCUMENT_POSITION_FOLLOWING)
  cleanup()
})

test('successful cell resumes remain visible when a later resume is refused', async () => {
  const snapshot = {
    experiment_id: 'exp-partial', status: 'partial',
    cells: [
      { cell_id: 'c0', method: 'done', dataset: 'spider', job_id: 'job-done', status: 'completed' },
      { cell_id: 'c1', method: 'first', dataset: 'spider', status: 'cancelled' },
      { cell_id: 'c2', method: 'second', dataset: 'spider', status: 'cancelled' },
    ],
  }
  const observed = []
  renderRun({
    adoptExperimentId: 'exp-partial', onExperimentSnapshot: value => observed.push(value),
    api: async path => path === '/api/experiments/exp-partial' ? snapshot : {},
    postJson: async path => {
      if (path.endsWith('/c2/resume')) throw new Error('All run slots are busy')
      return { ...snapshot, status: 'running', cells: snapshot.cells.map(cell => cell.cell_id === 'c1' ? { ...cell, status: 'running', job_id: 'job-resumed' } : cell) }
    },
  })
  await waitFor(() => assert.equal(screen.getByTestId('run-resume-action').disabled, false))
  await userEvent.setup().click(screen.getByTestId('run-resume-action'))
  assert.ok(observed.some(value => value.cells.some(cell => cell.job_id === 'job-resumed' && cell.status === 'running')))
  assert.match(screen.getByTestId('run-batch-monitor').textContent, /first \/ spiderrunning/)
  assert.match((await screen.findByRole('alert')).textContent, /All run slots are busy/)
})

test('a single Compose connection runs as a one-cell experiment', async () => {
  const calls = []
  const states = []
  renderRun({
    postJson: async (path, body) => {
      calls.push([path, body])
      return {
        experiment_id: 'exp-single',
        status: 'running',
        sampling: body.sampling,
        cells: [{ cell_id: 'c1', method: 'dinsql', dataset: 'spider', job_id: 'job-1', status: 'running', progress: {} }],
      }
    },
    onRunStateChange: state => states.push(state),
  })
  const user = userEvent.setup()

  assert.ok(screen.getByTestId('run-parameter-console'))
  assert.match(screen.getByTestId('run-compose-connections').textContent, /DINSQL→Spider/)
  assert.match(screen.getByTestId('run-config-command').textContent, /reproduce\/run\.py spider dinsql/)
  assert.equal(screen.queryByLabelText('Natural-language question'), null)
  await user.click(screen.getByRole('button', { name: 'Run config' }))

  assert.deepEqual(calls, [[
    '/api/experiments',
    {
      cells: [{ method: 'dinsql', dataset: 'spider' }],
      sampling: { limit: 20, mode: 'slice', seed: 42 },
    },
  ]])
  assert.ok(screen.getByTestId('run-batch-monitor'))
  assert.match(screen.getByTestId('run-batch-monitor').textContent, /dinsql \/ spider/)
  assert.equal(states.at(-1).phase, 'evaluating')
  assert.match(states.at(-1).context.command, /reproduce\/run\.py/)
})

test('launches one experiment for multiple Compose connections across benchmarks', async () => {
  const calls = []
  const secondConfig = {
    method: 'c3sql',
    dataset: 'bird',
    config_path: 'reproduce/configs/bird/c3sql.json',
    stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
  }
  const experiments = []
  renderRun({
    selectedConnections: [
      { method: 'DINSQL', database: 'Spider' },
      { method: 'C3SQL', database: 'BIRD' },
    ],
    configs: [baseConfig, secondConfig],
    onExperimentChange: id => experiments.push(id),
    postJson: async (path, body) => {
      calls.push([path, body])
      return {
        experiment_id: 'exp-1',
        status: 'running',
        sampling: body.sampling,
        cells: [
          { cell_id: 'c1', method: 'dinsql', dataset: 'spider', job_id: 'job-1', status: 'running', progress: {} },
          { cell_id: 'c2', method: 'c3sql', dataset: 'bird', job_id: null, status: 'queued', queue_position: 1, progress: {} },
        ],
      }
    },
  })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Run config' }))

  assert.equal(calls[0][0], '/api/experiments')
  assert.deepEqual(calls[0][1], {
    cells: [{ dataset: 'spider', method: 'dinsql' }, { dataset: 'bird', method: 'c3sql' }],
    sampling: { limit: 20, mode: 'slice', seed: 42 },
  })
  const monitor = screen.getByTestId('run-batch-monitor').textContent
  assert.match(monitor, /dinsql \/ spider/)
  assert.match(monitor, /c3sql \/ bird/)
  assert.match(monitor, /queued/)
  assert.deepEqual(experiments, ['exp-1'])
})

test('a variant runs as its own experiment cell next to the method', async () => {
  const calls = []
  const swap = { generate: 'C3SQLGenerator' }
  renderRun({
    variants: [{ method: 'dinsql', dataset: 'spider', stages: swap }],
    postJson: async (path, body) => {
      calls.push([path, body])
      return {
        experiment_id: 'exp-2',
        status: 'running',
        sampling: body.sampling,
        cells: [
          { cell_id: 'c1', method: 'dinsql', dataset: 'spider', label: 'dinsql', job_id: 'job-1', status: 'running', progress: {} },
          {
            cell_id: 'c2', method: 'dinsql', dataset: 'spider', label: 'dinsql · generate→C3SQLGenerator',
            stages: swap, job_id: 'job-2', status: 'running', progress: {},
          },
        ],
      }
    },
  })
  assert.match(screen.getByTestId('run-variants').textContent, /dinsql · generate→C3SQLGenerator \/ spider/)
  await userEvent.setup().click(screen.getByRole('button', { name: 'Run config' }))

  assert.equal(calls[0][0], '/api/experiments', 'one connection plus a variant is an experiment')
  assert.deepEqual(calls[0][1].cells, [
    { method: 'dinsql', dataset: 'spider' },
    { method: 'dinsql', dataset: 'spider', stages: swap },
  ])
  assert.match(screen.getByTestId('run-batch-monitor').textContent, /dinsql · generate→C3SQLGenerator \/ spider/)
})

test('jobsFromExperiment keeps live job detail and lists queued cells in order', () => {
  const experiment = {
    cells: [
      { cell_id: 'c1', method: 'dinsql', dataset: 'spider', job_id: 'job-1', status: 'running', progress: { completed: 3 } },
      { cell_id: 'c2', method: 'c3sql', dataset: 'bird', job_id: null, status: 'queued', queue_position: 2 },
      { cell_id: 'c3', method: 'finsql', dataset: 'spider', job_id: 'job-3', status: 'failed', error_code: 'evaluation_failed' },
    ],
  }
  const previous = [{ job_id: 'job-1', method: 'dinsql', dataset: 'spider', status: 'running', log: 'tail', resume_count: 1 }]
  const jobs = jobsFromExperiment(experiment, previous)
  assert.deepEqual(jobs.map(job => job.job_id), ['job-1', 'queued:c2', 'job-3'])
  assert.equal(jobs[0].log, 'tail', 'known job detail survives a refresh')
  assert.equal(jobs[0].resume_count, 1)
  assert.equal(jobs[1].status, 'queued')
  assert.equal(jobs[1].queue_position, 2)
  assert.equal(jobs[2].status, 'failed')
  assert.equal(jobs[2].error_code, 'evaluation_failed')
  assert.deepEqual(jobsFromExperiment(null, previous), previous)
})

test('summarizeRunProgress prefers finished samples and server progress', () => {
  const fromLog = summarizeRunProgress([
    '开始处理样本 dev_0',
    '样本 dev_0 @ c3sql_reduce1',
    '样本 dev_0 @ c3sql_generate1',
    '样本 dev_0 处理完成 (1.0s)',
    '开始处理样本 dev_1',
    '样本 dev_1 @ c3sql_generate1',
  ].join('\n'), { sample_limit: 20, status: 'running' })
  assert.equal(fromLog.completed, 1)
  assert.equal(fromLog.started, 2)
  assert.equal(fromLog.percent, 5)
  assert.equal(fromLog.currentStage, 'c3sql_generate1')

  const fromServer = summarizeRunProgress('truncated tail only', {
    sample_limit: 20,
    status: 'completed',
    progress: {
      current_stage: '评估完成',
      started: 20,
      completed: 20,
      total: 20,
      percent: 100,
    },
  })
  assert.equal(fromServer.percent, 100)
  assert.equal(fromServer.completed, 20)
  assert.equal(fromServer.currentStage, '评估完成')
})

test('resumes a cancelled board job through the reproduce resume endpoint', async () => {
  const calls = []
  let resumed = false
  const cancelledJob = {
    job_id: 'job-resume',
    method: 'dinsql',
    dataset: 'spider',
    status: 'cancelled',
    checkpoint_present: true,
    resumable: true,
    resume_count: 0,
    max_resume_attempts: 2,
    progress: { current_stage: 'c3sql_generate1', started: 8, completed: 8, total: 20, percent: 40 },
  }
  renderRun({
    api: async path => {
      if (path === '/api/session') {
        return { jobs: [cancelledJob] }
      }
      if (path === '/api/evaluations/job-resume') {
        if (!resumed) {
          return { ...cancelledJob, log: 'checkpoint ready' }
        }
        return {
          ...cancelledJob,
          status: 'running',
          resumable: false,
          resume_count: 1,
          log: '[demo] manual resume 1/2',
          progress: { current_stage: 'c3sql_generate1', started: 8, completed: 8, total: 20, percent: 40 },
        }
      }
      return {}
    },
    postJson: async (path, body) => {
      calls.push([path, body])
      resumed = true
      return {
        job_id: 'job-resume',
        method: 'dinsql',
        dataset: 'spider',
        status: 'running',
        checkpoint_present: true,
        resumable: false,
        resume_count: 1,
        max_resume_attempts: 2,
        resume_mode: 'manual',
      }
    },
  })

  const resume = await screen.findByTestId('run-resume-action')
  await waitFor(() => assert.equal(resume.disabled, false))
  assert.match(screen.getByTestId('run-config-command').textContent, /--resume/)
  await userEvent.setup().click(resume)

  assert.deepEqual(calls, [['/api/evaluations/job-resume/resume', {}]])
  await waitFor(() => assert.match(screen.getByTestId('run-batch-monitor').textContent, /running/i))
})

test('stops active board jobs through the cancel endpoint', async () => {
  const calls = []
  let cancelled = false
  renderRun({
    api: async path => {
      if (path === '/api/session') {
        return {
          jobs: [{
            job_id: 'job-stop',
            method: 'dinsql',
            dataset: 'spider',
            status: 'running',
            progress: { current_stage: 'c3sql_parse1', started: 4, completed: 2, total: 20, percent: 10 },
          }],
        }
      }
      if (path === '/api/evaluations/job-stop') {
        return {
          job_id: 'job-stop',
          method: 'dinsql',
          dataset: 'spider',
          status: cancelled ? 'cancelled' : 'running',
          log: cancelled ? 'cancelled by user' : 'running…',
          progress: {
            current_stage: cancelled ? '评估完成' : 'c3sql_parse1',
            started: cancelled ? 4 : 4,
            completed: cancelled ? 4 : 2,
            total: 20,
            percent: cancelled ? 20 : 10,
          },
        }
      }
      return {}
    },
    postJson: async (path, body) => {
      calls.push([path, body])
      cancelled = true
      return {
        job_id: 'job-stop',
        method: 'dinsql',
        dataset: 'spider',
        status: 'cancelled',
      }
    },
  })

  const stop = await screen.findByTestId('run-stop-action')
  await waitFor(() => assert.equal(stop.disabled, false))
  await userEvent.setup().click(stop)

  assert.deepEqual(calls, [['/api/evaluations/job-stop/cancel', {}]])
  await waitFor(() => assert.match(screen.getByTestId('run-batch-monitor').textContent, /cancelled/i))
})

test('visualizes completed job score-bundle artifacts in the run monitor', async () => {
  renderRun({
    api: async path => {
      if (path === '/api/session') {
        return {
          jobs: [{
            job_id: 'job-done',
            method: 'c3sql',
            dataset: 'spider',
            status: 'completed',
            progress: { current_stage: '评估完成', started: 20, completed: 20, total: 20, percent: 100 },
            result: {
              run_id: 'spider-c3sql-job-done',
              method: 'c3sql',
              dataset: 'spider',
              split: 'dev',
              sample_count: 20,
              metrics: { ex: 0.9, em: 0.85, sf1: 0.88, ves: 0.7, rves: 0.65 },
              component_f1: { select: 0.92, where: 0.8 },
              error_roots: [{ name: 'schema_link', count: 3, rate: 0.15 }],
              token: { total_tokens: 12000, avg_per_sample: 600 },
              stages: [{ name: 'generate', task_type: 'GenerateTask' }],
            },
            artifact: {
              run_id: 'spider-c3sql-job-done',
              artifact_ref: 'session:spider-c3sql-job-done/scores.json',
              latency: { mean_s: 1.25 },
            },
          }],
        }
      }
      if (path === '/api/evaluations/job-done') {
        return {
          job_id: 'job-done',
          method: 'c3sql',
          dataset: 'spider',
          status: 'completed',
          log: '评估完成',
          progress: { current_stage: '评估完成', started: 20, completed: 20, total: 20, percent: 100 },
          result: {
            run_id: 'spider-c3sql-job-done',
            method: 'c3sql',
            dataset: 'spider',
            split: 'dev',
            sample_count: 20,
            metrics: { ex: 0.9, em: 0.85, sf1: 0.88, ves: 0.7, rves: 0.65 },
            component_f1: { select: 0.92, where: 0.8 },
            error_roots: [{ name: 'schema_link', count: 3, rate: 0.15 }],
            token: { total_tokens: 12000 },
            stages: [{ name: 'generate', task_type: 'GenerateTask' }],
          },
          artifact: {
            run_id: 'spider-c3sql-job-done',
            artifact_ref: 'session:spider-c3sql-job-done/scores.json',
            latency: { mean_s: 1.25 },
          },
        }
      }
      return {}
    },
  })

  const panel = await screen.findByTestId('run-artifact-panel')
  assert.match(panel.textContent, /90\.0%/)
  assert.match(panel.textContent, /dashboard.structure/)
  assert.match(panel.textContent, /schema_link/)
  assert.match(panel.textContent, /session:spider-c3sql-job-done\/scores\.json/)
})

test('resolveJobArtifact maps summary metrics for visualization', () => {
  const resolved = resolveJobArtifact({
    job_id: 'x',
    result: {
      metrics: { ex: 0.5 },
      component_f1: { select: 0.7 },
      error_roots: [],
      token: { total_tokens: 10 },
      stages: [],
    },
  })
  assert.equal(resolved.metricCards.find(item => item.key === 'ex').value, 0.5)
  assert.equal(resolved.componentF1[0].name, 'select')
})

test('mergeJobDetail keeps terminal status and preserves newer artifacts', () => {
  const merged = mergeJobDetail(
    {
      job_id: 'job-1',
      status: 'completed',
      progress: { percent: 100 },
      result: { metrics: { ex: 0.9 } },
      artifact: { run_id: 'done' },
      resumable: false,
    },
    {
      job_id: 'job-1',
      status: 'running',
      progress: { percent: 40 },
      log: 'stale log should not be merged into job state',
      log_path: '/Users/example/secret/run.log',
      resumable: false,
    },
  )
  assert.equal(merged.status, 'completed')
  assert.equal(merged.progress.percent, 100)
  assert.equal(merged.result.metrics.ex, 0.9)
  assert.equal(merged.artifact.run_id, 'done')
  assert.equal(merged.log, undefined)
  assert.equal(merged.log_path, undefined)
})

test('restores supervised jobs from the backend session after a page reload', async () => {
  const calls = []
  renderRun({
    api: async path => {
      calls.push(path)
      if (path === '/api/session') {
        return {
          jobs: [{
            job_id: 'resumed-job',
            method: 'dinsql',
            dataset: 'spider',
            status: 'resuming',
            resume_count: 1,
            max_resume_attempts: 2,
          }],
        }
      }
      return { log: '[demo] autonomous resume 1/2' }
    },
  })

  assert.match((await screen.findByTestId('run-batch-monitor')).textContent, /dinsql \/ spider/)
  assert.ok(calls.includes('/api/session'))
  await waitFor(() => assert.ok(calls.includes('/api/evaluations/resumed-job')))
})

test('disables config runs when live_evaluation is unavailable', () => {
  renderRun({ liveEvaluation: false })
  assert.equal(screen.getByRole('button', { name: 'Run config' }).disabled, true)
  assert.match(document.querySelector('.run-workspace').textContent, /local demo only/)
})

test('runs config without requiring a connected model', () => {
  renderRun()
  assert.equal(screen.queryByRole('button', { name: /Configure SQL API|Configure LLM/ }), null)
  assert.doesNotMatch(document.querySelector('.run-workspace').textContent, /\bModel\b/)
  assert.equal(screen.getByRole('button', { name: 'Run config' }).disabled, false)
})

test('disables config runs when the focused configuration is missing', () => {
  renderRun({ configs: [] })
  assert.equal(screen.getByRole('button', { name: 'Run config' }).disabled, true)
  assert.match(document.querySelector('.run-workspace').textContent, /configuration is unavailable/)
})

test('sanitizes a rejected config-run error without exposing secrets', async () => {
  renderRun({
    postJson: async () => {
      throw new Error('Provider rejected api_key=sk-live-secret with Authorization: Bearer sk-live-secret')
    },
  })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Run config' }))
  assert.ok(await screen.findByRole('alert'))
  assert.equal(document.body.textContent.includes('sk-live-secret'), false)
})

test('sanitizes supported credential forms while preserving benign technical text', () => {
  const sanitized = sanitizeRunError(new Error([
    'API key: api-secret',
    'OPENAI_API_KEY=openai-secret',
    'Bearer bearer-secret',
    'password=pw-secret',
    'client_secret=oauth-secret',
    'sk_test_51LeakTokenValue',
    'SQLite syntax error near SELECT at line 4',
  ].join('; ')))
  assert.equal(sanitized.includes('api-secret'), false)
  assert.equal(sanitized.includes('bearer-secret'), false)
  assert.equal(sanitized.includes('pw-secret'), false)
  assert.equal(sanitized.includes('oauth-secret'), false)
  assert.equal(sanitized.includes('sk_test_51LeakTokenValue'), false)
  assert.match(sanitized, /SQLite syntax error near SELECT at line 4/)
})

test('the run page states what the plan will execute before it is sent', () => {
  renderRun({
    selectedConnections: [
      { method: 'DINSQL', database: 'Spider' },
      { method: 'C3SQL', database: 'BIRD' },
    ],
    configs: [baseConfig, { method: 'c3sql', dataset: 'bird', config_path: 'x', stages: [] }],
    variants: [{ method: 'dinsql', dataset: 'spider', stages: { generate: 'C3SQLGenerator' } }],
  })
  assert.equal(
    screen.getByTestId('run-plan-summary').textContent,
    '3 cells · 1 variants · 20 questions each · 60 questions in total',
  )
})

test('a refused experiment names each cell and the reason, so the user can go back and fix it', async () => {
  const error = Object.assign(new Error('Some cells cannot run yet: C3SQL/BIRD (credential_missing).'), {
    cells: [{ method: 'C3SQL', dataset: 'BIRD', error_code: 'credential_missing' }],
  })
  renderRun({
    selectedConnections: [{ method: 'C3SQL', database: 'BIRD' }],
    configs: [{ method: 'c3sql', dataset: 'bird', config_path: 'x', stages: [] }],
    postJson: async () => { throw error },
  })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Run config' }))
  const rejections = await screen.findByTestId('run-rejections')
  assert.match(rejections.textContent, /C3SQL \/ BIRD/)
  assert.match(rejections.textContent, /credential_missing/)
})
