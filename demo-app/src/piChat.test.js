import test from 'node:test'
import assert from 'node:assert/strict'

async function chatModule() {
  try {
    return await import('./piChat.js')
  } catch (error) {
    assert.fail(`Pi chat state module must load: ${error.message}`)
  }
}

test('builds assistant text from Pi streaming events', async () => {
  const { createPiChatState, applyPiEvent } = await chatModule()
  let state = createPiChatState()
  state = applyPiEvent(state, { type: 'agent_start' })
  state = applyPiEvent(state, { type: 'text_delta', delta: 'Hello' })
  state = applyPiEvent(state, { type: 'text_delta', delta: ' world' })
  state = applyPiEvent(state, { type: 'agent_end' })
  assert.equal(state.messages.at(-1).content, 'Hello world')
  assert.equal(state.messages.at(-1).streaming, false)
  assert.equal(state.status, 'ready')
})

test('tracks Pi tool calls as structured activity', async () => {
  const { createPiChatState, applyPiEvent } = await chatModule()
  let state = applyPiEvent(createPiChatState(), {
    type: 'tool_start', tool_call_id: 'tool-1', tool_name: 'read', args: { path: 'README.md' },
  })
  state = applyPiEvent(state, {
    type: 'tool_end', tool_call_id: 'tool-1', tool_name: 'read', is_error: false,
  })
  assert.deepEqual(state.tools[0], {
    id: 'tool-1', name: 'read', args: { path: 'README.md' }, status: 'complete', isError: false,
  })
})

test('streaming deltas keep untouched message identities for memoized rendering', async () => {
  const { createPiChatState, applyPiEvent, appendUserMessage } = await chatModule()
  let state = appendUserMessage(createPiChatState(), 'Show all singers')
  state = applyPiEvent(state, { type: 'text_delta', delta: 'SELECT' })
  const userMessage = state.messages[0]
  const next = applyPiEvent(state, { type: 'text_delta', delta: ' * FROM singer' })
  // The chat log memoizes per message; earlier entries must keep their
  // object identity so streaming only re-renders the active message.
  assert.equal(next.messages[0], userMessage)
  assert.notEqual(next.messages[1], state.messages[1])
  assert.equal(next.messages[1].content, 'SELECT * FROM singer')
})

test('formats project skills with Pi native command syntax', async () => {
  const { skillPrompt } = await chatModule()
  assert.equal(skillPrompt('run'), '/skill:run')
  assert.equal(skillPrompt('candidate-reader', 'https://github.com/example/repo'), '/skill:candidate-reader https://github.com/example/repo')
})

test('ui_action events become pending proposals the user can confirm or dismiss', async () => {
  const { applyPiEvent, createPiChatState, resolveProposal } = await chatModule()
  let state = createPiChatState()
  state = applyPiEvent(state, { type: 'ui_action', id: 'p1', action: 'open_cell', args: { method: 'c3sql', dataset: 'spider' }, reason: 'Look here' })
  assert.deepEqual(state.proposals, [
    { id: 'p1', action: 'open_cell', args: { method: 'c3sql', dataset: 'spider' }, reason: 'Look here', status: 'pending' },
  ])
  state = resolveProposal(state, 'p1', 'confirmed')
  assert.equal(state.proposals[0].status, 'confirmed')
  assert.deepEqual(resolveProposal(state, 'missing', 'dismissed'), state)
})
