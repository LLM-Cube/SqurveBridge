import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from './testDom.js'
import { DATABASES, METHODS } from './full-flow/model.js'

const closeDom = installTestDom()
globalThis.React = React
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: window.localStorage,
})
const { act, cleanup, fireEvent, render, screen, waitFor, within } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('./cssTestLoader.mjs', import.meta.url)
const unregister = register()

const { default: FullFlowDemo } = await import('./MatrixStudio.jsx')
const fullFlowCss = readFileSync(
  new URL('./full-flow/full-flow.css', import.meta.url),
  'utf8',
)

const C3SQL_SPIDER = {
  method: 'c3sql',
  dataset: 'spider',
  config_path: 'reproduce/configs/spider/c3sql.json',
  stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
}

// Every method × benchmark has a config, as in the real catalogue, so the matrix
// offers all 64 cells. Explicit configs win over the generated placeholders.
const keyOf = config => `${config.method}/${config.dataset}`.toLowerCase().replace(/[_\s-]+/g, '')
function catalogWith(explicit = []) {
  const taken = new Set(explicit.map(keyOf))
  const generated = METHODS.flatMap(method => DATABASES.map(database => ({
    method: method.toLowerCase().replace(/ /g, '-'),
    dataset: database,
    config_path: `reproduce/configs/${database}/${method}.json`,
    stages: [],
  })))
  return [...explicit, ...generated.filter(config => !taken.has(keyOf(config)))]
}

const baseCapabilities = { reproduce_configs: catalogWith([C3SQL_SPIDER]) }

function renderDemo(locale = 'en-US', capabilities = baseCapabilities, overrides = {}) {
  localStorage.setItem('squrve-demo-locale', locale)
  window.history.replaceState(null, '', '#configure')
  const api = overrides.api || (async path => {
    if (String(path).startsWith('/api/agent')) {
      return { available: true, profile: 'hosted-readonly', skills: [] }
    }
    return { runs: [] }
  })
  return render(React.createElement(FullFlowDemo, {
    capabilities,
    databases: [],
    sqlAuth: { configured: false },
    api,
    postJson: async () => ({}),
    onConfigureSql: () => {},
    ...overrides,
  }))
}

const EXPERIMENT_SECTIONS = ['Compose', 'Run', 'Compare & History']

// Compose, Run and Compare & History are sections of the one Experiment page.
async function goToStep(user, name) {
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  if (!EXPERIMENT_SECTIONS.includes(name)) {
    await user.click(within(tabs).getByRole('button', { name }))
    return
  }
  await user.click(within(tabs).getByRole('button', { name: /^Experiment/ }))
  const sections = screen.getByRole('navigation', { name: 'Experiment steps' })
  await user.click(within(sections).getByRole('button', { name: new RegExp(name) }))
}

test.afterEach(() => {
  cleanup()
  localStorage.clear()
  window.history.replaceState(null, '', '#configure')
})

test.after(() => {
  unregister()
  closeDom()
})

test('renders five bilingual modules in process order', () => {
  renderDemo('en-US')
  assert.ok(document.querySelector('.flow-demo.agent-shell'))
  assert.equal(document.querySelector('.agent-icon-rail'), null)
  assert.ok(document.querySelector('.agent-chat-column'))
  assert.ok(document.querySelector('.agent-dashboard-pane'))
  assert.equal(document.querySelectorAll('.flow-module').length, 5)
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'configure')
  assert.deepEqual(
    [...document.querySelectorAll('.flow-module')].map(section => section.id),
    ['configure', 'compose', 'board', 'evidence', 'query'],
  )
  assert.equal(document.querySelector('#configure h2')?.textContent, 'Methods & Databases')
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  assert.deepEqual(
    within(tabs).getAllByRole('button').map(button => button.textContent),
    ['Studio', 'ExperimentDraft', 'Query'],
    'compose, run and compare & history are one experiment page',
  )
  assert.ok(document.querySelector('.agent-chat-body'))
  assert.ok(screen.getByTestId('pi-backend-badge'))
  assert.match(screen.getByTestId('pi-backend-badge').textContent, /Pi/)
})

