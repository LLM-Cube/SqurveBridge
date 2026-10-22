import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { detectLocale, setDocumentLocale, translate } from '../i18n/index.js'
import BoardWorkspace from './BoardWorkspace.jsx'
import ConfigurationStudio from './ConfigurationStudio.jsx'
import ConnectionComposer from './ConnectionComposer.jsx'
import EvidenceHub from './EvidenceHub.jsx'
import ExperimentGrid from './ExperimentGrid.jsx'
import QueryWorkspace from './QueryWorkspace.jsx'
import {
  DATABASES,
  METHODS,
  ensureConnection,
  resolveFocusedConfig,
  selectGridLine,
  selectedDatabasesFromConnections,
  selectedMethodsFromConnections,
  toggleConnection,
  withConnectionKeys,
} from './model.js'
import { EXPERIMENT_SECTIONS, PROCESS_STEPS, resolveProcessStep, resolveSection } from './processSteps.js'
import { INITIAL_RUN_STATE } from './RunWorkspace.jsx'
import { displayNamesFor } from './resultsModel.js'
import { normalizeProposal, pageContextFor } from '../agentActions.js'
import { FlowStatus } from './flowUi.jsx'
import { addVariant, removeVariant } from './variantModel.js'
import { agentEnabledIn } from '../runtimeMode.js'
import './full-flow.css'
import './agent-shell.css'
import './ui-enhancements.css'

const AgentHarness = lazy(() => import('../AgentHarness.jsx'))
const DRAFT_STORAGE_KEY = 'squrve-demo-draft'

// The unsent plan (combinations and variants) survives a reload. A missing or unreadable draft means none.
function loadDraft() {
  try {
    const draft = JSON.parse(window.localStorage.getItem(DRAFT_STORAGE_KEY) || 'null')
    if (!draft || !Array.isArray(draft.connections) || !draft.connections.length) return null
    return { connections: draft.connections, variants: Array.isArray(draft.variants) ? draft.variants : [] }
  } catch {
    return null
  }
}

function saveDraft(connections, variants) {
  try {
    window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({
      connections: connections.map(({ method, database }) => ({ method, database })),
      variants,
    }))
  } catch {
    // Storage can be blocked; the page works without a draft.
  }
}
const SPLIT_STORAGE_KEY = 'squrve-demo-shell-layout'

// All five stage pages stay mounted (hidden pages keep their state), so any
// FullFlowDemo state change used to re-render every workspace. Memoizing them
// keeps shell interactions (pane collapse, run polling, locale-safe props)
// from touching pages whose props did not change.
const MemoConfigurationStudio = memo(ConfigurationStudio)
const MemoConnectionComposer = memo(ConnectionComposer)
const MemoExperimentGrid = memo(ExperimentGrid)
const MemoQueryWorkspace = memo(QueryWorkspace)
const MemoBoardWorkspace = memo(BoardWorkspace)
const MemoEvidenceHub = memo(EvidenceHub)

function loadShellLayout() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SPLIT_STORAGE_KEY) || '{}')
    return {
      dashboardWidth: Math.min(75, Math.max(35, Number(saved.dashboardWidth) || 62)),
      dashboardCollapsed: Boolean(saved.dashboardCollapsed),
      agentCollapsed: Boolean(saved.agentCollapsed),
    }
  } catch {
    return { dashboardWidth: 62, dashboardCollapsed: false, agentCollapsed: false }
  }
}

