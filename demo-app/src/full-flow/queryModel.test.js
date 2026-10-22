import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildCsv,
  buildPiAnalysisPrompt,
  computeSchemaHits,
  csvFileName,
  extractSqlIdentifiers,
  extractSqlSegments,
  formatElapsedMs,
  loadQuestionHistory,
  plannedQueryStages,
  pushQuestionHistory,
  schemaToCompletion,
  stagesFromTrace,
  databaseGroups,
  initialBenchmark,
} from './queryModel.js'

const SCHEMA = [
  {
    name: 'singer',
    columns: [
      { name: 'singer_id', type: 'number', primary_key: true },
      { name: 'name', type: 'text' },
      { name: 'country', type: 'text' },
    ],
  },
  {
    name: 'concert',
    columns: [
      { name: 'concert_id', type: 'number', primary_key: true },
      { name: 'name', type: 'text' },
    ],
  },
]

test('extractSqlIdentifiers skips keywords, comments, and string literals', () => {
  const { identifiers, qualified } = extractSqlIdentifiers(
    "SELECT s.name FROM singer AS s -- name comment\nWHERE s.country = 'France' /* concert */",
  )
  assert.ok(identifiers.has('singer'))
  assert.ok(identifiers.has('name'))
  assert.ok(identifiers.has('country'))
  assert.ok(!identifiers.has('select'))
  assert.ok(!identifiers.has('france'))
  assert.ok(!identifiers.has('concert'))
  assert.ok(qualified.has('s.name'))
})

test('computeSchemaHits marks referenced tables and their columns only', () => {
  const hits = computeSchemaHits(SCHEMA, 'SELECT name, country FROM singer')
  assert.deepEqual([...hits.tables], ['singer'])
  assert.ok(hits.columns.has('singer::name'))
  assert.ok(hits.columns.has('singer::country'))
  assert.ok(!hits.columns.has('concert::name'))
  assert.equal(hits.tableCount, 1)
  assert.equal(hits.columnCount, 2)
})

test('computeSchemaHits resolves table-qualified references without a bare table token', () => {
  const hits = computeSchemaHits(SCHEMA, 'SELECT concert.name FROM concert JOIN singer ON 1=1')
  assert.ok(hits.columns.has('concert::name'))
  assert.ok(hits.tables.has('concert'))
  assert.ok(hits.tables.has('singer'))
})

test('computeSchemaHits returns empty sets without sql or schema', () => {
  assert.equal(computeSchemaHits([], 'SELECT 1').tableCount, 0)
  assert.equal(computeSchemaHits(SCHEMA, '').columnCount, 0)
})

test('buildCsv escapes quotes, commas, and newlines', () => {
  const csv = buildCsv(['id', 'note'], [[1, 'plain'], [2, 'a,"b"\nc'], [3, null]])
  assert.equal(csv, 'id,note\r\n1,plain\r\n2,"a,""b""\nc"\r\n3,')
})

test('csvFileName sanitizes the database id and stamps the time', () => {
  const name = csvFileName('bird__formula_1', new Date(2026, 0, 2, 3, 4, 5))
  assert.equal(name, 'bird__formula_1-20260102-030405.csv')
  assert.equal(csvFileName('a b/c', new Date(2026, 0, 2, 3, 4, 5)), 'a_b_c-20260102-030405.csv')
})

test('plannedQueryStages lists workflow actors or the direct generator', () => {
  assert.deepEqual(
    plannedQueryStages({ mode: 'workflow', actors: ['P', 'G'] }).map(stage => stage.actor),
    ['P', 'G'],
  )
  assert.deepEqual(
    plannedQueryStages({ mode: 'direct', generator: 'DINSQLGenerator' }).map(stage => stage.actor),
    ['DINSQLGenerator'],
  )
})

test('stagesFromTrace maps the public trace into stage view models', () => {
  const stages = stagesFromTrace([
    { actor_name: 'C3SQLParser', stage: 'parse', status: 'completed', elapsed_ms: 812.5 },
    { actor_name: 'C3SQLGenerator', stage: 'generate', status: 'failed' },
  ])
  assert.equal(stages[0].actor, 'C3SQLParser')
  assert.equal(stages[0].status, 'done')
  assert.equal(stages[0].elapsedMs, 812.5)
  assert.equal(stages[1].status, 'failed')
  assert.equal(stages[1].elapsedMs, null)
})

