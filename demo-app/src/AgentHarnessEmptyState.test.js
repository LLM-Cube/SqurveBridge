import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from './testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render, screen } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
const unregister = register()

test.afterEach(() => cleanup())
test.after(() => {
  unregister()
  closeDom()
})

const Status = ({ children }) => React.createElement('span', null, children)

async function renderShell() {
  globalThis.fetch = async () => ({ ok: true })
  const AgentHarness = (await import('./AgentHarness.jsx')).default
  const api = async () => ({ available: true, backend: 'pi', profile: 'local', provider: null, model: null, skills: [], tools: [] })
  const postJson = async () => ({ session_id: 'session-empty', running: true })
  render(React.createElement(AgentHarness, { api, postJson, Status, shell: true }))
}

test('shell empty state shows greeting and capability cards', async () => {
  await renderShell()
  const cards = await screen.findByTestId('agent-empty-cards')
  const buttons = cards.querySelectorAll('button.pi-chat-hero-card')
  assert.equal(buttons.length, 4)
  assert.ok(screen.getByText('Inspect integration'))
  assert.ok(screen.getByText('Reproduce a run'))
})

test('clicking a capability card prefills the composer without sending', async () => {
  await renderShell()
  const user = userEvent.setup()
  await screen.findByTestId('agent-empty-cards')
  await user.click(screen.getByText('Integrate a method'))
  const textarea = screen.getByPlaceholderText('Type / for skills — connect a model to send')
  assert.match(textarea.value, /^\/skill:candidate-reader /)
  // Nothing was sent: the empty-state cards (and greeting) must still be visible.
  assert.ok(screen.getByTestId('agent-empty-cards'))
})