test('collapses either pane without hiding both and persists the shell layout', async () => {
  renderDemo('en-US')
  const user = userEvent.setup()
  const shell = screen.getByTestId('agent-shell')

  await user.click(screen.getByRole('button', { name: 'Collapse SqurveBridge Agent' }))
  assert.ok(shell.classList.contains('agent-collapsed'))
  assert.equal(JSON.parse(localStorage.getItem('squrve-demo-shell-layout')).agentCollapsed, true)

  await user.click(screen.getByRole('button', { name: 'Expand SqurveBridge Agent' }))
  assert.equal(shell.classList.contains('agent-collapsed'), false)
  await user.click(screen.getByRole('button', { name: 'Collapse dashboard' }))
  assert.ok(shell.classList.contains('dashboard-collapsed'))
  assert.equal(shell.classList.contains('agent-collapsed'), false)
})

test('switches process pages from the stage tabs', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const stage = screen.getByTestId('flow-stage')
  assert.equal(stage.getAttribute('data-active-step'), 'experiment')
  assert.equal(stage.getAttribute('data-active-section'), 'compose')
  assert.equal(window.location.hash, '#compose')

  // Compose, Run and Compare & History read top to bottom on one page.
  const page = screen.getByTestId('experiment-page')
  assert.deepEqual(
    [...page.querySelectorAll('.experiment-section')].map(section => section.dataset.section),
    ['compose', 'board', 'evidence'],
  )
  assert.equal(document.querySelector('#compose h2')?.textContent, 'Workflow Composition')
  assert.equal(document.querySelector('#board h2')?.textContent, 'Experiment Board')

  await goToStep(user, 'Run')
  assert.equal(stage.getAttribute('data-active-section'), 'board')
  assert.equal(window.location.hash, '#board')

  await goToStep(user, 'Compare & History')
  assert.equal(stage.getAttribute('data-active-section'), 'evidence')
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  assert.equal(within(tabs).getByRole('button', { name: /^Experiment/ }).getAttribute('aria-current'), 'page')
  const sections = screen.getByRole('navigation', { name: 'Experiment steps' })
  assert.equal(
    within(sections).getByRole('button', { name: /Compare & History/ }).getAttribute('aria-current'),
    'step',
  )
})

test('maps legacy process hashes onto the four-step flow', () => {
  localStorage.setItem('squrve-demo-locale', 'en-US')
  window.history.replaceState(null, '', '#run')
  render(React.createElement(FullFlowDemo, {
    capabilities: baseCapabilities,
    databases: [],
    sqlAuth: { configured: false },
    api: async () => ({ runs: [] }),
    postJson: async () => ({}),
    onConfigureSql: () => {},
  }))
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-section'), 'board')
  cleanup()
  localStorage.clear()
  window.history.replaceState(null, '', '#inspect')
  render(React.createElement(FullFlowDemo, {
    capabilities: baseCapabilities,
    databases: [],
    sqlAuth: { configured: false },
    api: async () => ({ runs: [] }),
    postJson: async () => ({}),
    onConfigureSql: () => {},
  }))
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-section'), 'evidence')
  cleanup()
  localStorage.clear()
  window.history.replaceState(null, '', '#archive')
  render(React.createElement(FullFlowDemo, {
    capabilities: baseCapabilities,
    databases: [],
    sqlAuth: { configured: false },
    api: async () => ({ runs: [] }),
    postJson: async () => ({}),
    onConfigureSql: () => {},
  }))
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-section'), 'evidence')
  cleanup()
  localStorage.clear()
  window.history.replaceState(null, '', '#visualize')
  render(React.createElement(FullFlowDemo, {
    capabilities: baseCapabilities,
    databases: [],
    sqlAuth: { configured: false },
    api: async () => ({ runs: [] }),
    postJson: async () => ({}),
    onConfigureSql: () => {},
  }))
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-section'), 'evidence')
})

test('keeps functional controls and technical metadata readable', () => {
  assert.match(fullFlowCss, /--flow-type-control:\s*12px/)
  assert.match(fullFlowCss, /--flow-type-meta:\s*11px/)
  for (const selector of [
    '.flashcard-tile-open',
    '.flashcard-face section p',
    '.flow-graph-nodes button',
    '.flow-actor-workflow li > span',
    '.run-phase-list li',
    '.result-run-context dt',
  ]) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    assert.match(
      fullFlowCss,
      new RegExp(`${escaped}[^{]*\\{[^}]*var\\(--flow-type-(?:control|meta)\\)`, 's'),
      `${selector} must use the readable type scale`,
    )
  }
})