test('formatElapsedMs renders milliseconds and seconds', () => {
  assert.equal(formatElapsedMs(812.5), '813 ms')
  assert.equal(formatElapsedMs(2450), '2.5 s')
  assert.equal(formatElapsedMs(undefined), '')
})

test('extractSqlSegments splits closed and streaming sql fences', () => {
  const closed = extractSqlSegments('Here you go:\n```sql\nSELECT 1\n```\nDone.')
  assert.deepEqual(closed.map(segment => segment.type), ['text', 'sql', 'text'])
  assert.equal(closed[1].sql, 'SELECT 1')
  assert.equal(closed[1].closed, true)

  const streaming = extractSqlSegments('Working…\n```sql\nSELECT name FROM')
  assert.equal(streaming.at(-1).type, 'sql')
  assert.equal(streaming.at(-1).closed, false)

  const plain = extractSqlSegments('no sql here')
  assert.deepEqual(plain, [{ type: 'text', text: 'no sql here' }])
})

test('buildPiAnalysisPrompt carries question, sql, stages, and outcome', () => {
  const prompt = buildPiAnalysisPrompt({
    question: 'How many singers?',
    dbId: 'concert_singer',
    sql: 'SELECT count(*) FROM singer',
    stages: [{ actor: 'DINSQLGenerator', stage: 'generate', status: 'done', elapsedMs: 1200 }],
    error: 'no such column: nope',
  })
  assert.match(prompt, /Database: concert_singer/)
  assert.match(prompt, /How many singers\?/)
  assert.match(prompt, /SELECT count\(\*\) FROM singer/)
  assert.match(prompt, /DINSQLGenerator \(generate\): done · 1\.2 s/)
  assert.match(prompt, /Execution failed: no such column: nope/)
})

test('question history dedupes, caps at ten, and survives broken storage', () => {
  const store = new Map()
  const storage = {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
  }
  pushQuestionHistory('first', storage)
  pushQuestionHistory('second', storage)
  pushQuestionHistory('first', storage)
  assert.deepEqual(loadQuestionHistory(storage), ['first', 'second'])
  for (let index = 0; index < 12; index += 1) pushQuestionHistory(`q${index}`, storage)
  assert.equal(loadQuestionHistory(storage).length, 10)

  const broken = { getItem: () => '{invalid json', setItem: () => { throw new Error('denied') } }
  assert.deepEqual(loadQuestionHistory(broken), [])
  assert.deepEqual(pushQuestionHistory('safe', broken), ['safe'])
})

test('schemaToCompletion shapes tables for lang-sql completion', () => {
  assert.deepEqual(schemaToCompletion(SCHEMA), {
    singer: ['singer_id', 'name', 'country'],
    concert: ['concert_id', 'name'],
  })
})

test('databases are grouped under their benchmark in Compose order and names', () => {
  const groups = databaseGroups([
    { id: 'concert_singer', benchmark: 'spider' },
    { id: 'my_upload' },
    { id: 'bird__formula_1', benchmark: 'bird' },
    { id: 'aan_1', benchmark: 'spider' },
    { id: 'ehr', benchmark: 'ehrsql-2024' },
  ])
  assert.deepEqual(groups.map(group => [group.id, group.label]), [
    ['spider', 'Spider'],
    ['bird', 'BIRD'],
    ['ehrsql-2024', 'EHRSQL-2024'],
    ['', null],
  ])
  assert.deepEqual(groups[0].databases.map(item => item.name), ['aan_1', 'concert_singer'])
  assert.deepEqual(groups[1].databases, [{ id: 'bird__formula_1', name: 'formula_1' }], 'the benchmark prefix is dropped from the name only')
})

test('the first benchmark shown owns the selected database, else follows Compose focus', () => {
  const groups = databaseGroups([
    { id: 'concert_singer', benchmark: 'spider' },
    { id: 'bird__formula_1', benchmark: 'bird' },
  ])
  assert.equal(initialBenchmark(groups, 'bird__formula_1', 'Spider'), 'bird')
  assert.equal(initialBenchmark(groups, '', 'BIRD'), 'bird')
  assert.equal(initialBenchmark(groups, '', 'Unknown'), 'spider')
  assert.equal(initialBenchmark([], '', 'Spider'), '')
})