export default function FullFlowDemo({
  capabilities,
  databases = [],
  sqlAuth,
  postJson,
  api,
  onConfigureSql,
  credentialMode = 'session',
}) {
  const configs = capabilities?.reproduce_configs || []
  // A showcase deployment turns the Agent off on the server; the page then has no Agent pane.
  const agentEnabled = agentEnabledIn(capabilities)
  const [locale, setLocale] = useState(() => detectLocale(window.localStorage.getItem('squrve-demo-locale')))
  const [activeStep, setActiveStep] = useState(() => resolveProcessStep(window.location.hash))
  // Which part of the experiment page to bring into view; the counter re-scrolls on repeat clicks.
  const [sectionRequest, setSectionRequest] = useState(() => ({
    section: resolveSection(window.location.hash),
    id: 0,
  }))
  const [selectedConnections, setSelectedConnections] = useState(() => (
    loadDraft()?.connections || [{ method: METHODS[0], database: DATABASES[0] }]
  ))
  const connectionsRef = useRef(selectedConnections)
  const [focusedMethod, setFocusedMethod] = useState(METHODS[0])
  const [focusedDatabase, setFocusedDatabase] = useState(DATABASES[0])
  const [sampleLimit, setSampleLimit] = useState(20)
  const [sampleMode, setSampleMode] = useState('slice')
  const [sampleSeed, setSampleSeed] = useState(42)
  const [runState, setRunState] = useState(INITIAL_RUN_STATE)
  const [experimentId, setExperimentId] = useState('')
  const [queryPrefill, setQueryPrefill] = useState(null)
  const [adoptedExperimentId, setAdoptedExperimentId] = useState('')
  const [chatKey, setChatKey] = useState(0)
  const [harnessTask, setHarnessTask] = useState(null)
  const [shellLayout, setShellLayout] = useState(loadShellLayout)
  const [mobilePane, setMobilePane] = useState('dashboard')
  const [adoptedSql, setAdoptedSql] = useState(null)
  // Actor-swap variants built in Compose; each runs as its own experiment cell.
  const [variants, setVariants] = useState(() => loadDraft()?.variants || [])
  const splitRef = useRef(null)
  const shellRef = useRef(null)
  const dividerGhostRef = useRef(null)
  const workspaceBodyRef = useRef(null)
  const t = useCallback((key, params) => translate(locale, key, params), [locale])
  const focusedConfig = useMemo(
    () => resolveFocusedConfig(configs, focusedMethod, focusedDatabase),
    [configs, focusedMethod, focusedDatabase],
  )
  const selectedMethods = useMemo(
    () => selectedMethodsFromConnections(selectedConnections),
    [selectedConnections],
  )
  const selectedDatabases = useMemo(
    () => selectedDatabasesFromConnections(selectedConnections),
    [selectedConnections],
  )
  const connections = useMemo(
    () => withConnectionKeys(selectedConnections),
    [selectedConnections],
  )

  useEffect(() => {
    window.localStorage.setItem('squrve-demo-locale', locale)
    setDocumentLocale(locale)
  }, [locale])

  useEffect(() => {
    window.localStorage.setItem(SPLIT_STORAGE_KEY, JSON.stringify(shellLayout))
  }, [shellLayout])

  useEffect(() => {
    saveDraft(selectedConnections, variants)
  }, [selectedConnections, variants])

  useEffect(() => {
    const syncFromHash = () => {
      setActiveStep(resolveProcessStep(window.location.hash))
      setSectionRequest(current => ({ section: resolveSection(window.location.hash), id: current.id + 1 }))
    }
    window.addEventListener('hashchange', syncFromHash)
    return () => window.removeEventListener('hashchange', syncFromHash)
  }, [])

  useEffect(() => {
    // Stage pages share one scroll container; reset it so the new page
    // header is never clipped by a stale scroll offset.
    if (workspaceBodyRef.current) workspaceBodyRef.current.scrollTop = 0
  }, [activeStep])

  useEffect(() => {
    // Runs after the page reset above, so a section link lands on its section.
    if (!sectionRequest.section || activeStep !== 'experiment') return
    workspaceBodyRef.current
      ?.querySelector(`[data-section="${sectionRequest.section}"]`)
      ?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
  }, [sectionRequest, activeStep])

  // Accepts a step or an experiment section (compose, board, evidence).
  const navigateToStep = useCallback(target => {
    const next = resolveProcessStep(target)
    const section = resolveSection(target)
    setActiveStep(next)
    setSectionRequest(current => ({ section, id: current.id + 1 }))
    const hash = `#${section || next}`
    if (window.location.hash !== hash) {
      window.history.replaceState(null, '', hash)
    }
  }, [])

  const applyFocus = useCallback((method, database) => {
    setFocusedMethod(method)
    setFocusedDatabase(database)
  }, [])

  const syncFocus = useCallback((next, preferredMethod = focusedMethod, preferredDatabase = focusedDatabase) => {
    const preferred = next.find(item =>
      item.method === preferredMethod && item.database === preferredDatabase,
    )
    const byMethod = next.find(item => item.method === preferredMethod)
    const byDatabase = next.find(item => item.database === preferredDatabase)
    const target = preferred || byMethod || byDatabase || next[0]
    applyFocus(target.method, target.database)
  }, [focusedMethod, focusedDatabase, applyFocus])

  // Wiring clicks can arrive faster than React commits the previous one, so the
  // ref — not the rendered state — is the authoritative list. Deriving the next
  // list from a stale render dropped the earlier pair, which made a method look
  // like it could only ever hold a single database.
  const commitConnections = useCallback(next => {
    connectionsRef.current = next
    setSelectedConnections(next)
    return next
  }, [])

  const onToggleConnection = useCallback((method, database) => {
    const next = commitConnections(toggleConnection(connectionsRef.current, method, database))
    if (next.some(item => item.method === method && item.database === database)) {
      applyFocus(method, database)
      return
    }
    syncFocus(next)
  }, [commitConnections, applyFocus, syncFocus])

  const onSelectGridLine = useCallback(line => {
    syncFocus(commitConnections(selectGridLine(connectionsRef.current, configs, line)))
  }, [commitConnections, syncFocus, configs])

  const onFocusConnection = useCallback((method, database) => {
    commitConnections(ensureConnection(connectionsRef.current, method, database))
    applyFocus(method, database)
  }, [commitConnections, applyFocus])

  // Replay one question from the per-question diff in Query, with the method's
  // workflow focused so the query runs the same actors as the recorded run.
  const onAddVariant = useCallback((config, stages) => {
    setVariants(current => addVariant(current, config, stages))
  }, [])
  const onRemoveVariant = useCallback(identity => {
    setVariants(current => removeVariant(current, identity))
  }, [])

  const replayInQuery = useCallback(({ method, stages, dataset, db_id: dbId, question }) => {
    const names = displayNamesFor(method, dataset)
    if (names) onFocusConnection(names.method, names.database)
    setQueryPrefill({ id: Date.now(), dbId, question, method, stages })
    navigateToStep('query')
    setMobilePane('dashboard')
  }, [onFocusConnection, navigateToStep])

  const agentPageContext = useMemo(() => pageContextFor({
    activeStep, experimentId, connections, configs, focusedMethod, focusedDatabase,
  }), [activeStep, experimentId, connections, configs, focusedMethod, focusedDatabase])

  // Runs an Agent proposal the user has just confirmed; anything the page does
  // not recognise, or that the deployment does not allow, is refused here.
  const runAgentAction = useCallback(async proposal => {
    const safe = normalizeProposal(proposal, { hosted: credentialMode !== 'local' })
    if (!safe) throw new Error(t('agent.proposalRejected'))
    const { action, args } = safe
    if (action === 'start_experiment') {
      const created = await postJson('/api/experiments', args)
      setExperimentId(created.experiment_id)
      setAdoptedExperimentId(created.experiment_id)
      navigateToStep('board')
      return
    }
    if (action === 'open_experiment') {
      setExperimentId(args.experiment_id)
      navigateToStep('evidence')
      return
    }
    if (action === 'open_cell') {
      const names = displayNamesFor(args.method, args.dataset)
      if (names) onFocusConnection(names.method, names.database)
      navigateToStep('evidence')
      return
    }
    replayInQuery(args)
  }, [credentialMode, postJson, navigateToStep, onFocusConnection, replayInQuery, t])

  const adoptSqlFromAgent = useCallback(sql => {
    setAdoptedSql({ id: Date.now(), sql })
    navigateToStep('query')
    setMobilePane('dashboard')
    setShellLayout(current => (current.dashboardCollapsed
      ? { ...current, dashboardCollapsed: false }
      : current))
  }, [navigateToStep])

  const askPiFromQuery = useCallback(prompt => {
    setHarnessTask({ id: `query-analyze-${Date.now()}`, command: prompt })
    setMobilePane('agent')
    setShellLayout(current => (current.agentCollapsed
      ? { ...current, agentCollapsed: false }
      : current))
  }, [])

  const onAdoptedSqlHandled = useCallback(() => setAdoptedSql(null), [])
  const onQueuedCommandSent = useCallback(() => setHarnessTask(null), [])
  const onRequestNewChat = useCallback(() => setChatKey(key => key + 1), [])

  // The run workspace republishes its state on every 2.5s poll while a job is
  // active; only the phase is displayed here, so drop identical-phase updates
  // instead of re-rendering the whole shell each poll.
  const onRunStateChange = useCallback(next => {
    setRunState(current => (current?.phase === next?.phase ? current : next))
  }, [])

  // Personal-workspace snapshot for the Studio status strip. Only local
  // session state; credential info is limited to a configured flag plus
  // provider/model names — never the key itself.
  const studioWorkspaceStatus = useMemo(() => ({
    connectionCount: connections.length,
    focusedMethod,
    focusedDatabase,
    credentialConfigured: Boolean(sqlAuth?.configured),
    credentialLabel: sqlAuth?.configured
      ? [sqlAuth.provider, sqlAuth.model].filter(Boolean).join(' / ')
      : '',
    runPhase: runState?.phase || 'ready',
  }), [connections, focusedMethod, focusedDatabase, sqlAuth, runState?.phase])

  const pages = {
    configure: (
      <MemoConfigurationStudio
        hostedReadOnly={credentialMode !== 'local'}
        workspaceStatus={studioWorkspaceStatus}
        onNavigateStep={navigateToStep}
        t={t}
      />
    ),
    experiment: (
      <div className="experiment-page" data-testid="experiment-page">
        <nav className="experiment-section-nav" aria-label={t('experiment.sections')}>
          {EXPERIMENT_SECTIONS.map((section, index) => (
            <button
              key={section}
              type="button"
              aria-current={sectionRequest.section === section ? 'step' : undefined}
              onClick={() => navigateToStep(section)}
            >
              <span aria-hidden="true">{index + 1}</span>
              {t(`process.${section}`)}
            </button>
          ))}
        </nav>
      <div className="experiment-section" data-section="compose">
      <MemoExperimentGrid
        api={api}
        configs={configs}
        connections={connections}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        onToggleConnection={onToggleConnection}
        onSelectLine={onSelectGridLine}
        onFocusConnection={onFocusConnection}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        t={t}
      />
      <MemoConnectionComposer
        selectedConnections={connections}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        onToggleConnection={onToggleConnection}
        onFocusConnection={onFocusConnection}
        configs={configs}
        focusedConfig={focusedConfig}
        stageActors={capabilities?.stage_actors}
        variants={variants}
        onAddVariant={onAddVariant}
        onRemoveVariant={onRemoveVariant}
        t={t}
      />
      </div>
      <div className="experiment-section" data-section="board">
      <MemoBoardWorkspace
        selectedConnections={connections}
        configs={configs}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        onFocusConnection={onFocusConnection}
        databases={databases}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        onSampleLimitChange={setSampleLimit}
        onSampleModeChange={setSampleMode}
        onSampleSeedChange={setSampleSeed}
        postJson={postJson}
        api={api}
        onRunStateChange={onRunStateChange}
        onExperimentChange={setExperimentId}
        adoptExperimentId={adoptedExperimentId}
        liveEvaluation={Boolean(capabilities?.deployment?.features?.live_evaluation)}
        variants={variants}
        t={t}
      />
      </div>
      <div className="experiment-section" data-section="evidence">
      <MemoEvidenceHub
        pageId="evidence"
        capabilities={capabilities}
        api={api}
        postJson={postJson}
        liveEvaluation={Boolean(capabilities?.deployment?.features?.live_evaluation)}
        focusedConfig={focusedConfig}
        selectedMethods={selectedMethods}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        configs={configs}
        connections={connections}
        experimentId={experimentId}
        onReplay={replayInQuery}
        t={t}
      />
      </div>
      </div>
    ),
    query: (
      <MemoQueryWorkspace
        databases={databases}
        capabilities={capabilities}
        focusedConfig={focusedConfig}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        sqlAuth={sqlAuth}
        credentialMode={credentialMode}
        onConfigureSql={onConfigureSql}
        postJson={postJson}
        api={api}
        adoptedSql={adoptedSql}
        onAdoptedSqlHandled={onAdoptedSqlHandled}
        onAskPi={agentEnabled ? askPiFromQuery : undefined}
        prefill={queryPrefill}
        t={t}
      />
    ),
  }

  const nextLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN'
  const languageLabel = locale === 'zh-CN'
    ? t('language.switchToEnglish')
    : t('language.switchToChinese')
  const projectLabel = focusedConfig
    ? `${focusedConfig.method} · ${focusedConfig.dataset}`
    : t('shell.workspace')
  const dashboardCollapsed = agentEnabled && shellLayout.dashboardCollapsed && !shellLayout.agentCollapsed
  const agentCollapsed = agentEnabled && shellLayout.agentCollapsed && !shellLayout.dashboardCollapsed
  const shellStyle = {
    '--dashboard-width': `${shellLayout.dashboardWidth}%`,
  }

  const setCollapsed = pane => {
    setShellLayout(current => pane === 'dashboard'
      ? { ...current, dashboardCollapsed: !current.dashboardCollapsed, agentCollapsed: false }
      : { ...current, agentCollapsed: !current.agentCollapsed, dashboardCollapsed: false })
  }

  const startResize = event => {
    if (dashboardCollapsed || agentCollapsed || window.matchMedia('(max-width: 899px)').matches) return
    event.preventDefault()
    const root = splitRef.current
    const shell = shellRef.current
    if (!root || !shell) return
    const ghost = dividerGhostRef.current
    if (!ghost) return
    const bounds = root.getBoundingClientRect()
    const divider = event.currentTarget
    // Live-resizing the grid forces a full layout of both panes (SVG
    // connection graph, editors, large tables) on every frame, which cannot
    // keep up with the pointer no matter how the style write is batched.
    // Instead the drag only moves a transform-driven ghost indicator —
    // compositor work, zero layout — and the width is applied to the grid
    // exactly once on release. Writing the transform synchronously per
    // pointermove (no rAF queue) also avoids a frame of added latency.
    // Percentage grid tracks resolve against the grid content box, so the
    // pointer→width mapping uses the content box too; the released divider
    // then lands exactly where the ghost was. The mapping is linear: the
    // only adjustments are the 35–75 clamp mirrored by aria-valuemin/max
    // and rounding for the aria-valuenow attribute (never for layout).
    const rootStyles = window.getComputedStyle(root)
    const paddingLeft = parseFloat(rootStyles.paddingLeft) || 0
    const paddingRight = parseFloat(rootStyles.paddingRight) || 0
    const contentWidth = Math.max(1, bounds.width - paddingLeft - paddingRight)
    let latest = shellLayout.dashboardWidth
    const placeGhost = width => {
      ghost.style.transform = `translateX(${paddingLeft + (contentWidth * width) / 100}px)`
    }
    const onMove = moveEvent => {
      const next = ((moveEvent.clientX - bounds.left - paddingLeft) / contentWidth) * 100
      latest = Math.min(75, Math.max(35, next))
      placeGhost(latest)
      divider.setAttribute('aria-valuenow', String(Math.round(latest)))
    }
    const stop = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      document.body.classList.remove('agent-shell-resizing')
      // Commit once: the shell variable updates the grid in a single
      // relayout, then React state persists the layout.
      shell.style.setProperty('--dashboard-width', `${latest}%`)
      divider.setAttribute('aria-valuenow', String(Math.round(latest)))
      setShellLayout(current => ({ ...current, dashboardWidth: latest }))
    }
    placeGhost(latest)
    document.body.classList.add('agent-shell-resizing')
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', stop, { once: true })
    window.addEventListener('pointercancel', stop, { once: true })
  }

  return (
    <main
      ref={shellRef}
      className={[
        'flow-demo agent-shell',
        dashboardCollapsed ? 'dashboard-collapsed' : '',
        agentCollapsed ? 'agent-collapsed' : '',
        agentEnabled ? `mobile-pane-${mobilePane}` : 'agent-off',
      ].filter(Boolean).join(' ')}
      data-testid="agent-shell"
      style={shellStyle}
    >
      <div className="agent-shell-notice">
        {t(credentialMode === 'local' ? 'local.notice' : 'hosted.notice')}
      </div>

      {agentEnabled ? (
      <div className="agent-mobile-switcher" role="tablist" aria-label={t('shell.workspace')}>
        <button type="button" role="tab" aria-selected={mobilePane === 'dashboard'} onClick={() => setMobilePane('dashboard')}>
          {t('shell.liveWorkspace')}
        </button>
        <button type="button" role="tab" aria-selected={mobilePane === 'agent'} onClick={() => setMobilePane('agent')}>
          {t('shell.piBackend')}
        </button>
      </div>
      ) : null}

      <div className="agent-shell-panes" ref={splitRef}>
        <section className="agent-dashboard-pane" aria-label={t('shell.liveWorkspace')}>
          <div className="agent-dashboard-surface">
            <header className="agent-dashboard-header">
              <div className="agent-dashboard-identity">
                <span className="agent-dashboard-kicker">SqurveBridge</span>
                <h1>{t(`process.${activeStep}`)}</h1>
                <span className="agent-chat-project">
                  <b>{projectLabel}</b>
                  <span>{t('header.configCount', { count: configs.length })}</span>
                </span>
              </div>
              <div className="agent-dashboard-actions">
                <button type="button" onClick={onConfigureSql}>
                  {t(credentialMode === 'local' ? 'header.configureLocalApi' : 'header.configureApi')}
                </button>
                <button type="button" aria-label={languageLabel} onClick={() => setLocale(nextLocale)}>
                  {languageLabel}
                </button>
                {agentEnabled ? (
                  <button
                    type="button"
                    className="agent-pane-toggle"
                    aria-label={t('shell.collapseDashboard')}
                    onClick={() => setCollapsed('dashboard')}
                  >
                    ‹
                  </button>
                ) : null}
              </div>
            </header>
            <nav className="agent-stage-tabs" aria-label={t('shell.stageTabs')}>
              {PROCESS_STEPS.map(step => (
                <button
                  key={step}
                  type="button"
                  className={activeStep === step ? 'active' : ''}
                  aria-current={activeStep === step ? 'page' : undefined}
                  onClick={() => navigateToStep(step)}
                >
                  {t(`process.${step}`)}
                </button>
              ))}
            </nav>
          <div className="agent-live-workspace-head">
            <h2>
              <span className="agent-live-dot" aria-hidden="true" />
              {t('shell.liveWorkspace')}
            </h2>
            <div className="agent-live-workspace-actions">
              <button type="button" aria-label={t('process.previous')} onClick={() => {
                const index = PROCESS_STEPS.indexOf(activeStep)
                if (index > 0) navigateToStep(PROCESS_STEPS[index - 1])
              }}>
                ‹
              </button>
              <button type="button" aria-label={t('process.next')} onClick={() => {
                const index = PROCESS_STEPS.indexOf(activeStep)
                if (index < PROCESS_STEPS.length - 1) navigateToStep(PROCESS_STEPS[index + 1])
              }}>
                ›
              </button>
            </div>
          </div>
          <div
            ref={workspaceBodyRef}
            className="agent-live-workspace-body"
            data-testid="flow-stage"
            data-active-step={activeStep}
            data-active-section={activeStep === 'experiment' ? sectionRequest.section || 'compose' : undefined}
          >
            {PROCESS_STEPS.map(step => (
              <div
                key={step}
                className="flow-stage-page"
                data-testid={`flow-stage-${step}`}
                hidden={activeStep !== step}
              >
                {pages[step]}
              </div>
            ))}
          </div>
          <footer className="agent-live-workspace-foot">
            <span>{t('shell.workspaceFoot')}</span>
            <span>
              {runState?.phase && runState.phase !== 'ready'
                ? <b>{runState.phase}</b>
                : t('status.ready')}
            </span>
          </footer>
          </div>
        </section>

        {agentEnabled ? (
        <>
        <button
          className="agent-dashboard-restore"
          type="button"
          aria-label={t('shell.expandDashboard')}
          onClick={() => setCollapsed('dashboard')}
        >
          <span>SB</span>
          <b>{t('shell.liveWorkspace')}</b>
        </button>

        <div
          className="agent-shell-divider"
          role="separator"
          aria-orientation="vertical"
          aria-valuemin="35"
          aria-valuemax="75"
          aria-valuenow={Math.round(shellLayout.dashboardWidth)}
          onPointerDown={startResize}
        >
          <span />
        </div>

        <span
          className="agent-shell-divider-ghost"
          ref={dividerGhostRef}
          aria-hidden="true"
          data-testid="agent-shell-divider-ghost"
        />

        <section className="agent-chat-column" aria-label={t('shell.chatColumn')}>
          <header className="agent-chat-header">
            <div className="agent-chat-header-left">
              <span className="agent-chat-pi-orb" aria-hidden="true">π</span>
              <h2 title={t('shell.piBackendDetail')}>{t('shell.piBackend')}</h2>
              <span
                className="agent-chat-pi-badge"
                data-testid="pi-backend-badge"
                title={t('shell.piBackendDetail')}
              >
                Pi · {credentialMode === 'local' ? 'Local' : 'Read only'}
              </span>
            </div>
            <div className="agent-chat-header-right">
              <button
                type="button"
                className="agent-pane-toggle agent-pane-close"
                aria-label={t('shell.collapseAgent')}
                title={t('shell.collapseAgent')}
                onClick={() => setCollapsed('agent')}
              >
                ✕
              </button>
            </div>
          </header>

          <div className="agent-chat-body">
            {api && postJson ? (
              <Suspense fallback={<div className="flow-agent-loading">{t('configure.agentLoading')}</div>}>
                <AgentHarness
                  key={chatKey}
                  api={api}
                  postJson={postJson}
                  Status={FlowStatus}
                  shell
                  t={t}
                  queuedCommand={harnessTask}
                  onQueuedCommandSent={onQueuedCommandSent}
                  onRequestNewChat={onRequestNewChat}
                  onAdoptSql={adoptSqlFromAgent}
                  pageContext={agentPageContext}
                  onAgentAction={runAgentAction}
                />
              </Suspense>
            ) : (
              <div className="pi-chat-empty">
                <b>{t('agent.unavailable')}</b>
              </div>
            )}
          </div>
        </section>

        <button
          className="agent-chat-restore"
          type="button"
          aria-label={t('shell.expandAgent')}
          title={t('shell.expandAgent')}
          onClick={() => setCollapsed('agent')}
        >
          <span>π</span>
          <b>{t('shell.piBackend')}</b>
        </button>
        </>
        ) : null}
      </div>
    </main>
  )
}