test('switches to Chinese and persists the locale', async () => {
  renderDemo('en-US')
  await userEvent.setup().click(screen.getByRole('button', { name: '切换到中文' }))
  assert.equal(document.querySelector('#configure h2')?.textContent, '方法与数据库')
  assert.equal(localStorage.getItem('squrve-demo-locale'), 'zh-CN')
  assert.equal(document.documentElement.lang, 'zh-CN')
})

test('keeps Studio focused on explaining methods and databases', async () => {
  renderDemo()
  const user = userEvent.setup()

  assert.equal(screen.queryByTestId('configure-agent-panel'), null)
  assert.match(document.querySelector('#configure').textContent, /Studio|工作室/)
  assert.match(screen.getByTestId('studio-guide').textContent, /Browse, then open|先浏览/)
  assert.ok(document.querySelector('.agent-chat-column'))
  assert.match(screen.getByRole('heading', { level: 2, name: 'SqurveBridge Agent' }).textContent, /SqurveBridge Agent/)
  assert.equal(document.querySelector('#configure [data-testid="actor-workflow"]'), null)
  assert.equal(screen.queryByRole('button', { name: /^Select method / }), null)

  await goToStep(user, 'Compose')
  assert.ok(screen.getByTestId('experiment-grid'))
  assert.ok(screen.getByTestId('actor-workflow'))
})

test('keeps the local Configure surface free of a duplicate agent intake panel', () => {
  renderDemo('en-US', baseCapabilities, { credentialMode: 'local' })

  assert.equal(screen.queryByTestId('configure-agent-panel'), null)
  assert.equal(screen.queryByLabelText('Candidate GitHub repository'), null)
  assert.ok(document.querySelector('.agent-chat-column'))
})

test('opens method and database flashcards with what, origin, and intro', async () => {
  renderDemo()
  const user = userEvent.setup()

  assert.ok(document.querySelector('#configure.configuration-studio-compact'))
  assert.match(document.querySelector('#configure').textContent, /Three-layer recall|Question enrichment/)
  await user.click(screen.getByRole('button', { name: 'Open flashcard for method E-SQL' }))
  const dialog = screen.getByTestId('flashcard-dialog')
  assert.match(dialog.textContent, /What it is/)
  assert.match(dialog.textContent, /Origin/)
  assert.match(dialog.textContent, /Introduction/)
  assert.match(dialog.textContent, /CSG-QE-SR/)
  assert.match(dialog.textContent, /arXiv:2409\.16751/)
  assert.ok(screen.getByRole('link', { name: 'arXiv:2409.16751' }))
  assert.equal(screen.queryByRole('button', { name: /Select method|Select for run/ }), null)
  await user.click(screen.getByRole('button', { name: 'Done' }))
  await user.click(screen.getByRole('button', { name: 'Open flashcard for database BIRD' }))
  assert.ok(screen.getByRole('link', { name: 'https://bird-bench.github.io/' }))
  assert.match(screen.getByTestId('flashcard-dialog').textContent, /bird-bench\.github\.io/)
})

test('supports arbitrary graph connections without cartesian expansion', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('checkbox', { name: 'DINSQL × BIRD' }))

  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)
  assert.ok(screen.getByRole('button', { name: 'Focus connection C3SQL to Spider' }))
  assert.ok(screen.getByRole('button', { name: 'Focus connection DINSQL to BIRD' }))
  assert.equal(
    screen.queryByRole('button', { name: 'Focus connection C3SQL to BIRD' }),
    null,
  )
  assert.equal(
    screen.queryByRole('button', { name: 'Focus connection DINSQL to Spider' }),
    null,
  )
  assert.equal(
    screen.getByRole('checkbox', { name: 'DINSQL × BIRD' }).checked,
    true,
  )
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)
})

