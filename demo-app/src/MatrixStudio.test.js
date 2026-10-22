import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from './testDom.js'

const closeDom = installTestDom()
globalThis.React = React
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: window.localStorage,
})
const { act, cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
registerLoader('./cssTestLoader.mjs', import.meta.url)
const unregister = register()

const { default: FullFlowDemo } = await import('./MatrixStudio.jsx')
const fullFlowCss = readFileSync(
  new URL('./full-flow/full-flow.css', import.meta.url),
  'utf8',
)

const baseCapabilities = {
  reproduce_configs: [{
    method: 'c3sql',
    dataset: 'spider',
    config_path: 'reproduce/configs/spider/c3sql.json',
    stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
  }],
}

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

async function goToStep(user, name) {
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  await user.click(within(tabs).getByRole('button', { name }))
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
    ['Studio', 'Compose', 'Run', 'Compare & History', 'Query'],
    'compose, run and compare & history sit together; query stands apart',
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
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'compose')
  assert.equal(document.querySelector('#compose h2')?.textContent, 'Workflow Composition')
  assert.equal(window.location.hash, '#compose')

  await goToStep(user, 'Run')
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'board')
  assert.equal(document.querySelector('#board h2')?.textContent, 'Experiment Board')

  await goToStep(user, 'Compare & History')
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'evidence')
  const tabs = screen.getByRole('navigation', { name: 'Workflow stages' })
  assert.equal(
    within(tabs).getByRole('button', { name: 'Compare & History' }).getAttribute('aria-current'),
    'page',
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
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'board')
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
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'evidence')
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
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'evidence')
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
  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'evidence')
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

test('wires exactly one connection per node pair without cartesian expansion', async () => {
  renderDemo()
  const user = userEvent.setup()

  assert.ok(document.querySelector('[data-testid="catalog-workspaces"]'))
  assert.equal(document.querySelectorAll('.catalog-workspace').length, 2)
  assert.ok(document.querySelector('.catalog-workspace-methods'))
  assert.ok(document.querySelector('.catalog-workspace-databases'))
  assert.equal(document.querySelector('#catalog-methods-title')?.textContent, 'Methods')
  assert.equal(document.querySelector('#catalog-databases-title')?.textContent, 'Databases')
  assert.equal(screen.getAllByRole('button', { name: /^Open flashcard for method / }).length, 8)
  assert.equal(screen.getAllByRole('button', { name: /^Open flashcard for database / }).length, 8)

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from method DINSQL' }))
  assert.equal(screen.getByTestId('compose-wire-hint').getAttribute('data-armed'), 'true')
  assert.match(screen.getByTestId('compose-wire-hint').textContent, /Wiring from DINSQL/)
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to BIRD' }))

  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)
  assert.ok(screen.getByRole('button', { name: 'Focus connection DINSQL to BIRD' }))
  assert.equal(screen.queryByRole('button', { name: 'Focus connection DINSQL to Spider' }), null)
  assert.equal(screen.queryByRole('button', { name: 'Focus connection C3SQL to BIRD' }), null)
  assert.equal(
    screen.getByRole('button', { name: 'Cancel wiring from DINSQL' }).getAttribute('aria-pressed'),
    'true',
  )

  // Wiring stays armed, so the same method can be fanned out to more databases.
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to Spider' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 3)
  // The freshly connected pair is focused; clicking it again removes it.
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to Spider' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)
})

