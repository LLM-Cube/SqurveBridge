/** Pure helpers for the interactive Query workspace. */

import { DATABASES, configKey } from './model.js'

const SQL_KEYWORDS = new Set([
  'select', 'from', 'where', 'group', 'order', 'by', 'having', 'join', 'inner',
  'left', 'right', 'outer', 'cross', 'on', 'as', 'and', 'or', 'not', 'in',
  'exists', 'limit', 'offset', 'distinct', 'count', 'avg', 'sum', 'min', 'max',
  'union', 'all', 'case', 'when', 'then', 'else', 'end', 'asc', 'desc', 'like',
  'between', 'is', 'null', 'cast', 'with', 'intersect', 'except', 'using',
])

const stripSqlNoise = sql => String(sql || '')
  .replace(/--[^\n]*/g, ' ')
  .replace(/\/\*[\s\S]*?\*\//g, ' ')
  .replace(/'(?:[^']|'')*'/g, ' ')

const IDENTIFIER = /"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*)/g
const QUALIFIED = /(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))\s*\.\s*(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))/g

export function extractSqlIdentifiers(sql) {
  const source = stripSqlNoise(sql)
  const identifiers = new Set()
  const qualified = new Set()
  for (const match of source.matchAll(IDENTIFIER)) {
    const name = (match[1] || match[2] || match[3] || match[4] || '').toLowerCase()
    if (name && !SQL_KEYWORDS.has(name)) identifiers.add(name)
  }
  for (const match of source.matchAll(QUALIFIED)) {
    const owner = (match[1] || match[2] || match[3] || match[4] || '').toLowerCase()
    const member = (match[5] || match[6] || match[7] || match[8] || '').toLowerCase()
    if (owner && member) qualified.add(`${owner}.${member}`)
  }
  return { identifiers, qualified }
}

/**
 * Map identifiers referenced by the SQL onto the schema tree.
 * A column counts as hit only when its own table is referenced (or the
 * reference is table-qualified) to avoid cross-table name noise.
 */
export function computeSchemaHits(tables, sql) {
  const empty = { tables: new Set(), columns: new Set(), tableCount: 0, columnCount: 0 }
  if (!sql || !Array.isArray(tables) || tables.length === 0) return empty
  const { identifiers, qualified } = extractSqlIdentifiers(sql)
  if (identifiers.size === 0) return empty
  const hitTables = new Set()
  const hitColumns = new Set()
  for (const table of tables) {
    const tableName = String(table?.name || '')
    const tableKey = tableName.toLowerCase()
    const tableHit = identifiers.has(tableKey)
    if (tableHit) hitTables.add(tableName)
    for (const column of table?.columns || []) {
      const columnName = String(column?.name || '')
      const columnKey = columnName.toLowerCase()
      const qualifiedHit = qualified.has(`${tableKey}.${columnKey}`)
      if (qualifiedHit || (tableHit && identifiers.has(columnKey))) {
        hitColumns.add(`${tableName}::${columnName}`)
        hitTables.add(tableName)
      }
    }
  }
  return {
    tables: hitTables,
    columns: hitColumns,
    tableCount: hitTables.size,
    columnCount: hitColumns.size,
  }
}

export function buildCsv(columns, rows) {
  const escape = value => {
    if (value == null) return ''
    const text = String(value)
    return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
  }
  return [columns || [], ...(rows || [])]
    .map(row => row.map(escape).join(','))
    .join('\r\n')
}

export function csvFileName(dbId, now = new Date()) {
  const pad = value => String(value).padStart(2, '0')
  const stamp = [
    now.getFullYear(), pad(now.getMonth() + 1), pad(now.getDate()),
  ].join('') + '-' + [pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds())].join('')
  const safe = String(dbId || 'result').replace(/[^A-Za-z0-9_-]+/g, '_')
  return `${safe}-${stamp}.csv`
}

export function plannedQueryStages({ mode, generator, actors }) {
  const names = mode === 'workflow' && Array.isArray(actors) && actors.length
    ? actors
    : [generator || 'Generator']
  return names.map((actor, index) => ({ id: `plan-${index}-${actor}`, actor, stage: '' }))
}

export function stagesFromTrace(trace) {
  if (!Array.isArray(trace)) return []
  return trace.map((record, index) => ({
    id: `trace-${index}-${record?.actor_name || record?.stage || 'stage'}`,
    actor: record?.actor_name || record?.stage || `Stage ${index + 1}`,
    stage: record?.stage || '',
    status: record?.status === 'failed' ? 'failed' : 'done',
    elapsedMs: typeof record?.elapsed_ms === 'number' ? record.elapsed_ms : null,
  }))
}

