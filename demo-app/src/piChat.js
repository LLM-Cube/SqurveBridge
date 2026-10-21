export const createPiChatState = () => ({
  status: 'stopped',
  messages: [],
  tools: [],
  skills: [],
  profile: '',
  provider: '',
  model: '',
  error: '',
  proposals: [],
})

const finishStreamingMessage = messages => messages.map((message, index) => (
  index === messages.length - 1 && message.role === 'assistant'
    ? { ...message, streaming: false }
    : message
))

export function applyPiEvent(state, event) {
  if (event.type === 'session') return { ...state, status: event.running ? 'starting' : 'stopped', profile: event.profile || state.profile }
  if (event.type === 'ready') {
    return {
      ...state,
      status: 'ready',
      skills: event.skills || state.skills,
      profile: event.profile || state.profile,
      provider: event.provider || state.provider,
      model: event.model || state.model,
      error: '',
    }
  }
  if (event.type === 'agent_start') return { ...state, status: 'thinking', error: '' }
  if (event.type === 'text_delta' || event.type === 'thinking_delta') {
    const field = event.type === 'text_delta' ? 'content' : 'thinking'
    const last = state.messages.at(-1)
    if (last?.role === 'assistant' && last.streaming) {
      return {
        ...state,
        messages: state.messages.map((message, index) => index === state.messages.length - 1
          ? { ...message, [field]: `${message[field] || ''}${event.delta || ''}` }
          : message),
      }
    }
    return {
      ...state,
      messages: [...state.messages, { role: 'assistant', content: '', thinking: '', streaming: true, [field]: event.delta || '' }],
    }
  }
  if (event.type === 'tool_start') {
    return {
      ...state,
      tools: [...state.tools, { id: event.tool_call_id, name: event.tool_name, args: event.args || {}, status: 'running', isError: false }],
    }
  }
  if (event.type === 'tool_end') {
    return {
      ...state,
      tools: state.tools.map(tool => tool.id === event.tool_call_id
        ? { ...tool, status: event.is_error ? 'failed' : 'complete', isError: Boolean(event.is_error) }
        : tool),
    }
  }
  if (event.type === 'agent_end' || event.type === 'agent_settled') {
    return { ...state, status: 'ready', messages: finishStreamingMessage(state.messages) }
  }
  if (event.type === 'aborted') return { ...state, status: 'ready', messages: finishStreamingMessage(state.messages) }
  if (event.type === 'exit') return { ...state, status: 'stopped', messages: finishStreamingMessage(state.messages) }
  if (event.type === 'ui_action') {
    return {
      ...state,
      proposals: [...(state.proposals || []), {
        id: event.id,
        action: event.action,
        args: event.args || {},
        reason: event.reason || null,
        status: 'pending',
      }],
    }
  }
  if (event.type === 'bridge_error' || event.type === 'command_error') {
    return { ...state, status: 'error', error: event.message || 'Pi agent failed.' }
  }
  return state
}

export const appendUserMessage = (state, content) => ({
  ...state,
  messages: [...state.messages, { role: 'user', content, streaming: false }],
})

export const skillPrompt = (name, args = '') => `/skill:${name}${args ? ` ${args}` : ''}`

/** Mark an Agent proposal confirmed or dismissed; unknown ids leave state unchanged. */
export function resolveProposal(state, id, status) {
  if (!(state.proposals || []).some(proposal => proposal.id === id)) return state
  return {
    ...state,
    proposals: state.proposals.map(proposal => (proposal.id === id ? { ...proposal, status } : proposal)),
  }
}
