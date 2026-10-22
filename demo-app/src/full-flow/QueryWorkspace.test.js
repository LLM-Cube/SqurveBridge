import assert from 'node:assert/strict'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render, screen, waitFor, fireEvent } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()

const { default: QueryWorkspace } = await import('./QueryWorkspace.jsx')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)

function FakeEditor({ value, onChange, ariaLabel, placeholder, disabled }) {
  return React.createElement('textarea', {
    'data-testid': 'fake-editor',
    'aria-label': ariaLabel,
    value,
    placeholder,
    disabled,
    onChange: event => onChange?.(event.target.value),
  })
}

const SCHEMA_RESPONSE = {
  db_id: 'concert_singer',
  tables: [
    {
      name: 'stadium',
      columns: [
        { name: 'stadium_id', type: 'number', primary_key: true },
        { name: 'capacity', type: 'number' },
      ],
    },
    {
      name: 'singer',
      columns: [
        { name: 'singer_id', type: 'number', primary_key: true },
        { name: 'name', type: 'text' },
        {
          name: 'stadium_id',
          type: 'number',
          foreign_key: { table: 'stadium', column: 'stadium_id' },
        },
      ],
    },
  ],
}

const baseCapabilities = {
  actors: {
    parser: ['LinkAlignParser'],
    generator: ['DINSQLGenerator', 'C3SQLGenerator'],
  },
  workflows: [['generator'], ['parser', 'generator']],
}

function renderWorkspace(overrides = {}) {
  const api = overrides.api || (async path => {
    if (String(path).includes('/schema')) return SCHEMA_RESPONSE
    return {}
  })
  const props = {
    databases: [{ id: 'concert_singer', tables: ['stadium', 'singer'], size_bytes: 2048, benchmark: 'spider' }],
    capabilities: baseCapabilities,
    focusedConfig: null,
    focusedMethod: 'DINSQL',
    focusedDatabase: 'Spider',
    sqlAuth: { configured: true, provider: 'qwen', model: 'qwen-plus' },
    credentialMode: 'session',
    onConfigureSql: () => {},
    postJson: async () => ({}),
    api,
    onAskPi: () => {},
    t,
    Editor: FakeEditor,
    ...overrides,
  }
  return render(React.createElement(QueryWorkspace, props))
}

test.afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

test.after(() => {
  unregister()
  closeDom()
})

function selectedDatabase() {
  return screen.getByRole('listbox', { name: 'Database' }).querySelector('[aria-selected="true"]')?.dataset.id || ''
}

test('auto-selects a database and renders its schema tree with keys', async () => {
  renderWorkspace()
  assert.equal(selectedDatabase(), 'concert_singer')
  const singerToggle = await screen.findByRole('button', { name: /singer/, expanded: false })
  await userEvent.setup().click(singerToggle)
  assert.ok(screen.getByText('name'))
  assert.ok(screen.getAllByText('PK').length >= 1)
  assert.ok(screen.getByText('FK'))
})

test('runs the direct pipeline, fills the real trace, auto-executes, and highlights hits', async () => {
  const calls = []
  renderWorkspace({
    postJson: async (path, body) => {
      calls.push([path, body])
      if (path === '/api/query') {
        return {
          status: 'success',
          sql: 'SELECT name FROM singer',
          trace: [
            { actor_name: 'DINSQLGenerator', stage: 'generate', status: 'completed', elapsed_ms: 950 },
          ],
        }
      }
      if (path === '/api/execute') {
        return { status: 'success', columns: ['name'], rows: [['Adele'], [null]], row_count: 2, truncated: false, elapsed_ms: 14 }
      }
      return {}
    },
  })
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })

  await user.type(screen.getByLabelText('Natural-language question'), 'List all singer names')
  await user.click(screen.getByRole('button', { name: 'Run pipeline' }))

  await screen.findByTestId('query-stages')
  await waitFor(() => assert.match(screen.getByTestId('query-stages').textContent, /DINSQLGenerator/))
  await waitFor(() => assert.match(screen.getByTestId('query-stages').textContent, /950 ms/))
  assert.match(screen.getByTestId('query-stages').textContent, /Execute/)

  const queryCall = calls.find(([path]) => path === '/api/query')
  assert.deepEqual(queryCall[1], {
    question: 'List all singer names',
    db_id: 'concert_singer',
    mode: 'direct',
    generator: 'DINSQLGenerator',
  })
  const executeCall = calls.find(([path]) => path === '/api/execute')
  assert.deepEqual(executeCall[1], { db_id: 'concert_singer', sql: 'SELECT name FROM singer' })

  assert.equal(screen.getByTestId('fake-editor').value, 'SELECT name FROM singer')
  assert.match(screen.getByTestId('query-results').textContent, /2 rows · 14 ms/)
  assert.ok(screen.getByText('Adele'))
  assert.ok(screen.getByText('NULL'))
  assert.match(screen.getByTestId('schema-hits').textContent, /1 tables · 1 columns/)
})