test('fans one method out to several databases from the grid', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  // Clicked back to back without waiting for the first commit, the way a user
  // wiring a row actually clicks.
  await act(async () => {
    fireEvent.click(within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }))
    fireEvent.click(within(grid).getByRole('checkbox', { name: 'DINSQL × Spider' }))
  })
  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × BookSQL' }))

  assert.equal(
    within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }).checked,
    true,
  )
  assert.equal(
    within(grid).getByRole('checkbox', { name: 'DINSQL × Spider' }).checked,
    true,
  )
  assert.equal(
    within(grid).getByRole('checkbox', { name: 'DINSQL × BookSQL' }).checked,
    true,
  )
  assert.ok(screen.getByRole('button', { name: 'Focus connection DINSQL to BIRD' }))
  assert.ok(screen.getByRole('button', { name: 'Focus connection DINSQL to Spider' }))
  assert.ok(screen.getByRole('button', { name: 'Focus connection DINSQL to BookSQL' }))
  assert.ok(screen.getByRole('button', { name: 'Focus connection C3SQL to Spider' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 4)
})

test('exposes every method and database pair as a grid cell', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  assert.equal(grid.querySelectorAll('tbody td').length, 64)
  assert.ok(within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }))
})

test('unselecting a matrix cell removes its connection', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)

  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
  assert.equal(within(grid).getByRole('checkbox', { name: 'DINSQL × BIRD' }).checked, false)
})

test('the last selected cell can be cleared and the plan becomes empty', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'C3SQL × Spider' }))
  assert.equal(screen.queryAllByRole('button', { name: /^Focus connection / }).length, 0)
  assert.equal(within(grid).getByRole('checkbox', { name: 'C3SQL × Spider' }).checked, false)
  assert.equal(within(grid).getByRole('button', { name: 'Go to run' }).disabled, true)
})