test('cancels the pending wiring state with Escape', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from method DINSQL' }))
  assert.equal(screen.getByTestId('compose-wire-hint').getAttribute('data-armed'), 'true')
  assert.ok(document.querySelector('.flow-connection-graph[data-armed="true"]'))

  await user.keyboard('{Escape}')
  assert.equal(screen.getByTestId('compose-wire-hint').getAttribute('data-armed'), 'false')
  assert.ok(screen.getByRole('button', { name: 'Start wiring from method DINSQL' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
})

test('announces node-first connect, inspect, and remove through the live region', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from method DINSQL' }))
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to BIRD' }))
  assert.match(screen.getByTestId('compose-live-region').textContent, /Connected DINSQL to BIRD/)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)

  // Clicking a connected-but-not-inspected pair switches the Inspect panel.
  await user.click(screen.getByRole('button', { name: 'Start wiring from method C3SQL' }))
  await user.click(screen.getByRole('button', { name: 'Wire C3SQL to Spider' }))
  assert.match(screen.getByTestId('compose-live-region').textContent, /Inspecting C3SQL to Spider/)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)

  // Clicking the inspected pair again removes it.
  await user.click(screen.getByRole('button', { name: 'Wire C3SQL to Spider' }))
  assert.match(screen.getByTestId('compose-live-region').textContent, /Removed connection C3SQL to Spider/)
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
})

test('refuses to drop the last connection and announces why', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from method C3SQL' }))
  await user.click(screen.getByRole('button', { name: 'Wire C3SQL to Spider' }))

  assert.match(screen.getByTestId('compose-live-region').textContent, /At least one connection must remain/)
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('keeps Studio focused on explaining methods and databases', async () => {
  renderDemo()
  const user = userEvent.setup()

  assert.equal(screen.queryByTestId('configure-agent-panel'), null)
  assert.match(document.querySelector('#configure').textContent, /Studio|工作室/)
  assert.match(screen.getByTestId('studio-guide').textContent, /Browse, then open|先浏览/)
  assert.ok(document.querySelector('.agent-chat-column'))
  assert.match(screen.getByRole('heading', { level: 2, name: 'SqurveBridge Agent' }).textContent, /SqurveBridge Agent/)
  assert.equal(document.querySelector('#configure .flow-connection-graph'), null)
  assert.equal(document.querySelector('#configure [data-testid="actor-workflow"]'), null)
  assert.equal(screen.queryByRole('button', { name: /^Select method / }), null)

  await goToStep(user, 'Compose')
  assert.ok(document.querySelector('#compose .flow-connection-graph'))
  assert.ok(screen.getByTestId('actor-workflow'))
  assert.match(document.querySelector('#compose').textContent, /Method × Database/)
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
  await user.click(screen.getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))

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
    screen.getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }).getAttribute('aria-pressed'),
    'true',
  )
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)
})

test('fans one method out to several databases from the grid', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  // Clicked back to back without waiting for the first commit, the way a user
  // wiring a row actually clicks.
  await act(async () => {
    fireEvent.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
    fireEvent.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to Spider' }))
  })
  await user.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BookSQL' }))

  assert.equal(
    within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }).getAttribute('aria-pressed'),
    'true',
  )
  assert.equal(
    within(grid).getByRole('button', { name: 'Toggle connection DINSQL to Spider' }).getAttribute('aria-pressed'),
    'true',
  )
  assert.equal(
    within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BookSQL' }).getAttribute('aria-pressed'),
    'true',
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
  const grid = screen.getByTestId('compose-grid')
  assert.equal(grid.querySelectorAll('.compose-grid-cell').length, 64)

  // The seeded pair reads as connected and currently inspected.
  const seeded = within(grid).getByRole('button', { name: 'Toggle connection C3SQL to Spider' })
  assert.equal(seeded.getAttribute('aria-pressed'), 'true')
  assert.ok(seeded.classList.contains('focused'))

  // A pair with a reproduce config is marked ready before it is wired.
  const unwired = within(grid).getByRole('button', { name: 'Toggle connection SEDE to AmbiDB' })
  assert.equal(unwired.getAttribute('aria-pressed'), 'false')
  assert.ok(unwired.classList.contains('unavailable'))
})