test('inherits the Compose pipeline in workflow mode', async () => {
  const calls = []
  renderWorkspace({
    focusedConfig: {
      method: 'c3sql',
      dataset: 'spider',
      stages: [
        { id: 'reduce', type: 'ReduceTask', actor: 'C3SQLReducer' },
        { id: 'parse', type: 'ParseTask', actor: 'C3SQLParser' },
        { id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' },
      ],
    },
    focusedMethod: 'C3SQL',
    postJson: async (path, body) => {
      calls.push([path, body])
      if (path === '/api/query') return { status: 'success', sql: 'SELECT 1', trace: [] }
      return { status: 'success', columns: ['1'], rows: [[1]], row_count: 1, truncated: false, elapsed_ms: 3 }
    },
  })
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })

  const composeChip = screen.getByRole('button', { name: 'Use C3SQL × Spider pipeline' })
  assert.equal(composeChip.getAttribute('aria-pressed'), 'true')
  assert.match(screen.getByTestId('query-compose-actors').textContent, /C3SQLReducer/)

  await user.type(screen.getByLabelText('Natural-language question'), 'count rows')
  await user.click(screen.getByRole('button', { name: 'Run pipeline' }))

  await waitFor(() => assert.ok(calls.some(([path]) => path === '/api/query')))
  const [, body] = calls.find(([path]) => path === '/api/query')
  assert.equal(body.mode, 'workflow')
  assert.deepEqual(body.actors, ['C3SQLReducer', 'C3SQLParser', 'C3SQLGenerator'])
})

test('marks edited SQL and re-executes it against the live database', async () => {
  const executed = []
  renderWorkspace({
    postJson: async (path, body) => {
      if (path === '/api/query') {
        return { status: 'success', sql: 'SELECT name FROM singer', trace: [] }
      }
      executed.push(body.sql)
      return { status: 'success', columns: ['n'], rows: [[7]], row_count: 1, truncated: false, elapsed_ms: 5 }
    },
  })
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })
  await user.type(screen.getByLabelText('Natural-language question'), 'names')
  await user.click(screen.getByRole('button', { name: 'Run pipeline' }))
  await screen.findByTestId('query-sql-panel')

  fireEvent.change(screen.getByTestId('fake-editor'), {
    target: { value: 'SELECT count(*) FROM singer' },
  })
  assert.ok(screen.getByText('Edited'))

  await user.click(screen.getByRole('button', { name: 'Execute SQL' }))
  await waitFor(() => assert.equal(executed.at(-1), 'SELECT count(*) FROM singer'))

  await user.click(screen.getByRole('button', { name: 'Reset to generated SQL' }))
  assert.equal(screen.getByTestId('fake-editor').value, 'SELECT name FROM singer')
})

test('locks generation without a credential but keeps schema browsing available', async () => {
  let configureCalls = 0
  renderWorkspace({
    sqlAuth: { configured: false },
    onConfigureSql: () => { configureCalls += 1 },
  })
  assert.ok(screen.getByTestId('query-locked'))
  assert.equal(screen.getByLabelText('Natural-language question').disabled, true)
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Configure LLM' }))
  assert.equal(configureCalls, 1)
})

