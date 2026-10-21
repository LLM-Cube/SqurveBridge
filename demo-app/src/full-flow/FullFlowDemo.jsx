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
import { PROCESS_STEPS, resolveProcessStep } from './processSteps.js'
import { INITIAL_RUN_STATE } from './RunWorkspace.jsx'
import { displayNamesFor } from './resultsModel.js'
import { FlowStatus } from './flowUi.jsx'
import './full-flow.css'
import './agent-shell.css'
import './ui-enhancements.css'

const AgentHarness = lazy(() => import('../AgentHarness.jsx'))
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
  const [locale, setLocale] = useState(() => detectLocale(
    navigator.language,
    window.localStorage.getItem('squrve-demo-locale'),
  ))
  const [activeStep, setActiveStep] = useState(() => resolveProcessStep(window.location.hash))
  const [selectedConnections, setSelectedConnections] = useState([
    { method: METHODS[0], database: DATABASES[0] },
  ])
  const connectionsRef = useRef(selectedConnections)
  const [focusedMethod, setFocusedMethod] = useState(METHODS[0])
  const [focusedDatabase, setFocusedDatabase] = useState(DATABASES[0])
  const [sampleLimit, setSampleLimit] = useState(20)
  const [sampleMode, setSampleMode] = useState('slice')
  const [sampleSeed, setSampleSeed] = useState(42)
  const [runState, setRunState] = useState(INITIAL_RUN_STATE)
  const [experimentId, setExperimentId] = useState('')
  const [queryPrefill, setQueryPrefill] = useState(null)
  const [chatKey, setChatKey] = useState(0)
  const [harnessTask, setHarnessTask] = useState(null)
  const [shellLayout, setShellLayout] = useState(loadShellLayout)
  const [mobilePane, setMobilePane] = useState('dashboard')
  const [adoptedSql, setAdoptedSql] = useState(null)
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
    const syncFromHash = () => setActiveStep(resolveProcessStep(window.location.hash))
    window.addEventListener('hashchange', syncFromHash)
    return () => window.removeEventListener('hashchange', syncFromHash)
  }, [])

  useEffect(() => {
    // Stage pages share one scroll container; reset it so the new page
    // header is never clipped by a stale scroll offset.
    if (workspaceBodyRef.current) workspaceBodyRef.current.scrollTop = 0
  }, [activeStep])

  const navigateToStep = useCallback(step => {
    const next = resolveProcessStep(step)
    setActiveStep(next)
    if (window.location.hash !== `#${next}`) {
      window.history.replaceState(null, '', `#${next}`)
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
  const replayInQuery = useCallback(({ method, dataset, db_id: dbId, question }) => {
    const names = displayNamesFor(method, dataset)
    if (names) onFocusConnection(names.method, names.database)
    setQueryPrefill({ id: Date.now(), dbId, question })
    navigateToStep('query')
    setMobilePane('dashboard')
  }, [onFocusConnection, navigateToStep])

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
    compose: (
      <>
      <MemoExperimentGrid
        api={api}
        configs={configs}
        connections={connections}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        onToggleConnection={onToggleConnection}
        onSelectLine={onSelectGridLine}
        t={t}
      />
      <MemoConnectionComposer
        selectedMethods={selectedMethods}
        selectedDatabases={selectedDatabases}
        selectedConnections={connections}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        onToggleConnection={onToggleConnection}
        onFocusConnection={onFocusConnection}
        configs={configs}
        focusedConfig={focusedConfig}
        t={t}
      />
      </>
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
        onAskPi={askPiFromQuery}
        prefill={queryPrefill}
        t={t}
      />
    ),
    board: (
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
        liveEvaluation={Boolean(capabilities?.deployment?.features?.live_evaluation)}
        t={t}
      />
    ),
    evidence: (
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
    ),
  }

  const nextLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN'
  const languageLabel = locale === 'zh-CN'
    ? t('language.switchToEnglish')
    : t('language.switchToChinese')
  const projectLabel = focusedConfig
    ? `${focusedConfig.method} · ${focusedConfig.dataset}`
    : t('shell.workspace')
  const dashboardCollapsed = shellLayout.dashboardCollapsed && !shellLayout.agentCollapsed
  const agentCollapsed = shellLayout.agentCollapsed && !shellLayout.dashboardCollapsed
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
        `mobile-pane-${mobilePane}`,
      ].filter(Boolean).join(' ')}
      data-testid="agent-shell"
      style={shellStyle}
    >
      <div className="agent-shell-notice">
        {t(credentialMode === 'local' ? 'local.notice' : 'hosted.notice')}
      </div>

      <div className="agent-mobile-switcher" role="tablist" aria-label={t('shell.workspace')}>
        <button type="button" role="tab" aria-selected={mobilePane === 'dashboard'} onClick={() => setMobilePane('dashboard')}>
          {t('shell.liveWorkspace')}
        </button>
        <button type="button" role="tab" aria-selected={mobilePane === 'agent'} onClick={() => setMobilePane('agent')}>
          {t('shell.piBackend')}
        </button>
      </div>

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
                <button
                  type="button"
                  className="agent-pane-toggle"
                  aria-label={t('shell.collapseDashboard')}
                  onClick={() => setCollapsed('dashboard')}
                >
                  ‹
                </button>
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
      </div>
    </main>
  )
}
