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
  postJson,
  api,
  onRunStateChange,
  onExperimentChange,
  adoptExperimentId = '',
  liveEvaluation = false,
  variants = [],
  t,
  ...planProps
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
        {...planProps}
        selectedConnections={selectedConnections}
        configs={configs}
        focusedMethod={focusedMethod}
        focusedDatabase={focusedDatabase}
        onFocusConnection={onFocusConnection}
        databases={databases}
        sampleLimit={sampleLimit}
        sampleMode={sampleMode}
        sampleSeed={sampleSeed}
        postJson={postJson}
        api={api}
        onRunStateChange={onRunStateChange}
        onExperimentChange={onExperimentChange}
        adoptExperimentId={adoptExperimentId}
        liveEvaluation={liveEvaluation}
        variants={variants}
        t={t}
      />
    </section>
  )
}
