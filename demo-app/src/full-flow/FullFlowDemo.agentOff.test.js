import assert from 'node:assert/strict'
import test from 'node:test'
import { register as registerLoader } from 'node:module'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()

const { default: FullFlowDemo } = await import('./FullFlowDemo.jsx')

window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })

const renderShell = agentChat => render(React.createElement(FullFlowDemo, {
  capabilities: {
    reproduce_configs: [],
    llm_providers: [],
    deployment: { target: 'local', features: { agent_chat: agentChat } },
  },
  databases: [],
  sqlAuth: null,
  api: async () => ({}),
  postJson: async () => ({}),
  onConfigureSql: () => {},
  credentialMode: 'local',
}))

test.afterEach(() => {
  cleanup()
  window.localStorage.clear()
})
test.after(() => {
  unregister()
  closeDom()
})

test('with the Agent off the workspace is the whole page', () => {
  const { container } = renderShell(false)
  assert.ok(container.querySelector('.agent-shell').classList.contains('agent-off'))
  for (const selector of [
    '.agent-chat-column', '.agent-chat-restore', '.agent-dashboard-restore',
    '.agent-shell-divider', '.agent-mobile-switcher', '.agent-pane-toggle',
  ]) {
    assert.equal(container.querySelector(selector), null, selector)
  }
  assert.ok(container.querySelector('.agent-dashboard-pane'))
})

test('a collapsed workspace saved earlier does not hide the page when the Agent is off', () => {
  window.localStorage.setItem('squrve-demo-shell-layout', JSON.stringify({ dashboardCollapsed: true }))
  const { container } = renderShell(false)
  assert.ok(!container.querySelector('.agent-shell').classList.contains('dashboard-collapsed'))
})

test('with the Agent on the shell keeps its Agent pane', () => {
  const { container } = renderShell(true)
  assert.ok(container.querySelector('.agent-chat-column'))
  assert.ok(container.querySelector('.agent-shell-divider'))
  assert.ok(!container.querySelector('.agent-shell').classList.contains('agent-off'))
})
