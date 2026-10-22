import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ActorWorkflow from './ActorWorkflow.jsx'
import IntegrationProvenance from './IntegrationProvenance.jsx'
import {
  DATABASES,
  METHODS,
  buildReadyKeys,
  configKey,
  hasConnection,
} from './model.js'
import './connection-composer.css'

// Curve endpoints are measured from the rendered node rows rather than derived
// from a hardcoded row pitch, so the canvas stays aligned when the pane is
// resized or the node styling changes.
const edgePath = (geometry, methodIndex, databaseIndex) => {
  const startY = geometry.methods[methodIndex]
  const endY = geometry.databases[databaseIndex]
  if (startY == null || endY == null) return null
  const width = geometry.width
  return `M 0 ${startY} C ${width * 0.4} ${startY}, ${width * 0.6} ${endY}, ${width} ${endY}`
}

const measureCenters = (list, base) => Array.from(list.children).map(item => {
  const rect = item.getBoundingClientRect()
  return rect.top + rect.height / 2 - base.top
})

// Memoized so hover-driven parent renders skip the (potentially heavy)
// workflow/provenance panels whose props stay stable while hovering.
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

// One connected curve. The canvas is a read-only overview now — every pair is
// operated from the grid below — so edges carry no hit area of their own.
const GraphEdge = memo(function GraphEdge({ path, focused, ready, nodeHover }) {
  const className = [
    ready ? 'ready' : 'unavailable',
    'selected',
    focused ? 'focused' : '',
    nodeHover ? 'node-hover' : '',
  ].filter(Boolean).join(' ')
  return <path className={className} d={path} pathLength="1" pointerEvents="none" />
})

