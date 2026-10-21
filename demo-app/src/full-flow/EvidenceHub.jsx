import { useEffect, useState } from 'react'
import Archive from '../Archive.jsx'
import ExperimentBoard from '../ExperimentBoard.jsx'
import { FlowEmpty, FlowPageHeading, FlowStatus } from './flowUi.jsx'
import ResultsMatrix from './ResultsMatrix.jsx'

export default function EvidenceHub({
  pageId = 'evidence',
  capabilities,
  api,
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
  t,
}) {
  const [expandedRunId, setExpandedRunId] = useState(archiveFocusRunId || '')
  const [chartsOpen, setChartsOpen] = useState(Boolean(archiveFocusRunId))
  const expanded = Boolean(expandedRunId)
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
          <ResultsMatrix
            api={api}
            configs={configs}
            connections={connections}
            experimentId={experimentId}
            sampleLimit={sampleLimit}
            sampleMode={sampleMode}
            sampleSeed={sampleSeed}
            liveEvaluation={liveEvaluation}
            onOpenRun={expandRun}
            t={t}
          />
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
