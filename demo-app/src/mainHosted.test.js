import test from 'node:test'
import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { register } from 'tsx/esm/api'
import { installTestDom } from './testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render, screen, waitFor, within } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('./cssTestLoader.mjs', import.meta.url)
const unregister = register()
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let HostedApp

test.afterEach(() => cleanup())
test.after(() => {
  unregister()
  closeDom()
})

test('hosted App exposes session SQL configuration instead of local env configuration', async () => {
  const providers = [{ id: 'qwen', models: ['qwen-plus'], default_model: 'qwen-plus' }]
  const matrix = JSON.parse(fs.readFileSync(path.join(root, 'config/reproduce_matrix.json'), 'utf8'))
  const configs = matrix.databases.flatMap(database => matrix.methods.map(method => {
    const configPath = path.join(root, 'reproduce/configs', database.directory, `${method}.json`)
    assert.ok(fs.existsSync(configPath), configPath)
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    return {
      method,
      dataset: database.directory,
      split: database.split,
      config_path: path.relative(root, configPath),
      stages: config.task.task_meta.map(task => ({
        id: task.task_id,
        type: task.task_type,
        actor: Object.values(task.meta.task)[0],
      })),
    }
  }))
  const datasets = matrix.databases.map(database => database.directory)
  const responses = {
    '/api/health': { status: 'ok', provider: { configured: false, ready: false } },
    '/api/capabilities': {
      llm_providers: providers,
      actors: {},
      workflows: [[]],
      reproduce_configs: configs,
      deployment: {
        target: 'hf-space',
        features: {
          live_sql: true,
          session_sql_auth: true,
          provider_configuration: false,
          agent_chat: false,
          live_evaluation: false,
        },
      },
    },
    '/api/databases': { databases: datasets.map(id => ({ id, tables: [] })) },
    '/api/sql-auth': { status: 'ok', configured: false, providers },
    '/api/comparisons/latest/results': { runs: [] },
    '/api/archive': { runs: [] },
  }
  const requested = []
  responses['/api/agent'] = { available: true, profile: 'hosted-readonly', skills: [] }
  globalThis.fetch = async path => {
    requested.push(path)
    const key = String(path).split('?')[0]
    return {
      ok: true,
      statusText: 'OK',
      json: async () => (
        path.startsWith('/api/comparisons/latest/results')
          ? responses['/api/comparisons/latest/results']
          : responses[key] || responses[path] || {}
      ),
    }
  }
  let appElement
  globalThis.__SQURVE_DEMO_ROOT__ = { render: element => { appElement = element } }
  await import(`./main.jsx?hosted-test=${Date.now()}`)
  HostedApp = appElement.type
  render(React.createElement(appElement.type))

  const configure = await screen.findByRole('button', { name: 'Configure LLM' })
  assert.equal(screen.queryByRole('dialog', { name: 'Connect a model' }), null)
  assert.match(document.body.textContent, /Bring your own SQL model credential/i)
  assert.deepEqual(
    [...document.querySelectorAll('.flow-module h2')].map(heading => heading.textContent),
    [
      'Methods & Databases',
      'Workflow Composition',
      'Experiment Board',
      'Compare & History',
      'Interactive Query Workspace',
    ],
  )
  assert.match(document.body.textContent, /64 canonical configurations/i)
  assert.match(document.body.textContent, /Method × Database/i)
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  assert.ok(within(tabs).getByRole('button', { name: 'Experiment' }))
  // The experiment page is mounted but hidden until its tab is opened.
  const sections = screen.getByRole('navigation', { name: 'Experiment steps', hidden: true })
  assert.ok(within(sections).getByRole('button', { name: /Run/, hidden: true }))
  assert.ok(within(sections).getByRole('button', { name: /Compare & History/, hidden: true }))
  await waitFor(() => {
    // History mounts the archive catalog; charts load on demand when expanded.
    assert.ok(requested.some(path => String(path).startsWith('/api/archive')))
  })
  await userEvent.setup().click(configure)
  const dialog = screen.getByRole('dialog', { name: 'Configure LLM' })
  assert.ok(within(dialog).getByRole('combobox', { name: 'Provider' }))
  assert.ok(within(dialog).getByRole('list', { name: 'Suggested models' }))
  assert.match(dialog.textContent, /current browser session/i)
  assert.equal(within(dialog).queryByText(/Search providers/i), null)
  assert.equal(within(dialog).queryByText(/Write to repo-root \.env/i), null)
})

