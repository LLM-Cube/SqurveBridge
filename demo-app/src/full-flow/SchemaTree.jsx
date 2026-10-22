import { useEffect, useMemo, useState } from 'react'
import { databaseGroups, defaultDatabase, filterDatabases, initialBenchmark } from './queryModel.js'

// Below this many databases a plain list is easy enough to scan.
const FILTER_THRESHOLD = 12

const matches = (value, term) => String(value || '').toLowerCase().includes(term)

function highlightText(text, term) {
  if (!term) return text
  const source = String(text)
  const index = source.toLowerCase().indexOf(term)
  if (index === -1) return source
  return (
    <>
      {source.slice(0, index)}
      <mark>{source.slice(index, index + term.length)}</mark>
      {source.slice(index + term.length)}
    </>
  )
}

/**
 * Collapsible database schema browser: database picker, table/column tree
 * with search, key badges, and SQL-reference highlights.
 */
export default function SchemaTree({
  databases = [],
  selectedDb = '',
  onSelectDb,
  focusedDatabase = '',
  schema = null,
  hits = null,
  hitsDismissed = false,
  onDismissHits,
  onInsert,
  collapsed = false,
  onToggleCollapsed,
  t,
}) {
  const [term, setTerm] = useState('')
  const [expanded, setExpanded] = useState(() => new Set())
  const normalizedTerm = term.trim().toLowerCase()
  const searching = normalizedTerm.length > 0

  // Pick a benchmark first, then one of its databases.
  const groups = useMemo(() => databaseGroups(databases), [databases])
  const [benchmark, setBenchmark] = useState(() => initialBenchmark(groups, selectedDb, focusedDatabase))
  const activeGroup = groups.find(group => group.id === benchmark) || null
  const [databaseTerm, setDatabaseTerm] = useState('')
  const listedDatabases = filterDatabases(activeGroup?.databases, databaseTerm, selectedDb)

  useEffect(() => {
    // Follow a database chosen elsewhere (a replay) and fill in once databases load.
    const owner = groups.find(group => group.databases.some(item => item.id === selectedDb))
    if (owner && owner.id !== benchmark) setBenchmark(owner.id)
    else if (!activeGroup && groups.length) setBenchmark(initialBenchmark(groups, selectedDb, focusedDatabase))
  }, [groups, selectedDb])

  // Switching benchmark opens its first database, so the schema always matches the picker.
  const chooseBenchmark = next => {
    setBenchmark(next)
    setDatabaseTerm('')
    const opening = defaultDatabase(groups.find(group => group.id === next))
    if (opening && opening !== selectedDb) onSelectDb?.(opening)
  }

  const tables = schema?.tables || []
  const visibleTables = useMemo(() => {
    if (!searching) return tables
    return tables
      .map(table => {
        const tableHit = matches(table.name, normalizedTerm)
        const columns = (table.columns || []).filter(column => matches(column.name, normalizedTerm))
        if (!tableHit && columns.length === 0) return null
        return { ...table, columns: tableHit ? table.columns : columns }
      })
      .filter(Boolean)
  }, [tables, normalizedTerm, searching])

  const showHits = Boolean(hits && hits.tableCount > 0 && !hitsDismissed)
  const isExpanded = table => searching || expanded.has(table.name)
  const toggleTable = table => {
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(table.name)) next.delete(table.name)
      else next.add(table.name)
      return next
    })
  }

  if (collapsed) {
    return (
      <aside className="query-schema is-collapsed">
        <button
          type="button"
          className="query-schema-toggle"
          aria-label={t('query.expandSchema')}
          title={t('query.expandSchema')}
          onClick={onToggleCollapsed}
        >
          <span aria-hidden="true">⌸</span>
          <b>{t('query.schemaEyebrow')}</b>
        </button>
      </aside>
    )
  }

  return (
    <aside className="query-schema" data-testid="query-schema">
      <header className="query-schema-head">
        <span>{t('query.schemaEyebrow')}</span>
        <button
          type="button"
          className="query-schema-toggle"
          aria-label={t('query.collapseSchema')}
          title={t('query.collapseSchema')}
          onClick={onToggleCollapsed}
        >
          ‹
        </button>
      </header>

      <label className="query-schema-database">
        <span>{t('query.benchmark')}</span>
        <select
          value={activeGroup?.id ?? ''}
          aria-label={t('query.benchmark')}
          onChange={event => chooseBenchmark(event.target.value)}
        >
          {groups.map(group => (
            <option key={group.id || 'other'} value={group.id}>
              {t('query.benchmarkOption', {
                name: group.label || t('query.otherDatabases'),
                count: group.databases.length,
              })}
            </option>
          ))}
        </select>
      </label>

      {(activeGroup?.databases.length || 0) > FILTER_THRESHOLD ? (
        <input
          type="search"
          className="query-schema-search"
          value={databaseTerm}
          placeholder={t('query.filterDatabases', { count: activeGroup.databases.length })}
          aria-label={t('query.filterDatabasesLabel')}
          onChange={event => setDatabaseTerm(event.target.value)}
        />
      ) : null}

      <label className="query-schema-database">
        <span>
          {t('query.database')}
          {databaseTerm.trim() ? ` · ${t('query.databaseMatches', { count: listedDatabases.length })}` : ''}
        </span>
        <select
          value={selectedDb}
          aria-label={t('query.database')}
          onChange={event => onSelectDb?.(event.target.value)}
        >
          <option value="">{t('query.selectDatabase')}</option>
          {listedDatabases.map(database => (
            <option key={database.id} value={database.id}>
              {database.name}
            </option>
          ))}
        </select>
      </label>

      {selectedDb && (
        <input
          type="search"
          className="query-schema-search"
          value={term}
          placeholder={t('query.schemaSearch')}
          aria-label={t('query.schemaSearch')}
          onChange={event => setTerm(event.target.value)}
        />
      )}

      {showHits && (
        <div className="query-schema-hits" data-testid="schema-hits">
          <span>
            {t('query.schemaHits', { tables: hits.tableCount, columns: hits.columnCount })}
          </span>
          <button type="button" onClick={onDismissHits}>{t('query.schemaHitsClear')}</button>
        </div>
      )}

      <div className="query-schema-body">
        {!selectedDb && <p className="query-schema-note">{t('query.schemaEmpty')}</p>}
        {selectedDb && schema?.status === 'loading' && (
          <p className="query-schema-note">{t('query.schemaLoading')}</p>
        )}
        {selectedDb && schema?.status === 'error' && (
          <p className="query-schema-note is-error">{t('query.schemaUnavailable')}</p>
        )}
        {selectedDb && schema?.status === 'ready' && searching && visibleTables.length === 0 && (
          <p className="query-schema-note">{t('query.noSchemaMatches', { term: term.trim() })}</p>
        )}
        {selectedDb && schema?.status === 'ready' && (
          <ul className="query-schema-tables">
            {visibleTables.map(table => {
              const tableHit = Boolean(hits?.tables?.has(table.name)) && !hitsDismissed
              const open = isExpanded(table)
              return (
                <li key={table.name} className={tableHit ? 'is-hit' : ''}>
                  <div className="query-schema-table-row">
                    <button
                      type="button"
                      className="query-schema-table"
                      aria-expanded={open}
                      onClick={() => toggleTable(table)}
                    >
                      <i aria-hidden="true">{open ? '▾' : '▸'}</i>
                      {tableHit && <em className="query-hit-dot" aria-hidden="true" />}
                      <b>{highlightText(table.name, normalizedTerm)}</b>
                      <small>{t('query.tableColumns', { count: (table.columns || []).length })}</small>
                    </button>
                    <button
                      type="button"
                      className="query-schema-insert"
                      aria-label={t('query.insertIdentifier', { name: table.name })}
                      onClick={() => onInsert?.(table.name)}
                    >
                      +
                    </button>
                  </div>
                  {open && (
                    <ul className="query-schema-columns">
                      {(table.columns || []).map(column => {
                        const columnHit = Boolean(hits?.columns?.has(`${table.name}::${column.name}`)) && !hitsDismissed
                        const foreign = column.foreign_key
                        return (
                          <li key={column.name} className={columnHit ? 'is-hit' : ''}>
                            <button
                              type="button"
                              className="query-schema-column"
                              aria-label={t('query.insertIdentifier', { name: column.name })}
                              title={foreign
                                ? t('query.foreignKey', { table: foreign.table, column: foreign.column })
                                : column.primary_key
                                  ? t('query.primaryKey')
                                  : column.type || undefined}
                              onClick={() => onInsert?.(column.name)}
                            >
                              {columnHit && <em className="query-hit-dot" aria-hidden="true" />}
                              <span>{highlightText(column.name, normalizedTerm)}</span>
                              {column.type && <code>{column.type}</code>}
                              {column.primary_key && <i className="query-key-badge" aria-label={t('query.primaryKey')}>PK</i>}
                              {foreign && <i className="query-key-badge is-fk" aria-label={t('query.foreignKey', { table: foreign.table, column: foreign.column })}>FK</i>}
                            </button>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </aside>
  )
}