test('removes a grid connection only through the cell disconnect control', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  await user.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)

  // The cell body no longer doubles as a delete: clicking the inspected cell
  // again must keep the pair.
  await user.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
  assert.equal(
    within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }).getAttribute('aria-pressed'),
    'true',
  )
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)

  await user.click(within(grid).getByRole('button', { name: 'Disconnect DINSQL from BIRD' }))
  assert.match(screen.getByTestId('compose-live-region').textContent, /Removed connection DINSQL to BIRD/)
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
  assert.equal(
    within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }).getAttribute('aria-pressed'),
    'false',
  )
  assert.equal(within(grid).queryByRole('button', { name: 'Disconnect DINSQL from BIRD' }), null)
})

test('restores a removed grid connection and its inspected state through undo', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  await user.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)

  // Dropping the inspected pair and undoing puts it back as the inspected one.
  await user.click(within(grid).getByRole('button', { name: 'Disconnect DINSQL from BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Undo removing DINSQL to BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → BIRD/)
  assert.equal(screen.queryByTestId('compose-undo'), null)

  // Dropping a pair that is not inspected must not steal the inspected one.
  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to Spider' }))
  await user.click(within(grid).getByRole('button', { name: 'Disconnect DINSQL from BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Undo removing DINSQL to BIRD' }))
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 2)
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('drops the undo entry once the next composition step happens', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  await user.click(within(grid).getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
  await user.click(within(grid).getByRole('button', { name: 'Disconnect DINSQL from BIRD' }))
  assert.ok(screen.getByTestId('compose-undo'))

  await user.click(within(grid).getByRole('button', { name: 'Toggle connection SEDE to AmbiDB' }))
  assert.equal(screen.queryByTestId('compose-undo'), null)
})

test('protects the last connection from the grid disconnect control', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  const drop = within(grid).getByRole('button', { name: 'Disconnect C3SQL from Spider' })
  assert.equal(drop.getAttribute('aria-disabled'), 'true')
  assert.match(drop.getAttribute('title'), /At least one connection must remain/)

  await user.click(drop)
  assert.match(screen.getByTestId('compose-live-region').textContent, /At least one connection must remain/)
  assert.equal(screen.getAllByRole('button', { name: /^Focus connection / }).length, 1)
  assert.equal(screen.queryByTestId('compose-undo'), null)
})

test('marks the hovered row and column across the connection grid', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  const grid = screen.getByTestId('compose-grid')
  await user.hover(within(grid).getByRole('button', { name: 'Toggle connection SEDE to BookSQL' }))

  assert.equal(
    within(grid).getByRole('rowheader', { name: 'SEDE' }).getAttribute('data-peer'),
    'true',
  )
  assert.equal(
    within(grid).getByRole('columnheader', { name: 'BookSQL' }).getAttribute('data-peer'),
    'true',
  )
  assert.equal(
    within(grid).getByRole('rowheader', { name: 'C3SQL' }).getAttribute('data-peer'),
    'false',
  )
})

test('switches inspected workflow across multiple selected connections', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Toggle connection DINSQL to BIRD' }))
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
    reproduce_configs: [
      ...baseCapabilities.reproduce_configs,
      {
        method: 'dinsql',
        dataset: 'spider',
        config_path: 'reproduce/configs/spider/dinsql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'DINSQLGenerator' }],
      },
    ],
  })
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from method DINSQL' }))
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to Spider' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /DINSQL → Spider/)
  await user.click(screen.getByRole('button', { name: 'Wire DINSQL to Spider' }))

  assert.equal(screen.getByRole('button', { name: 'Start wiring from method C3SQL' }).getAttribute('aria-pressed'), 'true')
  assert.equal(screen.getByRole('button', { name: 'Cancel wiring from DINSQL' }).getAttribute('aria-pressed'), 'false')
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → Spider/)
})