test('local App renders the same full-flow surface as the hosted deployment', async () => {
  const responses = {
    '/api/health': {
      status: 'ok',
      provider: {
        configured: true,
        ready: true,
        verified: true,
        provider: 'qwen',
        model: 'qwen-plus',
      },
    },
    '/api/capabilities': {
      llm_providers: [{
        id: 'qwen',
        configured: true,
        models: ['qwen-turbo', 'qwen-plus', 'qwen-max', 'deepseek-v4-flash'],
        default_model: 'qwen-plus',
      }],
      actors: {},
      workflows: [[]],
      reproduce_configs: [],
      deployment: {
        target: 'local',
        features: {
          live_sql: true,
          session_sql_auth: false,
          provider_configuration: true,
          agent_chat: true,
          live_evaluation: true,
        },
      },
    },
    '/api/databases': { databases: [] },
    '/api/comparisons/latest/results': { runs: [] },
    '/api/archive': { runs: [] },
  }
  globalThis.fetch = async path => ({
    ok: true,
    statusText: 'OK',
    json: async () => path.startsWith('/api/comparisons/latest/results')
      ? responses['/api/comparisons/latest/results']
      : responses[path] || {},
  })

  render(React.createElement(HostedApp))

  assert.equal((await screen.findByRole('heading', { level: 2, name: 'Methods & Databases' })).textContent, 'Methods & Databases')
  await userEvent.setup().click(screen.getByRole('button', { name: 'Configure LLM' }))
  assert.ok(screen.getByRole('dialog', { name: 'Configure LLM' }))
  assert.ok(document.querySelector('.flow-provider-dialog'))
  assert.ok(screen.getByRole('list', { name: 'Suggested models' }))
  assert.ok(screen.getByRole('button', { name: 'qwen-plus' }))
  assert.equal(screen.getByRole('button', { name: 'qwen-plus' }).getAttribute('aria-pressed'), 'true')
  assert.doesNotMatch(document.body.textContent, /Hugging Face Live Demo/i)
  assert.ok(within(screen.getByRole('navigation', { name: 'Workflow stages' })).getByRole('button', { name: 'Experiment' }))
  assert.ok(document.querySelector('.flow-demo'))
})

test('does not expose the local console while capabilities are pending', async () => {
  let resolveFetch
  globalThis.fetch = () => new Promise(resolve => { resolveFetch = resolve })
  render(React.createElement(HostedApp))
  assert.match(document.body.textContent, /Loading demo/)
  assert.equal(screen.queryByText('Experiment Board'), null)
  assert.equal(screen.queryByText('Archive'), null)
  resolveFetch?.({ ok: false, statusText: 'offline', json: async () => ({}) })
})

test('shows a neutral boot error instead of falling back to the local console', async () => {
  globalThis.fetch = async () => { throw new Error('offline') }
  render(React.createElement(HostedApp))
  assert.ok(await screen.findByRole('alert'))
  assert.match(document.body.textContent, /could not load its deployment configuration/i)
  assert.equal(screen.queryByText('Experiment Board'), null)
  assert.equal(screen.queryByText('Archive'), null)
})

test('localizes the pre-capabilities failure for a Chinese session', async () => {
  window.localStorage.setItem('squrve-demo-locale', 'zh-CN')
  globalThis.fetch = async () => { throw new Error('offline') }
  render(React.createElement(HostedApp))
  assert.ok(await screen.findByRole('alert'))
  assert.match(document.body.textContent, /无法加载演示部署配置/)
  assert.equal(screen.queryByText('Experiment Board'), null)
})
