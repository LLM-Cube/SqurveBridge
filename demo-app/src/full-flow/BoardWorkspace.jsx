import RunWorkspace from './RunWorkspace.jsx'

export default function BoardWorkspace({
  selectedConnections,
  configs,
  focusedMethod,
  focusedDatabase,
  onFocusConnection,
  databases,
  sampleLimit,
  sampleMode,
  sampleSeed,
  onSampleLimitChange,
  onSampleModeChange,
  onSampleSeedChange,
  postJson,
  api,
  onRunStateChange,
  onExperimentChange,
  liveEvaluation = false,
  t,
}) {
  return (
    <section id="board" className="flow-module flow-glass board-workspace-flow" data-testid="board-workspace">
      <header className="flow-module-header">
        <div>
          <span>{t('process.board')}</span>
          <h2>{t('board.title')}</h2>
          <p>{t('board.description')}</p>
        </div>
      </header>

      <RunWorkspace
        selectedConnections={selectedConnections}
        configs={configs}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        onFocusConnection={onFocusConnection}
        databases={databases}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        onSampleLimitChange={onSampleLimitChange}
        onSampleModeChange={onSampleModeChange}
        onSampleSeedChange={onSampleSeedChange}
        postJson={postJson}
        api={api}
        onRunStateChange={onRunStateChange}
        onExperimentChange={onExperimentChange}
        liveEvaluation={liveEvaluation}
        t={t}
      />
    </section>
  )
}