test('switches inspected workflow across multiple selected connections', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('checkbox', { name: 'DINSQL × BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)
  assert.match(screen.getByTestId('compose-connection-switcher').textContent, /2 \/ 2/)

  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to Spider' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
  assert.equal(
    screen.getByRole('button', { name: 'Focus connection C3SQL to Spider' }).getAttribute('aria-pressed'),
    'true',
  )
  assert.equal(
    screen.getByRole('button', { name: 'Focus connection DINSQL to BIRD' }).getAttribute('aria-pressed'),
    'false',
  )

  await user.click(screen.getByRole('button', { name: 'Next connection' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)

  await user.click(screen.getByRole('button', { name: 'Remove connection DINSQL to BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('moves method focus to a remaining selected method when removing the focused method', async () => {
  renderDemo('en-US', {
    reproduce_configs: catalogWith([
      C3SQL_SPIDER,
      {
        method: 'dinsql',
        dataset: 'spider',
        config_path: 'reproduce/configs/spider/dinsql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'DINSQLGenerator' }],
      },
    ]),
  })
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × Spider' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → Spider/)
  await user.click(within(grid).getByRole('checkbox', { name: 'DINSQL × Spider' }))

  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('moves database focus to a remaining selected database when removing the focused database', async () => {
  renderDemo('en-US', {
    reproduce_configs: catalogWith([
      C3SQL_SPIDER,
      {
        method: 'c3sql',
        dataset: 'bird',
        config_path: 'reproduce/configs/bird/c3sql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
      },
    ]),
  })
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'C3SQL × BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → BIRD/)
  await user.click(within(grid).getByRole('checkbox', { name: 'C3SQL × BIRD' }))

  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('renders real board controls for config runs', async () => {
  renderDemo('en-US')
  const user = userEvent.setup()

  await goToStep(user, 'Run')
  assert.match(document.querySelector('#board').textContent, /Parameter console/)
  assert.match(document.querySelector('#board').textContent, /reproduce\/run\.py|Config/)
  assert.equal(screen.getByRole('button', { name: 'Run config' }).disabled, true)
  assert.equal(document.querySelector('#board .diagnosis-workspace'), null)
  assert.equal(document.querySelector('#board .improvement-workspace'), null)
  await goToStep(user, 'Compare & History')
  assert.match(document.querySelector('#evidence').textContent, /Compare & History|No archived runs/)
  assert.ok(screen.getByTestId('evidence-workspace'))
})

test('translates the process navigation and board controls', async () => {
  renderDemo('en-US')
  assert.ok(screen.getByRole('navigation', { name: 'Workflow stages' }))

  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '切换到中文' }))

  const tabs = screen.getByRole('navigation', { name: '工作流阶段' })
  assert.ok(tabs)
  await user.click(within(tabs).getByRole('button', { name: /^实验/ }))
  const sections = screen.getByRole('navigation', { name: '实验步骤' })
  await user.click(within(sections).getByRole('button', { name: /运行/ }))
  assert.match(document.querySelector('#board').textContent, /参数工作台/)
  assert.match(document.querySelector('#board').textContent, /运行 Config|reproduce\/run\.py|本地 Demo/)
  assert.equal(document.querySelector('#board .diagnosis-workspace'), null)
  assert.equal(document.querySelector('#board .improvement-workspace'), null)
})

test('renders only configuration-backed workflow and provenance', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  assert.ok(screen.getByTestId('experiment-grid'))
  assert.match(screen.getByTestId('actor-workflow').textContent, /C3SQLGenerator/)
  assert.match(screen.getByTestId('actor-workflow').textContent, /GenerateTask/)

  assert.ok(screen.getByTestId('integration-provenance'))
  const provenance = screen.getByTestId('integration-provenance').textContent
  assert.match(provenance, /reproduce\/configs\/spider\/c3sql\.json/)
  assert.match(provenance, /C3SQLGenerator/)
  assert.match(provenance, /GenerateTask|Actors/)
  assert.doesNotMatch(provenance, /Unavailable/)
  await user.click(screen.getByRole('button', { name: /Configuration provenance/ }))
  assert.equal(screen.getByTestId('integration-provenance').hidden, true)
})

test('does not invent stages for a connection without a verified workflow', async () => {
  renderDemo()
  const user = userEvent.setup()
  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to BIRD' }))

  assert.match(screen.getByTestId('actor-workflow').textContent, /No verified workflow/)
  assert.doesNotMatch(screen.getByTestId('actor-workflow').textContent, /Generator/)
})

test('expands archive charts inline without requesting raw files on hosted demo', async () => {
  const calls = []
  const api = async path => {
    calls.push(String(path))
    if (String(path).startsWith('/api/archive?') || path === '/api/archive') {
      return {
        runs: [{
          run_id: 'run-archive-1',
          method: 'c3sql',
          dataset: 'spider',
          split: 'dev',
          source: 'evidence',
          metrics: { ex: 0.5 },
          file_count: 1,
        }],
        filters: { datasets: ['spider'], methods: ['c3sql'], sources: ['evidence'] },
        total: 1,
      }
    }
    if (path === '/api/archive/run-archive-1') {
      return {
        run_id: 'run-archive-1',
        method: 'c3sql',
        dataset: 'spider',
        split: 'dev',
        source: 'evidence',
        metrics: { ex: 0.5 },
        files: [{ name: 'scores.json', path: 'scores.json', size_bytes: 12, kind: 'json' }],
      }
    }
    if (String(path).includes('/api/archive/run-archive-1/files/')) {
      return {
        name: 'scores.json',
        path: 'scores.json',
        kind: 'json',
        json: {
          method: 'c3sql',
          dataset: 'spider',
          metrics: { ex: 0.5, em: 0.4 },
        },
      }
    }
    return { runs: [] }
  }
  renderDemo('en-US', {
    ...baseCapabilities,
    deployment: { target: 'hf-space', features: { live_evaluation: false } },
  }, { api })
  const user = userEvent.setup()

  await goToStep(user, 'Compare & History')
  await user.click(screen.getByRole('button', { name: 'Past experiments' }))
  await screen.findByText('run-archive-1')
  await screen.findByRole('button', { name: 'Open run' })
  await user.click(screen.getByRole('button', { name: 'Open run' }))
  assert.ok(screen.getByTestId('evidence-run-page'))
  await screen.findByRole('button', { name: 'Expand charts' })
  await user.click(screen.getByRole('button', { name: 'Expand charts' }))

  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-section'), 'evidence')
  assert.equal(window.location.hash, '#evidence')
  assert.ok(screen.getByTestId('evidence-charts-panel'))
  await screen.findByText(/Archive run · run-archive-1/)
  // Hosted History keeps raw archive files closed; charts use sanitized summary fields.
  assert.equal(calls.some(path => path.includes('/files/')), false)
})

test('keeps config-run jobs labelled with their immutable connection after focus changes', async () => {
  const capabilities = {
    deployment: { features: { live_evaluation: true } },
    reproduce_configs: catalogWith([
      C3SQL_SPIDER,
      {
        method: 'c3sql',
        dataset: 'bird',
        config_path: 'reproduce/configs/bird/c3sql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
      },
    ]),
  }
  const postJson = async (path, body) => {
    if (path === '/api/experiments') {
      return {
        experiment_id: 'exp-1',
        status: 'completed',
        sampling: body.sampling,
        cells: body.cells.map((cell, index) => ({
          cell_id: `c${index + 1}`,
          method: cell.method,
          dataset: cell.dataset,
          job_id: 'job-1',
          status: 'completed',
          progress: {},
        })),
      }
    }
    return {}
  }
  renderDemo('en-US', capabilities, {
    databases: [{ id: 'Spider' }, { id: 'BIRD' }],
    sqlAuth: { configured: true, provider: 'openai', model: 'gpt-4.1-mini' },
    postJson,
    api: async path => {
      if (String(path).startsWith('/api/archive')) {
        return { runs: [], filters: { datasets: [], methods: [], sources: [] }, total: 0 }
      }
      return { job_id: 'job-1', method: 'c3sql', dataset: 'spider', status: 'completed', log: 'ok' }
    },
  })
  const user = userEvent.setup()

  await goToStep(user, 'Run')
  await user.click(screen.getByRole('button', { name: 'Run config' }))
  await screen.findByText((content, element) => {
    return element?.tagName === 'STRONG' && /c3sql\s*\/\s*spider/i.test(content)
  })

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → BIRD/)

  await goToStep(user, 'Run')
  const monitor = screen.getByTestId('run-batch-monitor').textContent
  assert.match(monitor, /c3sql\s*\/\s*spider/i)
  assert.doesNotMatch(monitor, /c3sql\s*\/\s*bird/i)
})

test('a selected matrix cell opens its workflow from the matrix itself', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  await user.click(within(grid).getByRole('checkbox', { name: 'C3SQL × BIRD' }))
  await user.click(within(grid).getByRole('button', { name: 'Show the workflow of C3SQL × BIRD' }))

  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → BIRD/)
  assert.equal(
    within(grid).getByRole('button', { name: 'Show the workflow of C3SQL × BIRD' }).getAttribute('aria-pressed'),
    'true',
  )
  // Only selected pairs have a workflow control; DINSQL × BIRD is not selected.
  assert.equal(within(grid).queryByRole('button', { name: 'Show the workflow of DINSQL × BIRD' }), null)
})

test('each matrix cell shows one state: no config, not installed, or its checkbox', async () => {
  const configs = catalogWith([C3SQL_SPIDER]).filter(config => !(config.method === 'c3sql' && config.dataset === 'BIRD'))
  renderDemo('en-US', {
    reproduce_configs: configs.map(config => (config.dataset === 'AmbiDB' ? { ...config, installed: false } : config)),
  })
  const user = userEvent.setup()
  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('experiment-grid')
  const rowOf = method => [...grid.querySelectorAll('tbody tr')].find(row => row.querySelector('th')?.textContent === method)
  const cellOf = (method, database) => rowOf(method).children[DATABASES.indexOf(database) + 1]

  const noConfig = cellOf('C3SQL', 'BIRD')
  assert.equal(noConfig.textContent, 'no config')
  assert.equal(noConfig.querySelectorAll('input, button').length, 0)

  const notInstalled = cellOf('C3SQL', 'AmbiDB')
  assert.equal(notInstalled.textContent, 'Dataset not installed')
  assert.equal(notInstalled.querySelectorAll('input, button').length, 0)

  const selectable = cellOf('C3SQL', 'Spider')
  assert.equal(selectable.querySelectorAll('input').length, 1)
  assert.doesNotMatch(selectable.textContent, /no config|not installed/)
})

test('an unsent draft plan survives a page reload', async () => {
  const first = renderDemo()
  const user = userEvent.setup()
  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }))
  assert.equal(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }).checked, true)
  first.unmount()

  renderDemo()
  await goToStep(user, 'Compose')
  assert.equal(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }).checked, true, 'the draft is restored')
  assert.equal(screen.getByRole('checkbox', { name: 'C3SQL × Spider' }).checked, true)
})