// Full method × database surface. Every pair is a cell, so connecting no longer
// depends on hitting a thin curve, and unconnected pairs cost no ink on the
// canvas above. Removing lives on a separate control per connected cell: the
// cell body only connects or inspects, so no click can delete by surprise.
const ConnectionGrid = memo(function ConnectionGrid({
  connections,
  focusedKey,
  readyKeys,
  hoveredMethod,
  hoveredDatabase,
  lastConnection,
  t,
  onActivate,
  onRemove,
  onHoverCell,
}) {
  return <div className="compose-grid-scroll">
    <table className="compose-grid" data-testid="compose-grid" aria-label={t('compose.gridLabel')}>
      <caption>{t('compose.gridHint')}</caption>
      <thead>
        <tr>
          <td />
          {DATABASES.map(database => (
            <th
              key={database}
              scope="col"
              data-peer={database === hoveredDatabase ? 'true' : 'false'}
            >
              <span>{database}</span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {METHODS.map(method => (
          <tr key={method} data-peer={method === hoveredMethod ? 'true' : 'false'}>
            <th scope="row" data-peer={method === hoveredMethod ? 'true' : 'false'}>
              {method}
            </th>
            {DATABASES.map(database => {
              const key = configKey(method, database)
              const connected = hasConnection(connections, method, database)
              const focused = key === focusedKey
              const ready = readyKeys.has(key)
              const protectedLast = connected && lastConnection
              const dropLabel = t('compose.dropConnection', { method, database })
              return (
                <td key={database} data-peer={database === hoveredDatabase ? 'true' : 'false'}>
                  <button
                    type="button"
                    className={[
                      'compose-grid-cell',
                      connected ? 'connected' : '',
                      focused ? 'focused' : '',
                      ready ? 'ready' : 'unavailable',
                    ].filter(Boolean).join(' ')}
                    aria-label={t('compose.toggleConnection', { method, database })}
                    aria-pressed={connected}
                    title={focused
                      ? t('compose.cellFocused', { method, database })
                      : connected
                        ? t('compose.cellInspect', { method, database })
                        : t('compose.cellConnect', { method, database })}
                    onClick={() => onActivate(method, database)}
                    onMouseEnter={() => onHoverCell(method, database)}
                    onMouseLeave={() => onHoverCell(null)}
                    onFocus={() => onHoverCell(method, database)}
                    onBlur={() => onHoverCell(null)}
                  >
                    <span aria-hidden="true" />
                  </button>
                  {connected ? (
                    // Sibling of the cell button, never a child: a button inside
                    // a button is invalid and unreachable for assistive tech.
                    <button
                      type="button"
                      className="compose-grid-drop"
                      aria-label={dropLabel}
                      title={protectedLast ? t('compose.liveKeepLast') : dropLabel}
                      aria-disabled={protectedLast ? 'true' : undefined}
                      onClick={() => onRemove(method, database)}
                      onMouseEnter={() => onHoverCell(method, database)}
                      onMouseLeave={() => onHoverCell(null)}
                      onFocus={() => onHoverCell(method, database)}
                      onBlur={() => onHoverCell(null)}
                    >
                      ×
                    </button>
                  ) : null}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
})

export default function ConnectionComposer({
  selectedMethods,
  selectedDatabases,
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
  const [hoveredNode, setHoveredNode] = useState(null)
  // The pair the pointer is on, wherever it came from: a grid cell now, since
  // the canvas itself is read-only.
  const [hoveredCell, setHoveredCell] = useState(null)
  const [geometry, setGeometry] = useState(null)
  const canvasRef = useRef(null)
  const methodListRef = useRef(null)
  const databaseListRef = useRef(null)
  // Node-first wiring: `armed` remembers the node the user picked as the
  // starting point of a connection ({ side: 'method'|'database', name }).
  const [armed, setArmed] = useState(null)
  const [liveMessage, setLiveMessage] = useState('')
  // The last removal, kept only until the next composition step, so a removal
  // needs no confirmation dialog to stay recoverable.
  const [undoEntry, setUndoEntry] = useState(null)

  // Re-measure on mount and whenever the pane resizes. Without ResizeObserver
  // (jsdom) the canvas simply renders no curves; the grid stays authoritative.
  useEffect(() => {
    const canvas = canvasRef.current
    const methodList = methodListRef.current
    const databaseList = databaseListRef.current
    if (!canvas || !methodList || !databaseList) return undefined

    const measure = () => {
      const base = canvas.getBoundingClientRect()
      if (!base.width || !base.height) {
        setGeometry(null)
        return
      }
      setGeometry({
        width: base.width,
        height: base.height,
        methods: measureCenters(methodList, base),
        databases: measureCenters(databaseList, base),
      })
    }

    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(canvas)
    observer.observe(methodList)
    observer.observe(databaseList)
    return () => observer.disconnect()
  }, [])

  const nodeTouchesHoveredEdge = name =>
    Boolean(hoveredCell && (hoveredCell.method === name || hoveredCell.database === name))
  const nodeLinkedToHoveredNode = (name, side) => {
    if (!hoveredNode || hoveredNode.side === side) return false
    return connections.some(connection => (side === 'method'
      ? connection.method === name && connection.database === hoveredNode.name
      : connection.database === name && connection.method === hoveredNode.name))
  }

  const removeConnection = useCallback((method, database) => {
    if (connections.length === 1) {
      setLiveMessage(t('compose.liveKeepLast'))
      return
    }
    setUndoEntry({
      method,
      database,
      // Dropping the inspected pair moves the inspection elsewhere, so undo has
      // to restore the previous inspection target as well as the connection.
      previousMethod: focusedMethod,
      previousDatabase: focusedDatabase,
      wasFocused: configKey(method, database) === focusedKey,
    })
    onToggleConnection(method, database)
    setLiveMessage(t('compose.liveRemoved', { method, database }))
  }, [connections, focusedKey, focusedMethod, focusedDatabase, onToggleConnection, t])

  const undoRemoval = useCallback(() => {
    if (!undoEntry) return
    // ensureConnection semantics: re-adds the pair and inspects it, without the
    // risk of a toggle removing it again.
    onFocusConnection(undoEntry.method, undoEntry.database)
    if (!undoEntry.wasFocused) {
      onFocusConnection(undoEntry.previousMethod, undoEntry.previousDatabase)
    }
    setUndoEntry(null)
    setLiveMessage(t('compose.liveRestored', {
      method: undoEntry.method,
      database: undoEntry.database,
    }))
  }, [undoEntry, onFocusConnection, t])

  // Grid cells are two-state: not connected → connect; connected → inspect.
  // Removing is the cell's own × control, so no click can delete by accident.
  const inspectConnection = useCallback((method, database) => {
    setUndoEntry(null)
    if (!hasConnection(connections, method, database)) {
      onToggleConnection(method, database)
      setLiveMessage(t('compose.liveConnected', { method, database }))
      return
    }
    if (focusedKey !== configKey(method, database)) {
      onFocusConnection(method, database)
      setLiveMessage(t('compose.liveFocused', { method, database }))
    }
  }, [connections, focusedKey, onToggleConnection, onFocusConnection, t])

  const focusConnection = useCallback((method, database) => {
    setUndoEntry(null)
    onFocusConnection(method, database)
  }, [onFocusConnection])

  // Node wiring stays tri-state: wiring onto the pair you are already
  // inspecting is the gesture's own way of cutting that connection.
  const activateConnection = useCallback((method, database) => {
    const selected = hasConnection(connections, method, database)
    setUndoEntry(null)
    if (!selected) {
      onToggleConnection(method, database)
      setLiveMessage(t('compose.liveConnected', { method, database }))
      return
    }
    if (focusedKey !== configKey(method, database)) {
      onFocusConnection(method, database)
      setLiveMessage(t('compose.liveFocused', { method, database }))
      return
    }
    removeConnection(method, database)
  }, [connections, focusedKey, onToggleConnection, onFocusConnection, removeConnection, t])

  const handleNodeActivate = useCallback((side, name) => {
    if (!armed) {
      setArmed({ side, name })
      return
    }
    if (armed.side === side) {
      // Same side: clicking the armed node cancels, another node re-arms.
      setArmed(armed.name === name ? null : { side, name })
      return
    }
    const method = side === 'method' ? name : armed.name
    const database = side === 'database' ? name : armed.name
    activateConnection(method, database)
  }, [armed, activateConnection])

  const cancelArmed = useCallback(() => setArmed(null), [])

  const handleGraphKeyDown = useCallback(event => {
    if (event.key === 'Escape' && armed) {
      event.stopPropagation()
      cancelArmed()
    }
  }, [armed, cancelArmed])

  const handleBlankClick = useCallback(event => {
    if (!armed) return
    if (event.target.closest?.('button, path, a, input, select, textarea')) return
    cancelArmed()
  }, [armed, cancelArmed])

  const edgeTouchesHoveredNode = edge =>
    Boolean(hoveredNode && (hoveredNode.side === 'method'
      ? edge.method === hoveredNode.name
      : edge.database === hoveredNode.name))
  const edgeTouchesArmedNode = edge =>
    Boolean(armed && (armed.side === 'method'
      ? edge.method === armed.name
      : edge.database === armed.name))

  // Only connected pairs get a curve; the grid below carries every other pair.
  // Focused last so it paints on top without reordering on hover.
  const overviewEdges = useMemo(() => {
    if (!geometry) return []
    return connections
      .map(connection => {
        const methodIndex = METHODS.indexOf(connection.method)
        const databaseIndex = DATABASES.indexOf(connection.database)
        if (methodIndex < 0 || databaseIndex < 0) return null
        const path = edgePath(geometry, methodIndex, databaseIndex)
        if (!path) return null
        return {
          method: connection.method,
          database: connection.database,
          key: configKey(connection.method, connection.database),
          path,
        }
      })
      .filter(Boolean)
      .sort((left, right) => Number(left.key === focusedKey) - Number(right.key === focusedKey))
  }, [connections, focusedKey, geometry])

  // The pair the pointer is proposing but has not created yet, drawn as a
  // dashed ghost so the canvas previews what a grid click or wiring step does.
  const previewEdge = useMemo(() => {
    if (!geometry) return null
    const candidate = hoveredCell
      || (armed && hoveredNode && armed.side !== hoveredNode.side
        ? {
          method: armed.side === 'method' ? armed.name : hoveredNode.name,
          database: armed.side === 'database' ? armed.name : hoveredNode.name,
        }
        : null)
    if (!candidate) return null
    if (hasConnection(connections, candidate.method, candidate.database)) return null
    const path = edgePath(
      geometry,
      METHODS.indexOf(candidate.method),
      DATABASES.indexOf(candidate.database),
    )
    return path ? { ...candidate, path } : null
  }, [armed, connections, geometry, hoveredCell, hoveredNode])

  const hoveredMethod = hoveredCell?.method
    || (hoveredNode?.side === 'method' ? hoveredNode.name : undefined)
  const hoveredDatabase = hoveredCell?.database
    || (hoveredNode?.side === 'database' ? hoveredNode.name : undefined)

  const handleCellHover = useCallback((method, database) => {
    setHoveredCell(method ? { method, database } : null)
  }, [])

  const hoveredCellKey = hoveredCell
    ? configKey(hoveredCell.method, hoveredCell.database)
    : null

  const hoveredSelected = hoveredCell
    ? hasConnection(connections, hoveredCell.method, hoveredCell.database)
    : false
  const hoveredFocused = hoveredCell
    ? configKey(hoveredCell.method, hoveredCell.database) === focusedKey
    : false

  const nodeLabel = (side, name) => {
    if (armed && armed.side !== side) {
      const method = side === 'method' ? name : armed.name
      const database = side === 'database' ? name : armed.name
      return t('compose.wireTo', { method, database })
    }
    if (armed && armed.side === side && armed.name === name) {
      return t('compose.cancelWire', { name })
    }
    return t(side === 'method' ? 'compose.armMethod' : 'compose.armDatabase', { name })
  }

  const nodeWiringClasses = (side, name) => {
    if (!armed) return []
    if (armed.side === side) {
      return armed.name === name ? ['armed'] : []
    }
    const method = side === 'method' ? name : armed.name
    const database = side === 'database' ? name : armed.name
    const key = configKey(method, database)
    return [
      'wire-target',
      readyKeys.has(key) ? 'wire-target-ready' : '',
      hasConnection(connections, method, database) ? 'wire-target-connected' : '',
    ]
  }

  const wireHint = armed
    ? t('compose.wireHintArmed', { name: armed.name })
    : connections.length
      ? t('compose.wireHintIdle')
      : t('compose.wireHintEmpty')

  return <section
    id="compose"
    className="flow-module flow-glass connection-composer"
    onClick={handleBlankClick}
  >
    <header className="flow-module-header">
      <div>
        <span>{t('process.compose')}</span>
        <h2>{t('compose.title')}</h2>
        <p>{t('compose.description')}</p>
      </div>
    </header>

    <div className="flow-compose-grid">
      <div className="connection-matrix">
        <div className="connection-matrix-header">
          <h3>{t('compose.matrixLabel')}</h3>
          <p>{t('compose.matrixHint')}</p>
        </div>
        <div className="connection-axis" aria-hidden="true">
          <span>{t('compose.methods')}</span>
          <span>{t('compose.databases')}</span>
        </div>
        <div
          className="flow-connection-graph"
          data-has-selection={connections.length > 0 ? 'true' : 'false'}
          data-node-hover={hoveredNode ? 'true' : 'false'}
          data-armed={armed ? 'true' : 'false'}
          onKeyDown={handleGraphKeyDown}
        >
          <ol className="flow-graph-nodes flow-method-nodes" ref={methodListRef}>
            {METHODS.map(method => <li key={method}>
              <button
                type="button"
                aria-label={nodeLabel('method', method)}
                aria-pressed={selectedMethods.includes(method)}
                className={[
                  selectedMethods.includes(method) ? 'selected' : '',
                  focusedMethod === method ? 'focused' : '',
                  nodeTouchesHoveredEdge(method) ? 'edge-peer' : '',
                  nodeLinkedToHoveredNode(method, 'method') ? 'peer-linked' : '',
                  ...nodeWiringClasses('method', method),
                ].filter(Boolean).join(' ')}
                onClick={() => handleNodeActivate('method', method)}
                onMouseEnter={() => setHoveredNode({ side: 'method', name: method })}
                onMouseLeave={() => setHoveredNode(null)}
                onFocus={() => setHoveredNode({ side: 'method', name: method })}
                onBlur={() => setHoveredNode(null)}
              >
                {method}
              </button>
            </li>)}
          </ol>
          <div className="flow-connection-canvas" ref={canvasRef}>
            <svg
              viewBox={geometry ? `0 0 ${geometry.width} ${geometry.height}` : undefined}
              width={geometry?.width || undefined}
              height={geometry?.height || undefined}
              role="group"
              aria-labelledby="flow-matrix-title flow-matrix-description"
            >
              <title id="flow-matrix-title">{t('compose.matrixTitle')}</title>
              <desc id="flow-matrix-description">{t('compose.matrixDescription')}</desc>
              <defs>
                {/* userSpaceOnUse keeps gradients visible on horizontal paths
                    whose bounding box would otherwise collapse to zero height. */}
                <linearGradient id="flow-edge-selected-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2={geometry?.width || 1000} y2="0">
                  <stop offset="0" stopColor="#8a5c22" />
                  <stop offset=".5" stopColor="#c99a63" />
                  <stop offset="1" stopColor="#8a5c22" />
                </linearGradient>
                <linearGradient id="flow-edge-focused-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2={geometry?.width || 1000} y2="0">
                  <stop offset="0" stopColor="#a9722f" />
                  <stop offset=".5" stopColor="#d4a574" />
                  <stop offset="1" stopColor="#a9722f" />
                </linearGradient>
              </defs>
              {previewEdge ? (
                <path className="flow-connection-preview" d={previewEdge.path} pointerEvents="none" />
              ) : null}
              {overviewEdges.map(edge => (
                <GraphEdge
                  key={edge.key}
                  path={edge.path}
                  focused={edge.key === focusedKey}
                  ready={readyKeys.has(edge.key)}
                  nodeHover={edgeTouchesHoveredNode(edge)
                    || edgeTouchesArmedNode(edge)
                    || edge.key === hoveredCellKey}
                />
              ))}
            </svg>
            {hoveredCell ? (
              <div className="flow-connection-tooltip" role="status">
                <strong>{hoveredCell.method}</strong>
                <span aria-hidden="true">→</span>
                <strong>{hoveredCell.database}</strong>
                <em>
                  {hoveredFocused
                    ? t('compose.tooltipFocused')
                    : hoveredSelected
                      ? t('compose.tooltipFocus')
                      : t('compose.tooltipConnect')}
                </em>
              </div>
            ) : null}
          </div>
          <ol className="flow-graph-nodes flow-database-nodes" ref={databaseListRef}>
            {DATABASES.map(database => <li key={database}>
              <button
                type="button"
                aria-label={nodeLabel('database', database)}
                aria-pressed={selectedDatabases.includes(database)}
                className={[
                  selectedDatabases.includes(database) ? 'selected' : '',
                  focusedDatabase === database ? 'focused' : '',
                  nodeTouchesHoveredEdge(database) ? 'edge-peer' : '',
                  nodeLinkedToHoveredNode(database, 'database') ? 'peer-linked' : '',
                  ...nodeWiringClasses('database', database),
                ].filter(Boolean).join(' ')}
                onClick={() => handleNodeActivate('database', database)}
                onMouseEnter={() => setHoveredNode({ side: 'database', name: database })}
                onMouseLeave={() => setHoveredNode(null)}
                onFocus={() => setHoveredNode({ side: 'database', name: database })}
                onBlur={() => setHoveredNode(null)}
              >
                {database}
              </button>
            </li>)}
          </ol>
        </div>
        <ConnectionGrid
          connections={connections}
          focusedKey={focusedKey}
          readyKeys={readyKeys}
          hoveredMethod={hoveredMethod}
          hoveredDatabase={hoveredDatabase}
          lastConnection={connections.length === 1}
          t={t}
          onActivate={inspectConnection}
          onRemove={removeConnection}
          onHoverCell={handleCellHover}
        />
        {undoEntry ? (
          <p className="compose-undo" data-testid="compose-undo">
            <span>
              {t('compose.undoPrompt', {
                method: undoEntry.method,
                database: undoEntry.database,
              })}
            </span>
            <button
              type="button"
              aria-label={t('compose.undoRemoval', {
                method: undoEntry.method,
                database: undoEntry.database,
              })}
              onClick={undoRemoval}
            >
              {t('compose.undoAction')}
            </button>
          </p>
        ) : null}
        <p
          className="compose-wire-hint"
          role="status"
          data-testid="compose-wire-hint"
          data-armed={armed ? 'true' : 'false'}
        >
          {wireHint}
        </p>
        <div className="connection-graph-legend">
          <span className="legend-item is-selected">{t('compose.legendSelected')}</span>
          <span className="legend-item is-focused">{t('compose.legendFocused')}</span>
          <span className="legend-item is-browsable">{t('compose.legendBrowsable')}</span>
          <p>{t('compose.graphTip')}</p>
        </div>
        <p className="compose-live-region" aria-live="polite" data-testid="compose-live-region">
          {liveMessage}
        </p>
      </div>

      <aside className="compose-workflow-panel" data-testid="compose-workflow-panel">
        <ConnectionSwitcher
          connections={connections}
          focusedKey={focusedKey}
          readyKeys={readyKeys}
          focusedIndex={focusedIndex}
          t={t}
          onFocusConnection={focusConnection}
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
    </div>
  </section>
}
