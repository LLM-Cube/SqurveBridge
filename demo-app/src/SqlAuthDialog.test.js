import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from './testDom.js'

const closeDom = installTestDom()
const { cleanup, render, screen } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
const unregister = register()

async function loadDialog() {
  try {
    return (await import('./SqlAuthDialog.jsx')).default
  } catch (error) {
    assert.fail(`SQL auth dialog must load: ${error.message}`)
  }
}

const providers = [
  {
    id: 'qwen',
    models: ['qwen-plus', 'qwen-max'],
    default_model: 'qwen-plus',
    default_endpoint_id: 'china',
    endpoints: [
      { id: 'china', label: 'China (Beijing)' },
      { id: 'international', label: 'International (Singapore)' },
      { id: 'china_workspace', label: 'China (Beijing) · workspace', requires_workspace: true },
    ],
  },
  { id: 'deepseek', models: ['deepseek-chat'], default_model: 'deepseek-chat' },
]

test.afterEach(() => cleanup())
test.after(() => {
  unregister()
  closeDom()
})

test('tests and saves a masked SQL credential without retaining it', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  const statuses = []
  const api = async (path, options = {}) => {
    calls.push({ path, options })
    if (options.method === 'POST') return { status: 'ok', validated: true, provider: 'deepseek', model: 'deepseek-chat' }
    if (options.method === 'PUT') return { status: 'ok', configured: true, provider: 'deepseek', model: 'deepseek-chat', providers }
    return { status: 'ok', configured: false, providers }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: value => statuses.push(value),
    onClose: () => {},
  }))

  await user.selectOptions(screen.getByLabelText('Provider'), 'deepseek')
  const keyInput = screen.getByLabelText('API key')
  assert.equal(keyInput.type, 'password')
  await user.type(keyInput, 'sql-secret')

  await user.click(screen.getByRole('button', { name: 'Test connection' }))
  assert.equal(calls.at(-1).path, '/api/sql-auth/test')
  assert.equal(keyInput.value, 'sql-secret')

  await user.click(screen.getByRole('button', { name: 'Save' }))
  assert.equal(calls.at(-1).path, '/api/sql-auth')
  assert.equal(calls.at(-1).options.method, 'PUT')
  assert.equal(keyInput.value, '')
  assert.equal(statuses.at(-1).configured, true)
  assert.equal(document.body.textContent.includes('sql-secret'), false)
})

test('disconnects and clears transient input when the dialog closes', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  let closed = false
  const api = async (path, options = {}) => {
    calls.push({ path, options })
    return { status: 'ok', configured: false, providers }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: true, provider: 'qwen', model: 'qwen-plus', providers },
    onStatusChange: () => {},
    onClose: () => { closed = true },
  }))

  await user.type(screen.getByLabelText('API key'), 'temporary-secret')
  await user.click(screen.getByRole('button', { name: 'Disconnect' }))
  assert.equal(calls.at(-1).options.method, 'DELETE')
  await user.type(screen.getByLabelText('API key'), 'close-secret')
  await user.click(screen.getAllByRole('button', { name: 'Close' }).at(-1))
  assert.equal(closed, true)
  assert.equal(screen.getByLabelText('API key').value, '')
})

test('renders a sanitized API error without clearing a key that was not saved', async () => {
  const SqlAuthDialog = await loadDialog()
  const api = async () => { throw new Error('The qwen credential was rejected.') }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: () => {},
    onClose: () => {},
  }))

  await user.type(screen.getByLabelText('API key'), 'rejected-secret')
  await user.click(screen.getByRole('button', { name: 'Test connection' }))
  assert.match(screen.getByRole('alert').textContent, /credential was rejected/i)
  assert.equal(screen.getByLabelText('API key').value, 'rejected-secret')
  assert.equal(document.body.textContent.includes('rejected-secret'), false)
})