export function formatElapsedMs(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return ''
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} s`
  return `${Math.round(value)} ms`
}

/** Split chat content into text and ```sql fenced segments. */
export function extractSqlSegments(content) {
  const source = String(content || '')
  const segments = []
  const fence = /```sql[^\n]*\n([\s\S]*?)(```|$)/gi
  let cursor = 0
  for (const match of source.matchAll(fence)) {
    if (match.index > cursor) segments.push({ type: 'text', text: source.slice(cursor, match.index) })
    segments.push({
      type: 'sql',
      sql: match[1].replace(/\s+$/, ''),
      closed: match[2] === '```',
    })
    cursor = match.index + match[0].length
  }
  if (cursor < source.length) segments.push({ type: 'text', text: source.slice(cursor) })
  return segments.length ? segments : [{ type: 'text', text: source }]
}

export function buildPiAnalysisPrompt({ question, dbId, sql, stages = [], error = '', rowCount = null }) {
  const stageLines = stages
    .filter(stage => stage.actor)
    .map(stage => `- ${stage.actor}${stage.stage ? ` (${stage.stage})` : ''}: ${stage.status}${stage.elapsedMs != null ? ` · ${formatElapsedMs(stage.elapsedMs)}` : ''}`)
  const outcome = error
    ? `Execution failed: ${error}`
    : rowCount != null
      ? `Execution returned ${rowCount} rows.`
      : 'The SQL has not been executed yet.'
  return [
    'Analyze this SqurveBridge interactive query attempt.',
    `Database: ${dbId || 'unknown'}`,
    `Question: ${question || '(none)'}`,
    'Generated SQL:',
    '```sql',
    sql || '(empty)',
    '```',
    stageLines.length ? `Pipeline stages:\n${stageLines.join('\n')}` : '',
    outcome,
    'Explain the most likely weak stage (schema linking, generation, or execution) and propose a corrected SQL if needed.',
  ].filter(Boolean).join('\n')
}

const HISTORY_KEY = 'squrve-query-history'
const HISTORY_LIMIT = 10

const safeStorage = storage => storage || (typeof window !== 'undefined' ? window.localStorage : null)

export function loadQuestionHistory(storage) {
  try {
    const raw = safeStorage(storage)?.getItem(HISTORY_KEY)
    const parsed = JSON.parse(raw || '[]')
    return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string').slice(0, HISTORY_LIMIT) : []
  } catch {
    return []
  }
}

export function pushQuestionHistory(question, storage) {
  const normalized = String(question || '').trim()
  const history = loadQuestionHistory(storage)
  if (!normalized) return history
  const next = [normalized, ...history.filter(item => item !== normalized)].slice(0, HISTORY_LIMIT)
  try {
    safeStorage(storage)?.setItem(HISTORY_KEY, JSON.stringify(next))
  } catch {
    // Storage may be unavailable (private mode); history stays in memory.
  }
  return next
}

/** Shape the schema tree for @codemirror/lang-sql completion. */
export function schemaToCompletion(tables) {
  const completion = {}
  for (const table of tables || []) {
    if (!table?.name) continue
    completion[table.name] = (table.columns || []).map(column => column.name).filter(Boolean)
  }
  return completion
}

export function exampleQuestions(table, t) {
  if (!table?.name) return []
  const examples = [
    t('query.exampleCount', { table: table.name }),
    t('query.exampleList', { table: table.name }),
  ]
  const column = (table.columns || []).find(item => item?.name)
  if (column) examples.push(t('query.exampleDistinct', { table: table.name, column: column.name }))
  return examples
}

const benchmarkKey = id => configKey('x', id)

/**
 * Databases grouped by the benchmark they belong to, in Compose's benchmark
 * order and with Compose's names; anything else (uploads) comes last.
 * Database names drop the `<benchmark>__` prefix some ids carry.
 */
export function databaseGroups(databases) {
  const byBenchmark = new Map()
  for (const database of databases || []) {
    const id = database.benchmark || ''
    if (!byBenchmark.has(id)) byBenchmark.set(id, [])
    byBenchmark.get(id).push({
      id: database.id,
      name: id && String(database.id).startsWith(`${id}__`) ? String(database.id).slice(id.length + 2) : database.id,
    })
  }
  const rank = id => {
    const index = DATABASES.findIndex(name => benchmarkKey(name) === benchmarkKey(id))
    return index < 0 ? DATABASES.length : index
  }
  return [...byBenchmark.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([id, items]) => ({
      id,
      label: DATABASES.find(name => benchmarkKey(name) === benchmarkKey(id)) || id || null,
      databases: items.sort((a, b) => a.name.localeCompare(b.name)),
    }))
}

/** The group a database belongs to, else the one matching Compose's focus, else the first. */
export function initialBenchmark(groups, selectedDb, focusedDatabase) {
  const owner = groups.find(group => group.databases.some(item => item.id === selectedDb))
  if (owner) return owner.id
  const focused = groups.find(group => group.id && benchmarkKey(group.id) === benchmarkKey(focusedDatabase))
  return (focused || groups[0])?.id ?? ''
}