test('the draft keeps the experiment sampling across a reload', async () => {
  const first = renderDemo()
  const user = userEvent.setup()
  await goToStep(user, 'Compose')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Sample limit' }), '50')
  first.unmount()

  renderDemo()
  await goToStep(user, 'Compose')
  assert.equal(screen.getByRole('combobox', { name: 'Sample limit' }).value, '50')
})

test('a past experiment can be used as the template of a new plan', async () => {
  const api = async path => {
    if (String(path) === '/api/experiments') {
      return {
        experiments: [{
          experiment_id: 'exp-old',
          status: 'completed',
          sampling: { limit: 50, mode: 'random', seed: 7 },
          created_at: 1700000100,
          cells: [{ method: 'c3sql', dataset: 'bird', stages: {} }],
        }],
      }
    }
    return { runs: [] }
  }
  renderDemo('en-US', baseCapabilities, { api })
  const user = userEvent.setup()
  await goToStep(user, 'Compare & History')
  await user.click(screen.getByRole('button', { name: 'Past experiments' }))
  await user.click(await screen.findByRole('button', { name: 'Use as template' }))

  const stage = screen.getByTestId('flow-stage')
  assert.equal(stage.getAttribute('data-active-section'), 'compose')
  assert.equal(screen.getByRole('combobox', { name: 'Sample limit' }).value, '50')
  assert.equal(screen.getByRole('checkbox', { name: 'C3SQL × BIRD' }).checked, true)
})

