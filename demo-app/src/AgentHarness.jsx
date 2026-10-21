import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import PiAuthDialog from './PiAuthDialog.jsx'
import { appendUserMessage, applyPiEvent, createPiChatState, resolveProposal, skillPrompt } from './piChat.js'
import { applyPiAuthEvent, createPiAuthState } from './piAuth.js'
import { extractSqlSegments } from './full-flow/queryModel.js'

const DEFAULT_SKILLS = ['candidate-reader', 'integration-pipeline', 'config-adapter', 'run', 'meta-evo']

const SKILL_META = {
  'candidate-reader': {
    step: 1,
    titleKey: 'agent.skill.candidateReader',
    detailKey: 'configure.agentSkillCandidate',
  },
  'integration-pipeline': {
    step: 2,
    titleKey: 'agent.skill.integrationPipeline',
    detailKey: 'configure.agentSkillPipeline',
  },
  'config-adapter': {
    step: 3,
    titleKey: 'agent.skill.configAdapter',
    detailKey: 'configure.agentSkillConfig',
  },
  run: {
    step: 4,
    titleKey: 'agent.skill.run',
    detailKey: 'agent.skill.runDetail',
  },
  'meta-evo': {
    step: 5,
    titleKey: 'agent.skill.metaEvo',
    detailKey: 'agent.skill.metaEvoDetail',
  },
}

const HOSTED_SUGGESTIONS = [
  { id: 'explain', labelKey: 'agent.suggest.explain', prompt: 'Explain what this SqurveBridge bundle contains and how the published methods relate to each other.' },
  { id: 'config', labelKey: 'agent.suggest.config', prompt: 'Walk me through one reproduce configuration in this bundle and what each Actor stage does.' },
  { id: 'evidence', labelKey: 'agent.suggest.evidence', prompt: 'Where should I look for published evidence or evaluation artifacts in this repository?' },
]

const LOCAL_SUGGESTIONS = [
  { id: 'candidate', labelKey: 'agent.suggest.candidate', skill: 'candidate-reader' },
  { id: 'pipeline', labelKey: 'agent.suggest.pipeline', skill: 'integration-pipeline' },
  { id: 'config', labelKey: 'agent.suggest.writeConfig', skill: 'config-adapter' },
]

// Prompt starters for the welcome screen. They only prefill the composer draft
// so the user can edit before sending; nothing is dispatched on click.
const LOCAL_EMPTY_CARDS = [
  { id: 'inspect', titleKey: 'agent.card.inspect', prompt: 'Inspect the current SqurveBridge integration and point out anything that needs attention.' },
  { id: 'integrate', titleKey: 'agent.card.integrate', prompt: 'Help me integrate a new Text-to-SQL method from a public GitHub repository.' },
  { id: 'reproduce', titleKey: 'agent.card.reproduce', prompt: 'Propose an experiment for the cells I selected in Compose with squrve_propose_action (start_experiment, 20 samples, slice). I will confirm it in the UI.' },
  { id: 'evaluate', titleKey: 'agent.card.evaluate', prompt: 'Read the comparison for the methods and benchmarks on this page with squrve_results, then explain which method does best where, the accuracy vs latency trade-offs, and which cells are missing or unaligned.' },
]

const HOSTED_EMPTY_CARDS = [
  { id: 'inspect', titleKey: 'agent.card.hostedExplain', prompt: HOSTED_SUGGESTIONS[0].prompt },
  { id: 'reproduce', titleKey: 'agent.card.hostedConfig', prompt: HOSTED_SUGGESTIONS[1].prompt },
  { id: 'evaluate', titleKey: 'agent.card.hostedEvidence', prompt: 'Read the recorded evidence with squrve_results (c3sql, dinsql, finsql on spider; 100 random samples, seed 42) and explain how the methods compare, including the questions only one of them gets right.' },
]

