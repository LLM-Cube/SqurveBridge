import { memo, useCallback, useMemo } from 'react'
import ActorWorkflow from './ActorWorkflow.jsx'
import IntegrationProvenance from './IntegrationProvenance.jsx'
import { buildReadyKeys, configKey } from './model.js'
import './connection-composer.css'

const MemoActorWorkflow = memo(ActorWorkflow)
const MemoIntegrationProvenance = memo(IntegrationProvenance)

const ConnectionSwitcher = memo(function ConnectionSwitcher({
  connections,
  focusedKey,
  readyKeys,
  focusedIndex,
  t,
  onFocusConnection,
  onRemoveConnection,
}) {
  if (!connections.length) {
    return <p className="compose-switcher-empty">{t('compose.noConnections')}</p>
  }

  return (
    <div className="compose-connection-switcher" data-testid="compose-connection-switcher">
      <div className="compose-switcher-toolbar">
        <span>{t('compose.viewingWorkflow')}</span>
        <div className="compose-switcher-nav">
          <button
            type="button"
            aria-label={t('compose.prevConnection')}
            disabled={connections.length < 2}
            onClick={() => {
              const previous = connections[(focusedIndex - 1 + connections.length) % connections.length]
              onFocusConnection(previous.method, previous.database)
            }}
          >
            ‹
          </button>
          <strong>
            {t('compose.connectionIndex', {
              current: focusedIndex + 1,
              total: connections.length,
            })}
          </strong>
          <button
            type="button"
            aria-label={t('compose.nextConnection')}
            disabled={connections.length < 2}
            onClick={() => {
              const next = connections[(focusedIndex + 1) % connections.length]
              onFocusConnection(next.method, next.database)
            }}
          >
            ›
          </button>
        </div>
      </div>
      <div className="compose-switcher-list" role="listbox" aria-label={t('compose.selectedConnections')}>
        {connections.map(connection => {
          const active = focusedKey === connection.key
          const ready = readyKeys.has(connection.key)
          const removeLabel = t('compose.removeConnection', {
            method: connection.method,
            database: connection.database,
          })
          return (
            <div
              key={connection.key}
              className={[
                'compose-switcher-item',
                active ? 'active' : '',
                ready ? 'ready' : 'unavailable',
              ].filter(Boolean).join(' ')}
              role="option"
              aria-selected={active}
            >
              <button
                type="button"
                className="compose-switcher-focus"
                aria-label={t('compose.focusConnection', {
                  method: connection.method,
                  database: connection.database,
                })}
                aria-pressed={active}
                onClick={() => onFocusConnection(connection.method, connection.database)}
              >
                <strong>{connection.method}</strong>
                <span aria-hidden="true"> → </span>
                <strong>{connection.database}</strong>
                {!ready && <em>{t('compose.browsable')}</em>}
              </button>
              <button
                type="button"
                className="compose-switcher-remove"
                aria-label={removeLabel}
                title={removeLabel}
                disabled={connections.length < 2}
                onClick={() => onRemoveConnection(connection.method, connection.database)}
              >
                ×
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
})

/**
 * The focused combination: the selected pairs to step through, its Actor
 * workflow and its provenance. Selection itself happens in the experiment
 * matrix, so there is one place to choose what runs.
 */
export default function ConnectionComposer({
  selectedConnections,
  focusedMethod,
  focusedDatabase,
  onToggleConnection,
  onFocusConnection,
  configs,
  focusedConfig,
  stageActors,
  variants,
  onAddVariant,
  onRemoveVariant,
  t,
}) {
  const readyKeys = useMemo(() => buildReadyKeys(configs), [configs])
  const focusedKey = configKey(focusedMethod, focusedDatabase)
  const connections = useMemo(() => selectedConnections || [], [selectedConnections])
  const focusedIndex = Math.max(0, connections.findIndex(item => item.key === focusedKey))

  // The last pair cannot be removed: the experiment always has something to run.
  const removeConnection = useCallback((method, database) => {
    if (connections.length < 2) return
    onToggleConnection(method, database)
  }, [connections, onToggleConnection])

  return <section id="compose" className="flow-module flow-glass connection-composer">
    <header className="flow-module-header">
      <div>
        <span>{t('process.compose')}</span>
        <h2>{t('compose.title')}</h2>
        <p>{t('compose.workflowDescription')}</p>
      </div>
    </header>

    <aside className="compose-workflow-panel compose-workflow-panel-solo" data-testid="compose-workflow-panel">
      <ConnectionSwitcher
        connections={connections}
        focusedKey={focusedKey}
        readyKeys={readyKeys}
        focusedIndex={focusedIndex}
        t={t}
        onFocusConnection={onFocusConnection}
        onRemoveConnection={removeConnection}
      />
      <MemoActorWorkflow
        focusedConfig={focusedConfig}
        stageActors={stageActors}
        variants={variants}
        onAddVariant={onAddVariant}
        onRemoveVariant={onRemoveVariant}
        t={t}
      />
      <MemoIntegrationProvenance focusedConfig={focusedConfig} t={t} />
    </aside>
  </section>
}