test('accepts a custom SQL model id while keeping catalog suggestions', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  const api = async (path, options = {}) => {
    calls.push({ path, options })
    const payload = JSON.parse(options.body)
    return { status: 'ok', validated: true, provider: payload.provider, model: payload.model }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: () => {},
    onClose: () => {},
  }))

  const modelInput = screen.getByLabelText('Model')
  assert.equal(modelInput.tagName, 'INPUT')
  assert.ok(screen.getByRole('list', { name: 'Suggested models' }))
  assert.ok(screen.getByRole('button', { name: 'qwen-plus' }))
  await user.clear(modelInput)
  await user.type(modelInput, 'qwen-custom-latest')
  await user.type(screen.getByLabelText('API key'), 'custom-model-secret')
  await user.click(screen.getByRole('button', { name: 'Test connection' }))

  assert.equal(JSON.parse(calls.at(-1).options.body).model, 'qwen-custom-latest')
})

test('sends only an allowlisted Qwen API region identifier', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  const api = async (path, options = {}) => {
    calls.push({ path, options })
    return { status: 'ok', validated: true, provider: 'qwen', model: 'qwen-plus' }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: () => {},
    onClose: () => {},
  }))

  await user.selectOptions(screen.getByLabelText('API region'), 'international')
  await user.type(screen.getByLabelText('API key'), 'regional-test-key')
  await user.click(screen.getByRole('button', { name: 'Test connection' }))

  const payload = JSON.parse(calls.at(-1).options.body)
  assert.equal(payload.endpoint_id, 'international')
  assert.equal(Object.hasOwn(payload, 'base_url'), false)
})

test('sends a workspace id instead of accepting a custom Qwen base URL', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  const api = async (path, options = {}) => {
    calls.push({ path, options })
    return { status: 'ok', validated: true, provider: 'qwen', model: 'qwen-plus' }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: () => {},
    onClose: () => {},
  }))

  await user.selectOptions(screen.getByLabelText('API region'), 'china_workspace')
  await user.type(screen.getByLabelText('Workspace ID'), 'llm-valid-123')
  await user.type(screen.getByLabelText('API key'), 'workspace-test-key')
  await user.click(screen.getByRole('button', { name: 'Test connection' }))

  const payload = JSON.parse(calls.at(-1).options.body)
  assert.equal(payload.workspace_id, 'llm-valid-123')
  assert.equal(Object.hasOwn(payload, 'base_url'), false)
})

test('refreshes the live model list, labels its source, and keeps a typed model', async () => {
  const SqlAuthDialog = await loadDialog()
  const calls = []
  const api = async (path, options = {}) => {
    calls.push({ path, body: JSON.parse(options.body) })
    return { status: 'ok', models: ['deepseek-flash', 'deepseek-v4-pro'], models_source: 'live', models_error: null }
  }
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers },
    onStatusChange: () => {},
    onClose: () => {},
  }))

  await user.selectOptions(screen.getByLabelText('Provider'), 'deepseek')
  assert.match(document.body.textContent, /Built-in list/)
  await user.clear(screen.getByLabelText('Model'))
  await user.type(screen.getByLabelText('Model'), 'my-private-alias')
  await user.type(screen.getByLabelText('API key'), 'listing-secret')
  await user.click(screen.getByRole('button', { name: 'Refresh models' }))

  assert.equal(calls[0].path, '/api/llm/models')
  assert.equal(calls[0].body.provider, 'deepseek')
  assert.equal(calls[0].body.api_key, 'listing-secret')
  assert.match(document.body.textContent, /Live list from provider/)
  assert.ok(screen.getByRole('button', { name: 'deepseek-v4-pro' }))
  assert.equal(screen.getByLabelText('Model').value, 'my-private-alias')
})

test('shows why the live list fell back and an empty-state hint without any model', async () => {
  const SqlAuthDialog = await loadDialog()
  const api = async () => ({ status: 'ok', models: [], models_source: 'fallback', models_error: 'credential_rejected' })
  const user = userEvent.setup()
  render(React.createElement(SqlAuthDialog, {
    open: true,
    api,
    status: { configured: false, providers: [{ id: 'unlisted', models: [], default_model: '' }] },
    onStatusChange: () => {},
    onClose: () => {},
  }))
  await user.click(screen.getByRole('button', { name: 'Refresh models' }))
  assert.match(document.body.textContent, /credential_rejected/)
  assert.match(document.body.textContent, /No models listed/)
})