test('moves database focus to a remaining selected database when removing the focused database', async () => {
  renderDemo('en-US', {
    reproduce_configs: [
      ...baseCapabilities.reproduce_configs,
      {
        method: 'c3sql',
        dataset: 'bird',
        config_path: 'reproduce/configs/bird/c3sql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
      },
    ],
  })
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Start wiring from database BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Wire C3SQL to BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → BIRD/)
  await user.click(screen.getByRole('button', { name: 'Wire C3SQL to BIRD' }))

  assert.equal(screen.getByRole('button', { name: 'Start wiring from database Spider' }).getAttribute('aria-pressed'), 'true')
  assert.equal(screen.getByRole('button', { name: 'Cancel wiring from BIRD' }).getAttribute('aria-pressed'), 'false')
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
  await user.click(within(tabs).getByRole('button', { name: '运行' }))
  assert.match(document.querySelector('#board').textContent, /参数工作台/)
  assert.match(document.querySelector('#board').textContent, /运行 Config|reproduce\/run\.py|本地 Demo/)
  assert.equal(document.querySelector('#board .diagnosis-workspace'), null)
  assert.equal(document.querySelector('#board .improvement-workspace'), null)
})

test('renders only configuration-backed workflow and provenance', async () => {
  renderDemo()
  const user = userEvent.setup()

  await goToStep(user, 'Compose')
  assert.ok(screen.getByRole('group', { name: /^Method to database configuration matrix/ }))
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

test('does not invent stages for an unavailable connection', async () => {
  renderDemo()
  const user = userEvent.setup()
  await goToStep(user, 'Compose')
  await user.click(screen.getByRole('button', { name: 'Toggle connection C3SQL to BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to BIRD' }))

  assert.match(screen.getByTestId('actor-workflow').textContent, /No verified workflow/)
  assert.doesNotMatch(screen.getByTestId('actor-workflow').textContent, /Generator/)
  await goToStep(user, 'Run')
  assert.match(document.querySelector('#board').textContent, /configuration is unavailable/)
  assert.equal(screen.getByRole('button', { name: 'Run config' }).disabled, true)
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
  await screen.findByText('run-archive-1')
  await screen.findByRole('button', { name: 'Open run' })
  await user.click(screen.getByRole('button', { name: 'Open run' }))
  assert.ok(screen.getByTestId('evidence-run-page'))
  await screen.findByRole('button', { name: 'Expand charts' })
  await user.click(screen.getByRole('button', { name: 'Expand charts' }))

  assert.equal(screen.getByTestId('flow-stage').getAttribute('data-active-step'), 'evidence')
  assert.equal(window.location.hash, '#evidence')
  assert.ok(screen.getByTestId('evidence-charts-panel'))
  await screen.findByText(/Archive run · run-archive-1/)
  // Hosted History keeps raw archive files closed; charts use sanitized summary fields.
  assert.equal(calls.some(path => path.includes('/files/')), false)
})

test('keeps config-run jobs labelled with their immutable connection after focus changes', async () => {
  const capabilities = {
    deployment: { features: { live_evaluation: true } },
    reproduce_configs: [
      baseCapabilities.reproduce_configs[0],
      {
        method: 'c3sql',
        dataset: 'bird',
        config_path: 'reproduce/configs/bird/c3sql.json',
        stages: [{ id: 'generate', type: 'GenerateTask', actor: 'C3SQLGenerator' }],
      },
    ],
  }
  const postJson = async (path, body) => {
    if (path === '/api/evaluations') {
      return {
        job_id: 'job-1',
        method: body.method,
        dataset: body.dataset,
        status: 'completed',
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
  await user.click(screen.getByRole('button', { name: 'Toggle connection C3SQL to BIRD' }))
  await user.click(screen.getByRole('button', { name: 'Focus connection C3SQL to BIRD' }))
  assert.match(screen.getByTestId('compose-workflow-panel').textContent, /C3SQL → BIRD/)

  await goToStep(user, 'Run')
  const monitor = screen.getByTestId('run-batch-monitor').textContent
  assert.match(monitor, /c3sql\s*\/\s*spider/i)
  assert.doesNotMatch(monitor, /c3sql\s*\/\s*bird/i)
})