test('a running experiment is shown again after the page is reloaded', async () => {
  const running = {
    experiment_id: 'exp-live',
    status: 'running',
    sampling: { limit: 20, mode: 'slice', seed: 42 },
    created_at: 1700000300,
    cells: [{ cell_id: 'c1', method: 'c3sql', dataset: 'spider', job_id: null, status: 'running', progress: {} }],
  }
  const api = async path => {
    if (String(path) === '/api/experiments') return { experiments: [running] }
    if (String(path) === '/api/experiments/exp-live') return running
    return { runs: [] }
  }
  renderDemo('en-US', baseCapabilities, { api })
  const user = userEvent.setup()
  await goToStep(user, 'Run')
  await waitFor(() => assert.match(screen.getByTestId('run-batch-monitor').textContent, /c3sql \/ spider/))
})

test('the Experiment tab shows where the plan is while it runs', async () => {
  const running = {
    experiment_id: 'exp-run',
    status: 'running',
    sampling: { limit: 20, mode: 'slice', seed: 42 },
    created_at: 1700000400,
    cells: [{ cell_id: 'c1', method: 'c3sql', dataset: 'spider', job_id: null, status: 'running', progress: {} }],
  }
  const api = async path => (String(path) === '/api/experiments' ? { experiments: [] } : { runs: [] })
  const postJson = async (path, body) => (path === '/api/experiments'
    ? { ...running, sampling: body.sampling }
    : {})
  renderDemo('en-US', { ...baseCapabilities, deployment: { features: { live_evaluation: true } } }, { api, postJson, sqlAuth: { configured: true } })
  const user = userEvent.setup()
  await goToStep(user, 'Run')
  await user.click(screen.getByRole('button', { name: 'Run config' }))
  await waitFor(() => assert.equal(screen.getByTestId('experiment-status').textContent, 'Running'))
})

test('an archived run of a variant says so, next to its method', async () => {
  const api = async path => {
    if (String(path).startsWith('/api/archive')) {
      return {
        runs: [{
          run_id: 'spider-c3sql-variant',
          method: 'c3sql',
          variant: 'c3sql.var-1a2b3c',
          label: 'c3sql · c3sql_generate→FINSQLGenerator',
          stages: { c3sql_generate: 'FINSQLGenerator' },
          dataset: 'spider',
          metrics: { ex: 0.7 },
          sample_count: 20,
          file_count: 1,
          files: [],
        }],
        filters: { datasets: ['spider'], methods: ['c3sql'], sources: ['artifacts'] },
        total: 1,
      }
    }
    return { runs: [] }
  }
  renderDemo('en-US', baseCapabilities, { api })
  const user = userEvent.setup()
  await goToStep(user, 'Compare & History')
  const card = await screen.findByText('spider-c3sql-variant')
  assert.match(card.closest('.archive-run-select').textContent, /Variant/)
  assert.match(card.closest('.archive-run-select').textContent, /c3sql_generate→FINSQLGenerator/)
})