const FALLBACK = {
  'agent.connectModel': 'Connect a model',
  'agent.switchModel': 'Switch model',
  'agent.needModel': 'Connect a model to start',
  'agent.unavailable': 'Pi build unavailable',
  'agent.working': 'Pi is working',
  'agent.ready': 'Ready',
  'agent.connected': 'Connected',
  'agent.stopResponse': 'Stop response',
  'agent.endSession': 'End session',
  'agent.send': 'Send',
  'agent.placeholder': 'Type / for skills',
  'agent.placeholderNeedModel': 'Type / for skills — connect a model to send',
  'agent.emptyHosted': 'Ask about the published bundle, configs, or evidence.',
  'agent.emptyLocal': 'Ask Pi to inspect, integrate, reproduce, or evaluate.',
  'agent.emptyHint': 'Suggestions below get you started. Skills stay available when you need them.',
  'agent.emptyLocalDetail': 'Pi works inside this workspace — pick a direction below, or just describe your task.',
  'agent.emptyHostedDetail': 'Ask about the published bundle, reproduce configs, or public evidence.',
  'agent.card.inspect': 'Inspect integration',
  'agent.card.integrate': 'Integrate a method',
  'agent.card.reproduce': 'Reproduce a run',
  'agent.card.evaluate': 'Evaluate & compare',
  'agent.card.hostedExplain': 'Explain the bundle',
  'agent.card.hostedConfig': 'Walk through a config',
  'agent.card.hostedEvidence': 'Find evidence',
  'agent.you': 'You',
  'agent.pi': 'Pi',
  'agent.reasoning': 'Reasoning',
  'agent.toolArgs': 'Arguments',
  'agent.suggest.explain': 'Explain this bundle',
  'agent.suggest.config': 'Walk through a config',
  'agent.suggest.evidence': 'Find published evidence',
  'agent.suggest.candidate': 'Read a GitHub candidate',
  'agent.suggest.pipeline': 'Rebuild into Actors',
  'agent.suggest.writeConfig': 'Write a reproduce config',
  'agent.skills': 'Skills',
  'agent.closeSkills': 'Close',
  'agent.skill.candidateReader': 'Read candidate',
  'agent.skill.integrationPipeline': 'Rebuild Actors',
  'agent.skill.configAdapter': 'Write config',
  'agent.skill.run': 'Run evaluation',
  'agent.skill.metaEvo': 'Meta evolution',
  'agent.skill.runDetail': 'Launch or inspect an evaluation run through the project Skill.',
  'agent.skill.metaEvoDetail': 'Diagnose bottlenecks and propose safe component improvements.',
  'configure.agentSkillCandidate': 'Read a public GitHub candidate and draft an integration manifest.',
  'configure.agentSkillPipeline': 'Rebuild the candidate into Squrve-native Actor workflows.',
  'configure.agentSkillConfig': 'Emit a reproduce config that appears in the Studio catalog.',
  'agent.title': 'Pi Agent',
  'agent.subtitle': 'Chat with the SqurveBridge backend',
  'agent.idleDetail': 'Ask below to get started',
  'agent.sendToWorkspace': 'Send to Query workspace',
  'agent.sqlCopy': 'Copy',
  'agent.sqlCopied': 'Copied',
  'agent.sqlStreaming': 'Receiving SQL…',
}

function label(t, key) {
  if (typeof t === 'function') {
    const value = t(key)
    if (value && value !== key) return value
  }
  return FALLBACK[key] || key
}