test('sends execution failures to Pi with full query context', async () => {
  const prompts = []
  renderWorkspace({
    postJson: async path => {
      if (path === '/api/query') {
        return { status: 'success', sql: 'SELECT nope FROM singer', trace: [] }
      }
      throw new Error('Execution failed: no such column: nope')
    },
    onAskPi: prompt => prompts.push(prompt),
  })
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })
  await user.type(screen.getByLabelText('Natural-language question'), 'broken question')
  await user.click(screen.getByRole('button', { name: 'Run pipeline' }))

  const alert = await screen.findByRole('alert')
  assert.match(alert.textContent, /no such column/)
  await user.click(screen.getAllByRole('button', { name: 'Ask Pi to analyze' }).at(-1))
  assert.equal(prompts.length, 1)
  assert.match(prompts[0], /broken question/)
  assert.match(prompts[0], /SELECT nope FROM singer/)
  assert.match(prompts[0], /no such column/)
})

test('adopts SQL handed over from the Pi panel', async () => {
  let handled = 0
  renderWorkspace({
    adoptedSql: { id: 7, sql: 'SELECT 42' },
    onAdoptedSqlHandled: () => { handled += 1 },
  })
  await screen.findByTestId('query-sql-panel')
  assert.equal(screen.getByTestId('fake-editor').value, 'SELECT 42')
  assert.equal(handled, 1)
})

test('shows the first-run guide and example questions fill in and auto-run the pipeline', async () => {
  const calls = []
  renderWorkspace({
    postJson: async (path, body) => {
      calls.push([path, body])
      if (path === '/api/query') {
        return { status: 'success', sql: 'SELECT count(*) FROM stadium', trace: [] }
      }
      return { status: 'success', columns: ['n'], rows: [[3]], row_count: 1, truncated: false, elapsed_ms: 2 }
    },
  })
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })
  const empty = screen.getByTestId('query-empty')
  assert.match(empty.textContent, /Ask your first question/)
  const example = screen.getByRole('button', { name: 'How many rows does stadium have?' })
  await userEvent.setup().click(example)
  assert.equal(
    screen.getByLabelText('Natural-language question').value,
    'How many rows does stadium have?',
  )
  await waitFor(() => assert.ok(calls.some(([path]) => path === '/api/query')))
  const [, body] = calls.find(([path]) => path === '/api/query')
  assert.equal(body.question, 'How many rows does stadium have?')
  await screen.findByTestId('query-results')
})

test('pressing Enter in the question box runs the pipeline; Shift+Enter adds a newline', async () => {
  const calls = []
  renderWorkspace({
    postJson: async (path, body) => {
      calls.push([path, body])
      if (path === '/api/query') return { status: 'success', sql: 'SELECT 1', trace: [] }
      return { status: 'success', columns: ['1'], rows: [[1]], row_count: 1, truncated: false, elapsed_ms: 1 }
    },
  })
  const user = userEvent.setup()
  await screen.findByRole('button', { name: 'Insert stadium into the SQL editor' })
  const textarea = screen.getByLabelText('Natural-language question')

  await user.type(textarea, 'line one{Shift>}{Enter}{/Shift}line two')
  assert.equal(textarea.value, 'line one\nline two')
  assert.equal(calls.length, 0)

  await user.type(textarea, '{Enter}')
  await waitFor(() => assert.ok(calls.some(([path]) => path === '/api/query')))
  const [, body] = calls.find(([path]) => path === '/api/query')
  assert.equal(body.question, 'line one\nline two')
})

test('a question replayed from the comparison runs with that method', async () => {
  const calls = []
  renderWorkspace({
    prefill: { id: 3, dbId: 'concert_singer', question: 'How many singers are there?', method: 'c3sql' },
    postJson: async (path, body) => {
      calls.push([path, body])
      if (path === '/api/query') return { status: 'success', sql: 'SELECT count(*) FROM singer', trace: [] }
      return { status: 'success', columns: ['n'], rows: [[6]], row_count: 1, truncated: false, elapsed_ms: 3 }
    },
  })
  assert.equal(selectedDatabase(), 'concert_singer')
  assert.equal(screen.getByLabelText('Natural-language question').value, 'How many singers are there?')
  await userEvent.setup().click(screen.getByRole('button', { name: 'Run pipeline' }))
  const query = calls.find(([path]) => path === '/api/query')
  assert.equal(query[1].method, 'c3sql')
  assert.equal(query[1].question, 'How many singers are there?')
})
