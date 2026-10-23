import { useEffect, useReducer, useState } from 'react'
import Archive from '../Archive.jsx'
import ExperimentBoard from '../ExperimentBoard.jsx'
import ExperimentHistory from './ExperimentHistory.jsx'
import { FlowEmpty, FlowPageHeading, FlowStatus } from './flowUi.jsx'
import { comparisonReducer, initialComparison } from './comparisonSet.js'
import ResultsMatrix from './ResultsMatrix.jsx'

export default function EvidenceHub({
  pageId = 'evidence',
  capabilities,
  api,
  comparisonRequest,
  refreshKey,
  postJson,
  liveEvaluation = false,
  focusedConfig,
  selectedMethods = [],
  sampleLimit,
  sampleMode,
  sampleSeed,
  archiveFocusRunId = '',
  onClearArchiveFocus,
  configs = [],
  connections = [],
  experimentId = '',
  onOpenExperiment,
  onUseTemplate,
  onReplay,
  t,
}) {
  const [comparisonState, onComparisonAction] = useReducer(comparisonReducer, undefined, initialComparison)
  const [view, setView] = useState('current')
  const [historySource, setHistorySource] = useState(liveEvaluation ? 'experiments' : 'recorded')
  const [owningExperimentId, setOwningExperimentId] = useState('')
  const [expandedRunId, setExpandedRunId] = useState(archiveFocusRunId || '')
  const [chartsOpen, setChartsOpen] = useState(Boolean(archiveFocusRunId))
  const expanded = Boolean(expandedRunId)
  useEffect(() => {
    if (!experimentId && !comparisonRequest) return
    setView('current')
    setExpandedRunId('')
    setChartsOpen(false)
  }, [experimentId, comparisonRequest])
  const initialMethods = selectedMethods.length
    ? selectedMethods.map(item => String(item).toLowerCase())
    : focusedConfig?.method
      ? [String(focusedConfig.method).toLowerCase()]
      : undefined

  useEffect(() => {
    if (!archiveFocusRunId) return
    setExpandedRunId(archiveFocusRunId)
    setChartsOpen(true)
  }, [archiveFocusRunId])

  const expandRun = runId => {
    const id = String(runId || '')
    if (!id) return
    setExpandedRunId(id)
    setChartsOpen(false)
    setOwningExperimentId('')
    if (liveEvaluation) api?.('/api/experiments').then(data => {
      const owner = (data?.experiments || []).find(experiment => experiment.cells?.some(cell => cell.run_id === id))
      setOwningExperimentId(owner?.experiment_id || '')
    }).catch(() => {})
  }

  const collapseRun = () => {
    setExpandedRunId('')
    setChartsOpen(false)
    onClearArchiveFocus?.()
  }

  const expandCharts = runId => {
    const id = String(runId || expandedRunId || '')
    if (!id) return
    setExpandedRunId(id)
    setChartsOpen(true)
    window.dispatchEvent(new window.CustomEvent('squrve:archive-focus', { detail: id }))
  }

  const collapseCharts = () => {
    setChartsOpen(false)
    onClearArchiveFocus?.()
  }

  return (
    <section
      id={pageId}
      className={`flow-module flow-glass evidence-hub${expanded ? ' evidence-hub-expanded' : ' evidence-hub-browse'}`}
      data-testid="evidence-workspace"
      data-expanded={expanded ? 'true' : 'false'}
      data-charts-open={chartsOpen ? 'true' : 'false'}
    >
      <header className="flow-module-header evidence-hub-header">
        <div>
          <span>{t('process.evidence')}</span>
          <h2>{expanded ? t('evidence.runTitle') : t('evidence.title')}</h2>
          <p>{expanded ? t('evidence.runDescription') : t('evidence.browseDescription')}</p>
        </div>
        {expanded ? (
          <div className="evidence-hub-toolbar">
            {owningExperimentId && <button type="button" className="flow-secondary-action" onClick={() => { onOpenExperiment?.(owningExperimentId); setView('current'); collapseRun() }}>{t('plan.backToExperiment')}</button>}
            {chartsOpen ? (
              <button type="button" className="flow-secondary-action" onClick={collapseCharts}>
                {t('evidence.collapseCharts')}
              </button>
            ) : null}
            <button type="button" className="flow-secondary-action" onClick={collapseRun}>
              {t('evidence.collapseRun')}
            </button>
          </div>
        ) : null}
      </header>

      <div className="evidence-hub-body">
        {!expanded ? (
          <>
          <nav className="evidence-view-tabs" aria-label={t('plan.resultViews')}>
            <button type="button" aria-pressed={view === 'current'} onClick={() => setView('current')}>{t('plan.currentExperiment')}</button>
            <button type="button" aria-pressed={view === 'history'} onClick={() => setView('history')}>{t('history.title')}</button>
          </nav>
          {view === 'history' && <nav className="evidence-view-tabs" aria-label={t('plan.historySources')}>
            {['experiments', 'archive', 'recorded'].filter(source => liveEvaluation || source === 'recorded').map(source => (
              <button type="button" key={source} aria-pressed={historySource === source} onClick={() => setHistorySource(source)}>{t(`plan.history.${source}`)}</button>
            ))}
          </nav>}
          <div hidden={view !== 'history' || historySource !== 'experiments'}>
            <ExperimentHistory
              api={liveEvaluation ? api : undefined}
              refreshKey={refreshKey}
              currentExperimentId={experimentId}
              onOpenExperiment={id => { onOpenExperiment?.(id); setView('current') }}
              onUseTemplate={onUseTemplate}
              t={t}
            />
          </div>
          {view === 'current' && liveEvaluation && !experimentId ? <p className="results-note">{t('plan.noCurrentExperiment')}</p> : view === 'history' && historySource === 'experiments' ? null : (
            <ResultsMatrix
              comparisonState={comparisonState}
              onComparisonAction={onComparisonAction}
              key={`${view}-${historySource}-${experimentId}`}
              api={api}
              configs={configs}
              connections={connections}
              experimentId={view === 'current' ? experimentId : ''}
              fixedSource={view === 'current' ? (experimentId ? 'experiment' : 'recorded') : (historySource === 'recorded' ? 'recorded' : 'archive')}
              sampleLimit={sampleLimit}
              sampleMode={sampleMode}
              sampleSeed={sampleSeed}
              liveEvaluation={liveEvaluation}
              postJson={postJson}
              onOpenRun={expandRun}
              onReplay={onReplay}
              t={t}
            />
          )}
          <div hidden={view !== 'history' || historySource === 'experiments'}>
          <Archive
            embedded
            mode="browse"
            allowFileContent={capabilities?.deployment?.target !== 'hf-space'}
            api={api}
            Status={FlowStatus}
            PageHeading={FlowPageHeading}
            Empty={FlowEmpty}
            onExpandRun={expandRun}
            t={t}
          />
          </div>
          </>
        ) : (
          <div className="evidence-run-page" data-testid="evidence-run-page">
            <Archive
              embedded
              mode="detail"
              runId={expandedRunId}
              allowFileContent={capabilities?.deployment?.target !== 'hf-space'}
              api={api}
              Status={FlowStatus}
              PageHeading={FlowPageHeading}
              Empty={FlowEmpty}
              onOpenInVisualize={expandCharts}
              t={t}
            />

            {chartsOpen ? (
              <section className="evidence-charts-panel" data-testid="evidence-charts-panel">
                <header className="evidence-charts-panel-head">
                  <div>
                    <span>{t('evidence.chartsEyebrow')}</span>
                    <strong>{expandedRunId}</strong>
                  </div>
                  <button type="button" className="flow-secondary-action" onClick={collapseCharts}>
                    {t('evidence.collapseCharts')}
                  </button>
                </header>
                <ExperimentBoard
                  t={t}
                  embedded
                  viewOnly
                  capabilities={capabilities}
                  liveEvaluation={liveEvaluation}
                  api={api}
                  postJson={postJson}
                  Status={FlowStatus}
                  PageHeading={FlowPageHeading}
                  Empty={FlowEmpty}
                  initialDataset={focusedConfig?.dataset}
                  initialSplit={focusedConfig?.split}
                  initialMethods={initialMethods}
                  initialSampleLimit={sampleLimit}
                  initialSampleMode={sampleMode}
                  initialSampleSeed={sampleSeed}
                  archiveRunId={expandedRunId}
                />
              </section>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}