export function MessageBody({ message, onAdoptSql, t }) {
  const [copiedIndex, setCopiedIndex] = useState(-1)
  // Parsing SQL fences is proportional to the message length; cache it so
  // streaming deltas in *other* messages never re-run it for settled ones.
  const segments = useMemo(() => extractSqlSegments(message.content || ''), [message.content])
  const hasSql = segments.some(segment => segment.type === 'sql')
  if (!hasSql) return <p>{message.content || (message.streaming ? '…' : '')}</p>

  const copySql = (sql, index) => {
    navigator.clipboard?.writeText(sql).catch(() => {})
    setCopiedIndex(index)
    setTimeout(() => setCopiedIndex(-1), 1500)
  }

  return (
    <div className="pi-message-body">
      {segments.map((segment, index) => segment.type === 'text'
        ? (segment.text.trim() ? <p key={index}>{segment.text}</p> : null)
        : (
          <div key={index} className="pi-sql-block" data-testid="pi-sql-block">
            <pre><code>{segment.sql}</code></pre>
            {segment.closed ? (
              <div className="pi-sql-actions">
                <button type="button" onClick={() => copySql(segment.sql, index)}>
                  {label(t, copiedIndex === index ? 'agent.sqlCopied' : 'agent.sqlCopy')}
                </button>
                {message.role === 'assistant' && onAdoptSql && (
                  <button
                    type="button"
                    className="pi-sql-adopt"
                    onClick={() => onAdoptSql(segment.sql)}
                  >
                    {label(t, 'agent.sendToWorkspace')}
                  </button>
                )}
              </div>
            ) : (
              <div className="pi-sql-actions is-streaming">{label(t, 'agent.sqlStreaming')}</div>
            )}
          </div>
        ))}
    </div>
  )
}

// Chat entries are memoized so a websocket text_delta only re-renders the one
// streaming message instead of the whole transcript. applyPiEvent keeps the
// object identity of untouched messages/tools, which makes these memos effective.
const ChatMessage = memo(function ChatMessage({ message, onAdoptSql, t }) {
  return (
    <article className={`pi-message ${message.role}`}>
      <header>
        <span>{message.role === 'user' ? label(t, 'agent.you') : 'π'}</span>
        {message.role === 'assistant' && <b>{message.streaming ? label(t, 'agent.working') : label(t, 'agent.pi')}</b>}
      </header>
      <div>
        {message.thinking && <details><summary>{label(t, 'agent.reasoning')}</summary><pre>{message.thinking}</pre></details>}
        <MessageBody message={message} onAdoptSql={onAdoptSql} t={t} />
      </div>
    </article>
  )
})

const ToolActivity = memo(function ToolActivity({ tool, Status, t }) {
  return (
    <div className={`pi-tool ${tool.status}`}>
      <div className="pi-tool-mark" aria-hidden="true">/</div>
      <span><strong>{tool.name}</strong><small>{label(t, 'agent.activity')}</small></span>
      <Status tone={tool.isError ? 'danger' : tool.status === 'running' ? 'running' : 'success'}>{tool.status}</Status>
      {tool.args && Object.keys(tool.args).length > 0 && <details className="pi-tool-args">
        <summary>{label(t, 'agent.toolArgs')}</summary>
        <code>{JSON.stringify(tool.args)}</code>
      </details>}
    </div>
  )
})

function AgentHarness({
  api,
  postJson,
  Status,
  candidateUrl = '',
  onCandidateReaderStart,
  onCandidateUrlRequired,
  queuedCommand = null,
  onQueuedCommandSent,
  embedded = false,
  shell = false,
  autoOpenAuth = false,
  onRequestNewChat,
  onAdoptSql,
  pageContext = null,
  onAgentAction,
  t,
}) {
  const socketRef = useRef(null)
  const pageContextRef = useRef(pageContext)
  const sessionRef = useRef(null)
  const handledCommandRef = useRef('')
  const autoAuthStartedRef = useRef(false)
  const autoAuthResolvedRef = useRef(false)
  const selectedModelKeyRef = useRef('')
  const endRef = useRef(null)
  const composerInputRef = useRef(null)
  const [catalog, setCatalog] = useState(null)
  const [chat, setChat] = useState(createPiChatState)
  const [auth, setAuth] = useState(createPiAuthState)
  const [authOpen, setAuthOpen] = useState(false)
  const [authCatalogReady, setAuthCatalogReady] = useState(false)
  const [draft, setDraft] = useState('')
  const [skillsOpen, setSkillsOpen] = useState(false)

  const receive = event => {
    try {
      const payload = JSON.parse(event.data)
      setChat(current => applyPiEvent(current, payload))
      setAuth(current => applyPiAuthEvent(current, payload))
      if (payload.type === 'auth_catalog' || payload.type === 'model_catalog') {
        setAuthCatalogReady(true)
      }
      if (payload.type === 'model_catalog') {
        const selected = payload.models?.find(model => model.selected)
        if (selected) setChat(current => ({ ...current, provider: selected.provider, model: selected.id }))
      }
    } catch {
      setChat(current => ({ ...current, status: 'error', error: 'Pi returned an invalid event.' }))
    }
  }

  const connect = session => {
    const existing = socketRef.current
    if (existing?.readyState === WebSocket.OPEN) return Promise.resolve(existing)
    if (existing?.readyState === WebSocket.CONNECTING) {
      return new Promise((resolve, reject) => {
        existing.addEventListener('open', () => resolve(existing), { once: true })
        existing.addEventListener('error', () => reject(new Error('Pi chat connection failed.')), { once: true })
      })
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/agent/sessions/${session.session_id}/ws`)
    socketRef.current = socket
    socket.onmessage = receive
    socket.onclose = () => {
      if (socketRef.current === socket) {
        socketRef.current = null
        setChat(current => current.status === 'stopped' ? current : { ...current, status: 'stopped' })
        setAuth(createPiAuthState())
        setAuthOpen(false)
      }
    }
    return new Promise((resolve, reject) => {
      socket.addEventListener('open', () => resolve(socket), { once: true })
      socket.addEventListener('error', () => reject(new Error('Pi chat connection failed.')), { once: true })
    })
  }

  const start = async () => {
    if (sessionRef.current?.running && socketRef.current?.readyState === WebSocket.OPEN) return sessionRef.current
    setChat(current => ({ ...current, status: 'starting', error: '' }))
    try {
      const session = await postJson('/api/agent/sessions', pageContextRef.current ? { context: pageContextRef.current } : {})
      sessionRef.current = session
      await connect(session)
      return session
    } catch (error) {
      setChat(current => ({ ...current, status: 'error', error: error.message }))
      return null
    }
  }

  // Keep the Agent's view of the page current: the next session starts with it,
  // and a live session hears about every change.
  useEffect(() => {
    pageContextRef.current = pageContext
    const socket = socketRef.current
    if (pageContext && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'context', context: pageContext }))
    }
  }, [JSON.stringify(pageContext)])

  const confirmProposal = async proposal => {
    setChat(current => resolveProposal(current, proposal.id, 'running'))
    try {
      await onAgentAction?.(proposal)
      setChat(current => resolveProposal(current, proposal.id, 'confirmed'))
    } catch (error) {
      setChat(current => ({ ...resolveProposal(current, proposal.id, 'failed'), error: error.message || '' }))
    }
  }

  const sendCommand = command => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) throw new Error('Pi chat connection is not ready.')
    socket.send(JSON.stringify(command))
  }

  const openAuth = async () => {
    let session = sessionRef.current
    if (!session?.running || socketRef.current?.readyState !== WebSocket.OPEN) session = await start()
    if (!session) return
    setAuthOpen(true)
  }

  const sendMessage = async (message, taskId = '') => {
    const normalized = message.trim()
    if (!normalized) return
    if (!auth.selectedModel) {
      await openAuth()
      return
    }
    let session = sessionRef.current
    if (!session?.running || socketRef.current?.readyState !== WebSocket.OPEN) session = await start()
    if (!session) return
    const socket = await connect(session)
    setChat(current => appendUserMessage(current, normalized))
    socket.send(JSON.stringify({ type: 'prompt', message: normalized }))
    setDraft('')
    if (taskId) {
      handledCommandRef.current = taskId
      onQueuedCommandSent?.(taskId, session)
    }
  }

  const stop = async () => {
    const session = sessionRef.current
    if (!session?.session_id) return
    try {
      await postJson(`/api/agent/sessions/${session.session_id}/stop`, {})
    } finally {
      sessionRef.current = null
      socketRef.current?.close()
      setChat(current => ({ ...current, status: 'stopped' }))
      setAuth(createPiAuthState())
      setAuthOpen(false)
    }
  }

  const abort = () => {
    if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: 'abort' }))
  }

  const useSkill = name => {
    if (name === 'candidate-reader' && (candidateUrl || onCandidateUrlRequired || onCandidateReaderStart)) {
      if (!candidateUrl) {
        onCandidateUrlRequired?.()
        return
      }
      if (onCandidateReaderStart) {
        onCandidateReaderStart()
        return
      }
    }
    sendMessage(skillPrompt(name)).catch(error => setChat(current => ({ ...current, error: error.message })))
  }

  const runSuggestion = suggestion => {
    if (suggestion.skill) {
      useSkill(suggestion.skill)
      return
    }
    sendMessage(suggestion.prompt).catch(error => setChat(current => ({ ...current, error: error.message })))
  }

  const prefillDraft = prompt => {
    setDraft(prompt)
    composerInputRef.current?.focus()
  }

  // Grows the prompt row with the draft. The CSS `field-sizing: content` would do
  // this without script, but Firefox and older Safari still lack it and a public
  // demo cannot assume the visitor's engine, so one measured height serves every
  // browser. The stylesheet's max-height is what stops the growth.
  const fitComposerInput = () => {
    const input = composerInputRef.current
    if (!input) return
    input.style.height = 'auto'
    if (input.scrollHeight > 0) input.style.height = `${input.scrollHeight}px`
  }

  useLayoutEffect(fitComposerInput, [draft])

  // Rewrapping the same draft into a narrower pane changes the line count, and
  // dragging the shell divider fires no window resize. Without ResizeObserver
  // (jsdom) the row keeps whatever height the last keystroke measured.
  useEffect(() => {
    const input = composerInputRef.current
    if (!input || typeof ResizeObserver === 'undefined') return undefined
    let width = input.clientWidth
    const observer = new ResizeObserver(() => {
      // Reacting to the height this writes back would loop, so only width counts.
      if (input.clientWidth === width) return
      width = input.clientWidth
      fitComposerInput()
    })
    observer.observe(input)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let active = true
    api('/api/agent').then(data => {
      if (!active) return
      const catalogData = data && typeof data === 'object' ? data : { available: false }
      setCatalog(catalogData)
      setChat(current => ({
        ...current,
        profile: catalogData.profile || '',
        provider: catalogData.provider || '',
        model: catalogData.model || '',
        skills: Array.isArray(catalogData.skills) ? catalogData.skills : [],
      }))
    }).catch(error => setChat(current => ({ ...current, status: 'error', error: error.message })))
    return () => {
      active = false
      const session = sessionRef.current
      socketRef.current?.close()
      if (session?.running) fetch(`/api/agent/sessions/${session.session_id}/stop`, { method: 'POST', keepalive: true }).catch(() => {})
    }
  }, [api])

  useEffect(() => {
    if (!queuedCommand?.id || handledCommandRef.current === queuedCommand.id) return
    sendMessage(queuedCommand.command, queuedCommand.id).catch(error => setChat(current => ({ ...current, error: error.message })))
  }, [queuedCommand?.id, auth.selectedModel?.provider, auth.selectedModel?.id])

  useEffect(() => {
    if (!autoOpenAuth || autoAuthStartedRef.current || !catalog || catalog.available === false) return
    if (auth.selectedModel) {
      autoAuthStartedRef.current = true
      autoAuthResolvedRef.current = true
      return
    }
    autoAuthStartedRef.current = true
    start().catch(error => setChat(current => ({ ...current, error: error.message })))
  }, [autoOpenAuth, catalog, auth.selectedModel])

  useEffect(() => {
    if (!autoOpenAuth || !autoAuthStartedRef.current || autoAuthResolvedRef.current || !authCatalogReady) return
    autoAuthResolvedRef.current = true
    if (auth.selectedModel) {
      setAuthOpen(false)
      return
    }
    const configured = auth.models.find(model => model.configured)
    if (configured) {
      try {
        sendCommand({
          type: 'model_select',
          provider: configured.provider,
          model: configured.id,
        })
        setAuthOpen(false)
      } catch {
        setAuthOpen(true)
      }
      return
    }
    setAuthOpen(true)
  }, [autoOpenAuth, authCatalogReady, auth.selectedModel, auth.models])

  useEffect(() => {
    const key = auth.selectedModel
      ? `${auth.selectedModel.provider}/${auth.selectedModel.id}`
      : ''
    const previous = selectedModelKeyRef.current
    selectedModelKeyRef.current = key
    if (key && key !== previous) setAuthOpen(false)
  }, [auth.selectedModel])

  const scrollPendingRef = useRef(false)
  useEffect(() => {
    // Streaming deltas arrive faster than frames; coalesce auto-scrolls to at
    // most one per animation frame instead of queueing a smooth scroll per delta.
    if (scrollPendingRef.current) return
    scrollPendingRef.current = true
    const flush = () => {
      scrollPendingRef.current = false
      endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
    }
    if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(flush)
    else flush()
  }, [chat.messages, chat.tools])

  const hosted = chat.profile === 'hosted-readonly'
  const skills = chat.skills.length
    ? chat.skills.filter(name => DEFAULT_SKILLS.includes(name))
    : DEFAULT_SKILLS
  const running = ['starting', 'thinking', 'ready'].includes(chat.status) && Boolean(sessionRef.current)
  const busy = chat.status === 'thinking'
  const authenticated = Boolean(auth.selectedModel)
  const modelLabel = authenticated ? `${auth.selectedModel.provider}/${auth.selectedModel.id}` : ''
  const statusTone = chat.error || auth.error ? 'danger' : busy ? 'running' : authenticated ? 'success' : 'neutral'
  const statusLabel = busy
    ? label(t, 'agent.working')
    : authenticated
      ? label(t, 'agent.connected')
      : catalog?.available === false
        ? label(t, 'agent.unavailable')
        : label(t, 'agent.needModel')
  const sessionDetail = authenticated
    ? modelLabel
    : catalog?.available === false
      ? label(t, `agent.unavailable.${catalog.unavailable_reason || 'unknown'}`)
      : embedded
        ? label(t, 'agent.idleDetail')
        : label(t, 'agent.subtitle')
  const suggestions = hosted ? HOSTED_SUGGESTIONS : LOCAL_SUGGESTIONS
  const composerDisabled = busy || catalog?.available === false
  const sendDisabled = composerDisabled || !draft.trim()

  const catchSend = promise => promise.catch(error => setChat(current => ({ ...current, error: error.message })))

  const resetChat = async () => {
    await stop()
    setChat(current => ({
      ...createPiChatState(),
      profile: current.profile,
      provider: current.provider,
      model: current.model,
      skills: current.skills,
    }))
    setDraft('')
    onRequestNewChat?.()
  }

  const isEmpty = chat.messages.length === 0
  const shellClass = [
    'tool-panel agent-harness pi-chat',
    embedded ? 'agent-harness-embedded' : '',
    shell ? 'agent-harness-shell' : '',
    shell && isEmpty ? 'is-empty' : '',
  ].filter(Boolean).join(' ')

  // New chat already stops the running session, so the shell offers it alone
  // rather than two adjacent teardown buttons.
  const toolbar = shell && !isEmpty ? (
    <div className="agent-shell-toolbar">
      <Status tone={statusTone}>{statusLabel}</Status>
      {busy && <button type="button" onClick={abort}>{label(t, 'agent.stopResponse')}</button>}
      <button type="button" onClick={() => { resetChat().catch(() => {}) }}>{label(t, 'shell.newChat')}</button>
    </div>
  ) : null

  // A flat, step-ordered list: five skills did not need three group headers,
  // numbered badges and a command echo on top of the name and what it does.
  const skillEntries = skills
    .map(name => ({ name, meta: SKILL_META[name] || { step: 99, titleKey: name, detailKey: name } }))
    .sort((left, right) => left.meta.step - right.meta.step)

  const shortcuts = skillEntries.length > 0 ? (
    <div className="harness-shortcuts pi-skills-list" role="list">
      {skillEntries.map(({ name, meta }) => (
        <button
          key={name}
          type="button"
          role="listitem"
          className={[
            'pi-skills-item',
            name === 'candidate-reader' && candidateUrl === '' && onCandidateUrlRequired ? 'needs-input' : '',
          ].filter(Boolean).join(' ')}
          onClick={() => {
            setSkillsOpen(false)
            useSkill(name)
          }}
        >
          <strong>{meta.titleKey === name ? name : label(t, meta.titleKey)}</strong>
          <span>{meta.detailKey === name ? `/skill:${name}` : label(t, meta.detailKey)}</span>
        </button>
      ))}
    </div>
  ) : null

  const chatLog = (
    <div className="pi-chat-log" aria-live="polite">
      {chat.messages.length > 0 && <div className="pi-chat-date">{label(t, 'agent.today')}</div>}
      {!chat.messages.length && <div className="pi-chat-empty">
        {shell ? (
          <>
            <span>{hosted ? label(t, 'agent.emptyHostedDetail') : label(t, 'agent.emptyLocalDetail')}</span>
            <div className="pi-chat-hero-cards" data-testid="agent-empty-cards">
              {(hosted ? HOSTED_EMPTY_CARDS : LOCAL_EMPTY_CARDS).map(card => (
                <button
                  key={card.id}
                  type="button"
                  className="pi-chat-hero-card"
                  onClick={() => prefillDraft(card.prompt)}
                >
                  {label(t, card.titleKey)}
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <b>{hosted ? label(t, 'agent.emptyHosted') : label(t, 'agent.emptyLocal')}</b>
            <span>{label(t, 'agent.emptyHint')}</span>
            <div className="pi-chat-suggestions">
              {suggestions.map(item => <button key={item.id} type="button" disabled={composerDisabled} onClick={() => runSuggestion(item)}>
                {label(t, item.labelKey)}
              </button>)}
            </div>
          </>
        )}
      </div>}
      {chat.messages.map((message, index) => (
        <ChatMessage
          key={`${message.role}-${index}`}
          message={message}
          onAdoptSql={onAdoptSql}
          t={t}
        />
      ))}
      {chat.tools.slice(-8).map(tool => (
        <ToolActivity key={tool.id} tool={tool} Status={Status} t={t} />
      ))}
      {(chat.proposals || []).map(proposal => (
        <div key={proposal.id} className="pi-proposal" data-status={proposal.status} data-testid="agent-proposal">
          <span>{label(t, 'agent.proposalEyebrow')}</span>
          <strong>{label(t, `agent.action.${proposal.action}`)}</strong>
          {proposal.reason && <p>{proposal.reason}</p>}
          <code>{JSON.stringify(proposal.args)}</code>
          {proposal.status === 'pending' ? (
            <div className="pi-proposal-actions">
              <button type="button" className="button primary" onClick={() => confirmProposal(proposal)}>
                {label(t, 'agent.proposalConfirm')}
              </button>
              <button type="button" className="button secondary" onClick={() => setChat(current => resolveProposal(current, proposal.id, 'dismissed'))}>
                {label(t, 'agent.proposalDismiss')}
              </button>
            </div>
          ) : (
            <small>{label(t, `agent.proposal.${proposal.status}`)}</small>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )

  const composer = (
    <form className="pi-chat-composer" onSubmit={event => { event.preventDefault(); catchSend(sendMessage(draft)) }}>
      {shell ? <div className="pi-chat-composer-inner">
        {skillsOpen && skills.length > 0 && (
          <div className="pi-composer-skills" data-testid="agent-skills-menu" role="dialog" aria-label={label(t, 'agent.skills')}>
            <div className="pi-composer-skills-head">
              <b>{label(t, 'agent.skills')}</b>
              <button type="button" onClick={() => setSkillsOpen(false)}>{label(t, 'agent.closeSkills')}</button>
            </div>
            {shortcuts}
          </div>
        )}
        <div className="pi-chat-composer-field">
          <textarea
            ref={composerInputRef}
            value={draft}
            disabled={composerDisabled}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Escape' && skillsOpen) {
                event.preventDefault()
                setSkillsOpen(false)
                return
              }
              if (event.key === '/' && !event.nativeEvent.isComposing && !draft && skills.length > 0) {
                event.preventDefault()
                setSkillsOpen(true)
                return
              }
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                catchSend(sendMessage(draft))
              }
            }}
            placeholder={label(t, authenticated ? 'agent.placeholder' : 'agent.placeholderNeedModel')}
            rows="1"
            aria-label={label(t, 'agent.placeholder')}
          />
          <div className="pi-chat-composer-bar">
            {skills.length > 0 && (
              <button
                type="button"
                className="pi-chat-skill-chip"
                aria-expanded={skillsOpen}
                aria-label={label(t, 'agent.skills')}
                onClick={() => setSkillsOpen(value => !value)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <path d="M14.6 4.8 9.4 19.2" />
                </svg>
                {label(t, 'agent.skills')}
              </button>
            )}
            <button
              type="button"
              className="pi-chat-model-pill"
              disabled={catalog?.available === false}
              title={authenticated ? modelLabel : undefined}
              onClick={openAuth}
            >
              {authenticated ? auth.selectedModel.id : label(t, 'agent.connectModel')}
            </button>
            <button className="pi-chat-send" disabled={sendDisabled} type="submit" aria-label={label(t, 'agent.send')}>
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M12 19V5M6.5 10.5 12 5l5.5 5.5" />
              </svg>
            </button>
          </div>
        </div>
      </div> : <>
        <textarea
          value={draft}
          disabled={composerDisabled}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              catchSend(sendMessage(draft))
            }
          }}
          placeholder={authenticated ? label(t, 'agent.placeholder') : label(t, 'agent.placeholderNeedModel')}
          rows="3"
        />
        <button className="button primary" disabled={sendDisabled} type="submit">{label(t, 'agent.send')}</button>
      </>}
    </form>
  )

  return <section className={shellClass} data-testid={shell ? 'agent-shell-chat' : undefined}>
    {!shell && <div className={`agent-harness-head${embedded ? ' agent-harness-head-compact' : ''}`}>
      <div>
        {!embedded && <strong>{label(t, 'agent.title')}</strong>}
        <span className="agent-session-detail">{sessionDetail}</span>
      </div>
      <div>
        <Status tone={statusTone}>{statusLabel}</Status>
        {busy && <button className="button agent-cancel" type="button" onClick={abort}>{label(t, 'agent.stopResponse')}</button>}
        <button className="button primary" type="button" disabled={catalog?.available === false} onClick={openAuth}>
          {authenticated ? label(t, 'agent.switchModel') : label(t, 'agent.connectModel')}
        </button>
        {running && <button className="button secondary" type="button" onClick={stop}>{label(t, 'agent.endSession')}</button>}
      </div>
    </div>}

    {shell ? (
      <>
        <div className="agent-shell-scroll" data-testid="agent-shell-scroll">
          {toolbar}
          {chatLog}
          <div className="agent-shell-scroll-spacer" aria-hidden="true" />
        </div>
        {composer}
      </>
    ) : (
      <>
        {shortcuts}
        {chatLog}
        {composer}
      </>
    )}
    {chat.error && <p className="error-banner">{chat.error}</p>}
    <PiAuthDialog open={authOpen} state={auth} send={sendCommand} onClose={() => setAuthOpen(false)} t={t} embedded={embedded || shell} />
  </section>
}

// Memoized so shell-level re-renders (pane resize/collapse, run-state polling)
// skip the chat tree entirely when its props are unchanged.
export default memo(AgentHarness)
