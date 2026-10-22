import path from 'node:path'
import { realpath } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

export function parseBridgeArgs(argv) {
  const values = {}
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (!key?.startsWith('--') || value === undefined) throw new Error(`Invalid Pi bridge argument: ${key || '<empty>'}`)
    values[key.slice(2)] = value
  }
  const tools = JSON.parse(values.tools || '[]')
  if (!values.cwd || !Array.isArray(tools) || tools.some(tool => typeof tool !== 'string')) {
    throw new Error('Pi bridge requires --cwd and a JSON array in --tools')
  }
  return {
    cwd: values.cwd,
    profile: values.profile || 'local-full',
    tools,
    provider: values.provider || null,
    model: values.model || null,
    apiBase: values['api-base'] || null,
  }
}

export function eventToWire(event) {
  if (event.type === 'message_update') {
    const delta = event.assistantMessageEvent
    if (delta?.type === 'text_delta') return { type: 'text_delta', delta: delta.delta }
    if (delta?.type === 'thinking_delta') return { type: 'thinking_delta', delta: delta.delta }
    return null
  }
  if (event.type === 'tool_execution_start') {
    return {
      type: 'tool_start',
      tool_call_id: event.toolCallId,
      tool_name: event.toolName,
      args: event.args,
    }
  }
  if (event.type === 'tool_execution_update') {
    return {
      type: 'tool_update',
      tool_call_id: event.toolCallId,
      tool_name: event.toolName,
      partial_result: event.partialResult,
    }
  }
  if (event.type === 'tool_execution_end') {
    return {
      type: 'tool_end',
      tool_call_id: event.toolCallId,
      tool_name: event.toolName,
      is_error: event.isError,
      result: event.result,
    }
  }
  if (['agent_start', 'agent_end', 'agent_settled', 'turn_start', 'turn_end'].includes(event.type)) {
    return { type: event.type }
  }
  return null
}

function writeEvent(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`)
}

// The parent process owns the event pipe; when it goes away there is nobody
// left to talk to, so exit quietly instead of crashing with an EPIPE stack.
export function exitOnLostEventPipe(error) {
  if (error?.code === 'EPIPE' || error?.code === 'ERR_STREAM_DESTROYED') {
    process.exit(0)
    return
  }
  throw error
}

process.stdout.on('error', exitOnLostEventPipe)

export function workspaceAgentDir(config) {
  const workspaceRoot = process.env.SQURVE_WORKSPACE_DIR
    ? path.resolve(process.env.SQURVE_WORKSPACE_DIR)
    : path.join(config.cwd, 'workspace')
  return path.join(workspaceRoot, 'sessions', 'pi-agent')
}

export function resourceLoaderOptions(config) {
  return {
    cwd: config.cwd,
    agentDir: workspaceAgentDir(config),
    additionalSkillPaths: [path.join(config.cwd, 'skills')],
    noSkills: true,
    noExtensions: true,
    appendSystemPrompt: [
      `You are the embedded Pi backend for SqurveBridge. Runtime profile: ${config.profile}.`,
      config.profile === 'hosted-readonly'
        ? 'This is a public read-only demo. Never claim to write files or execute commands.'
        : 'This is a trusted local coding session. Follow repository AGENTS.md and skill contracts.',
    ],
  }
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate)
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
}

export async function assertConfinedPath(root, requested = '.') {
  const canonicalRoot = await realpath(root)
  const absolute = path.resolve(root, requested || '.')
  if (!isWithin(root, absolute)) throw new Error('Requested path is outside the SqurveBridge project.')
  let canonicalCandidate
  try {
    canonicalCandidate = await realpath(absolute)
  } catch (error) {
    if (error?.code === 'ENOENT') return absolute
    throw error
  }
  if (!isWithin(canonicalRoot, canonicalCandidate)) {
    throw new Error('Requested path is outside the SqurveBridge project.')
  }
  return absolute
}

export function createHostedTools(sdk, config) {
  const factories = {
    read: sdk.createReadToolDefinition,
    grep: sdk.createGrepToolDefinition,
    find: sdk.createFindToolDefinition,
    ls: sdk.createLsToolDefinition,
  }
  return config.tools.map(name => {
    const factory = factories[name]
    if (!factory) throw new Error(`Hosted Pi tool is not read-only: ${name}`)
    const tool = factory(config.cwd)
    return {
      ...tool,
      async execute(toolCallId, params, signal, onUpdate, context) {
        await assertConfinedPath(config.cwd, params?.path || '.')
        return tool.execute(toolCallId, params, signal, onUpdate, context)
      },
    }
  })
}

// --- SqurveBridge Demo tools ---------------------------------------------------
// The Agent reads results and experiment state through the Demo API, and may
// only *propose* UI actions; the browser runs one only after the user confirms.

const SAFE_ID = /^[A-Za-z0-9._-]{1,128}$/
const RESULT_TEXT_LIMIT = 12000
const OPEN_ACTIONS = ['open_experiment', 'open_cell', 'open_query']
const LOCAL_ACTIONS = ['start_experiment', ...OPEN_ACTIONS]
let proposalSequence = 0

const textResult = (text, details = {}) => ({ content: [{ type: 'text', text }], details })

async function readApi(fetchImpl, url) {
  const response = await fetchImpl(url)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) return textResult(`Demo API error ${response.status}: ${body?.message || 'request failed'}`)
  const text = JSON.stringify(body)
  return textResult(text.length > RESULT_TEXT_LIMIT ? `${text.slice(0, RESULT_TEXT_LIMIT)}… (truncated)` : text)
}

const idList = value => (Array.isArray(value) ? value : String(value || '').split(','))
  .map(item => String(item).trim())
  .filter(Boolean)

export function createSqurveTools({ apiBase, profile, emit, fetchImpl = globalThis.fetch }) {
  const base = String(apiBase || '').replace(/\/$/, '')
  const hosted = profile === 'hosted-readonly'
  const allowedActions = hosted ? OPEN_ACTIONS : LOCAL_ACTIONS

  const results = {
    name: 'squrve_results',
    label: 'SqurveBridge results',
    description: 'Read SqurveBridge comparison results. Give methods and datasets (reproduce config ids such as c3sql, spider) '
      + 'for the method × benchmark matrix of metric values, cost and sample alignment; optionally experiment_id for one '
      + 'experiment, or sample_limit/sample_mode/sample_seed for archived runs (recorded evidence uses 100/random/42). '
      + 'Give diff_a and diff_b run ids to compare two runs question by question instead.',
    parameters: {
      type: 'object',
      properties: {
        methods: { type: 'array', items: { type: 'string' } },
        datasets: { type: 'array', items: { type: 'string' } },
        experiment_id: { type: 'string' },
        sample_limit: { type: 'number' },
        sample_mode: { type: 'string', enum: ['slice', 'random'] },
        sample_seed: { type: 'number' },
        diff_a: { type: 'string' },
        diff_b: { type: 'string' },
      },
    },
    async execute(_id, params = {}) {
      if (params.diff_a || params.diff_b) {
        if (!SAFE_ID.test(String(params.diff_a || '')) || !SAFE_ID.test(String(params.diff_b || ''))) {
          return textResult('Invalid run ids for a diff.')
        }
        const query = new URLSearchParams({ a: params.diff_a, b: params.diff_b, limit: '50' })
        return readApi(fetchImpl, `${base}/api/results/diff?${query}`)
      }
      const methods = idList(params.methods)
      const datasets = idList(params.datasets)
      if (!methods.length || !datasets.length || ![...methods, ...datasets].every(item => SAFE_ID.test(item))) {
        return textResult('Invalid request: give methods and datasets as simple ids.')
      }
      const query = new URLSearchParams({ methods: methods.join(','), datasets: datasets.join(',') })
      if (params.experiment_id) {
        if (!SAFE_ID.test(String(params.experiment_id))) return textResult('Invalid experiment id.')
        query.set('experiment_id', params.experiment_id)
      } else {
        query.set('sample_limit', String(params.sample_limit ?? 100))
        query.set('sample_mode', String(params.sample_mode ?? 'random'))
        query.set('sample_seed', String(params.sample_seed ?? 42))
      }
      return readApi(fetchImpl, `${base}/api/results/matrix?${query}`)
    },
  }

  const status = {
    name: 'squrve_experiment_status',
    label: 'SqurveBridge experiment status',
    description: 'Read the status of one experiment (experiment_id) or list this session\'s experiments: per-cell status, progress, queue position and error codes.',
    parameters: { type: 'object', properties: { experiment_id: { type: 'string' } } },
    async execute(_id, params = {}) {
      if (params.experiment_id === undefined || params.experiment_id === '') {
        return readApi(fetchImpl, `${base}/api/experiments`)
      }
      if (!SAFE_ID.test(String(params.experiment_id))) return textResult('Invalid experiment id.')
      return readApi(fetchImpl, `${base}/api/experiments/${params.experiment_id}`)
    },
  }

  const propose = {
    name: 'squrve_propose_action',
    label: 'Propose a UI action',
    description: `Propose an action for the user to confirm in the SqurveBridge UI; nothing runs until they click. Allowed: ${allowedActions.join(', ')}. `
      + 'start_experiment args: { cells: [{ method, dataset }], sampling: { limit, mode, seed } }. '
      + 'open_experiment: { experiment_id }. open_cell: { method, dataset }. open_query: { db_id, question, method, dataset }.',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', enum: allowedActions },
        args: { type: 'object' },
        reason: { type: 'string' },
      },
      required: ['action'],
    },
    async execute(_id, params = {}) {
      const action = String(params.action || '')
      if (!allowedActions.includes(action)) {
        return textResult(`Action ${action || '(none)'} is not allowed here. Allowed: ${allowedActions.join(', ')}.`)
      }
      proposalSequence += 1
      emit({
        type: 'ui_action',
        id: `proposal-${Date.now()}-${proposalSequence}`,
        action,
        args: params.args && typeof params.args === 'object' ? params.args : {},
        reason: params.reason ? String(params.reason).slice(0, 500) : null,
      })
      return textResult(`Proposed ${action}. It runs only if the user confirms it in the UI; do not assume it happened.`)
    },
  }

  return hosted ? [results, propose] : [results, status, propose]
}

const CONTEXT_LIMIT = 4096

/** Append the current page context; a leading /skill: command stays first. */
export function withPageContext(message, context) {
  if (!context) return message
  const text = JSON.stringify(context)
  if (!text || text === '{}' || text.length > CONTEXT_LIMIT) return message
  return `${message}\n\n<squrve-page-context>${text}</squrve-page-context>`
}

export function createInMemoryCredentialStore() {
  const data = new Map()
  return {
    async read(providerId) {
      return data.get(providerId)
    },
    async list() {
      return [...data.entries()].map(([providerId, credential]) => ({ providerId, type: credential.type }))
    },
    async modify(providerId, fn) {
      const next = await fn(data.get(providerId))
      if (next !== undefined) data.set(providerId, next)
      return data.get(providerId)
    },
    async delete(providerId) {
      data.delete(providerId)
    },
  }
}

export async function createBridgeRuntime(sdk, config) {
  if (config.profile === 'hosted-readonly') {
    const modelRuntime = await sdk.ModelRuntime.create({
      credentials: createInMemoryCredentialStore(),
      modelsPath: null,
      refreshOnCreate: false,
    })
    return {
      modelRuntime,
      modelRegistry: new sdk.ModelRegistry(modelRuntime),
      settingsManager: sdk.SettingsManager.inMemory(),
    }
  }
  const modelRuntime = await sdk.ModelRuntime.create({
    modelsPath: path.join(config.cwd, 'config', 'pi_models.json'),
    modelsStorePath: path.join(workspaceAgentDir(config), 'models-store.json'),
  })
  return {
    modelRuntime,
    modelRegistry: new sdk.ModelRegistry(modelRuntime),
    settingsManager: null,
  }
}

class AuthCancelledError extends Error {}

export function createPiAuthProtocol({ modelRuntime, modelRegistry, session, emit, initialModel = null }) {
  let activeModel = initialModel
  let pendingPrompt = null
  let promptSequence = 0
  let authAbortController = null
  let authRunning = false
  let pageContext = null

  const providerIds = () => [...new Set(modelRegistry.getAll().map(model => model.provider))]
  const supportsSubscription = provider => Boolean(modelRuntime.getProvider(provider)?.auth?.oauth)

  async function providerCatalog() {
    const credentials = new Map(
      (await modelRuntime.listCredentials()).map(info => [info.providerId, info.type]),
    )
    return providerIds().sort().map(provider => {
      const methods = ['api_key']
      if (supportsSubscription(provider)) methods.push('subscription')
      return {
        id: provider,
        name: modelRegistry.getProviderDisplayName(provider),
        auth_methods: methods,
        configured: Boolean(modelRegistry.getProviderAuthStatus(provider).configured),
        credential_type: credentials.get(provider) || null,
      }
    })
  }

  function modelCatalog() {
    const registered = modelRegistry.getAll()
    const models = activeModel && !registered.some(model => (
      model.provider === activeModel.provider && model.id === activeModel.id
    )) ? [...registered, activeModel] : registered
    return models.map(model => ({
      provider: model.provider,
      id: model.id,
      name: model.name || model.id,
      configured: Boolean(modelRegistry.hasConfiguredAuth(model)),
      selected: activeModel?.provider === model.provider && activeModel?.id === model.id,
    }))
  }

  async function emitCatalogs() {
    const providers = await providerCatalog()
    emit({ type: 'auth_catalog', providers })
    emit({
      type: 'auth_status',
      providers: providers.map(({ id, configured, credential_type }) => ({ id, configured, credential_type })),
    })
    emit({ type: 'model_catalog', models: modelCatalog() })
  }

  function askBrowser(kind, prompt) {
    if (pendingPrompt) throw new Error('Pi authentication already has a pending prompt')
    const requestId = `auth-${++promptSequence}`
    emit({
      type: 'auth_prompt',
      request_id: requestId,
      kind,
      message: prompt.message,
      placeholder: prompt.placeholder || null,
      options: prompt.options || null,
    })
    return new Promise((resolve, reject) => {
      pendingPrompt = { requestId, resolve, reject }
    })
  }

  function loginInteraction() {
    return {
      signal: authAbortController.signal,
      prompt(prompt) {
        return askBrowser(prompt.type, prompt)
      },
      notify(event) {
        if (event.type === 'auth_url') {
          emit({
            type: 'auth_event',
            event: 'auth_url',
            url: event.url,
            instructions: event.instructions || null,
          })
          return
        }
        if (event.type === 'device_code') {
          emit({
            type: 'auth_event',
            event: 'device_code',
            user_code: event.userCode,
            verification_uri: event.verificationUri,
            interval_seconds: event.intervalSeconds || null,
            expires_in_seconds: event.expiresInSeconds || null,
          })
          return
        }
        emit({ type: 'auth_event', event: 'progress', message: event.message })
      },
    }
  }

  async function startAuth(command) {
    if (authRunning) {
      emit({ type: 'auth_error', code: 'auth_in_progress', message: 'A Pi login is already in progress.' })
      return
    }
    const provider = String(command.provider || '')
    const method = String(command.method || '')
    if (!providerIds().includes(provider)) {
      emit({ type: 'auth_error', code: 'unsupported_provider', message: 'The selected Pi provider is unsupported.' })
      return
    }
    authRunning = true
    authAbortController = new AbortController()
    try {
      if (method === 'api_key') {
        await modelRuntime.login(provider, 'api_key', loginInteraction())
      } else if (method === 'subscription') {
        if (!supportsSubscription(provider)) throw new Error('Subscription login is unavailable for this provider')
        await modelRuntime.login(provider, 'oauth', loginInteraction())
      } else {
        throw new Error('Unsupported Pi authentication method')
      }
      await emitCatalogs()
      emit({ type: 'auth_complete', provider, method, status: 'authenticated' })
    } catch (error) {
      if (error instanceof AuthCancelledError || authAbortController?.signal.aborted) {
        await modelRuntime.logout(provider).catch(() => {})
        await emitCatalogs()
        emit({ type: 'auth_complete', provider, method, status: 'cancelled' })
      } else {
        emit({ type: 'auth_error', code: 'authentication_failed', message: 'Pi authentication failed.' })
      }
    } finally {
      authAbortController = null
      authRunning = false
    }
  }

  async function handle(command) {
    if (command.type === 'auth_start') {
      await startAuth(command)
      return
    }
    if (command.type === 'auth_prompt_response') {
      if (!pendingPrompt || command.request_id !== pendingPrompt.requestId) {
        emit({ type: 'auth_error', code: 'invalid_auth_prompt', message: 'The Pi login prompt is no longer active.' })
        return
      }
      const { resolve } = pendingPrompt
      pendingPrompt = null
      resolve(String(command.value ?? ''))
      return
    }
    if (command.type === 'auth_cancel') {
      authAbortController?.abort()
      if (pendingPrompt) {
        const { reject } = pendingPrompt
        pendingPrompt = null
        reject(new AuthCancelledError('Pi authentication cancelled'))
      }
      return
    }
    if (command.type === 'model_select') {
      const provider = String(command.provider || '')
      const modelId = String(command.model || '').trim()
      const registeredModel = modelRegistry.find(provider, modelId)
      const providerTemplate = modelRegistry.getAll().find(model => model.provider === provider)
      const model = registeredModel || (modelId && providerTemplate
        ? { ...providerTemplate, id: modelId, name: modelId }
        : null)
      if (!model) {
        emit({ type: 'auth_error', code: 'unsupported_model', message: 'The selected Pi model is unsupported.' })
        return
      }
      if (!modelRegistry.hasConfiguredAuth(model)) {
        emit({ type: 'auth_error', code: 'auth_required', message: 'Authenticate this Pi provider before selecting its model.' })
        return
      }
      await session.setModel(model)
      activeModel = model
      emit({ type: 'model_catalog', models: modelCatalog() })
      return
    }
    if (command.type === 'logout') {
      const provider = String(command.provider || '')
      await modelRuntime.logout(provider).catch(() => {})
      if (activeModel?.provider === provider) activeModel = null
      await emitCatalogs()
      emit({ type: 'auth_complete', provider, method: 'logout', status: 'logged_out' })
      return
    }
    if (command.type === 'prompt') {
      if (!activeModel || !modelRegistry.hasConfiguredAuth(activeModel)) {
        emit({ type: 'auth_error', code: 'auth_required', message: 'Select an authenticated Pi model before chatting.' })
        return
      }
      emit({ type: 'prompt_accepted' })
      await session.prompt(withPageContext(String(command.message || ''), pageContext))
      return
    }
    if (command.type === 'context') {
      pageContext = command.context && typeof command.context === 'object' ? command.context : null
      return
    }
    if (command.type === 'abort') {
      await session.abort()
      emit({ type: 'aborted' })
      return
    }
    throw new Error(`Unsupported Pi bridge command: ${command.type}`)
  }

  function dispose() {
    authAbortController?.abort()
    if (pendingPrompt) {
      pendingPrompt.reject(new AuthCancelledError('Pi bridge stopped'))
      pendingPrompt = null
    }
  }

  return { emitCatalogs, handle, dispose }
}

export async function runBridge(argv = process.argv.slice(2)) {
  const config = parseBridgeArgs(argv)
  let sdk
  try {
    sdk = await import('@earendil-works/pi-coding-agent')
  } catch (error) {
    throw new Error(`The embedded Pi SDK is not installed. Run npm ci --prefix ${path.join(config.cwd, 'demo')}: ${error.message}`)
  }

  const { modelRuntime, modelRegistry, settingsManager } = await createBridgeRuntime(sdk, config)
  const resourceLoader = new sdk.DefaultResourceLoader(resourceLoaderOptions(config))
  await resourceLoader.reload()

  let model = null
  if (config.profile !== 'hosted-readonly') {
    if (config.provider && config.model) model = modelRegistry.find(config.provider, config.model)
    if (!model) model = modelRegistry.getAvailable()[0]
  }

  const hostedTools = config.profile === 'hosted-readonly' ? createHostedTools(sdk, config) : null
  const squrveTools = config.apiBase
    ? createSqurveTools({ apiBase: config.apiBase, profile: config.profile, emit: writeEvent })
    : []
  const customTools = [...(hostedTools || []), ...squrveTools]
  const { session } = await sdk.createAgentSession({
    cwd: config.cwd,
    model,
    tools: [...config.tools, ...squrveTools.map(tool => tool.name)],
    noTools: hostedTools ? 'builtin' : undefined,
    customTools: customTools.length ? customTools : undefined,
    resourceLoader,
    modelRuntime,
    settingsManager: settingsManager || undefined,
    sessionManager: sdk.SessionManager.inMemory(config.cwd),
  })
  const unsubscribe = session.subscribe(event => {
    const wire = eventToWire(event)
    if (wire) writeEvent(wire)
  })
  const { skills, diagnostics } = resourceLoader.getSkills()
  writeEvent({
    type: 'ready',
    backend: 'pi',
    profile: config.profile,
    provider: model?.provider || null,
    model: model?.id || null,
    skills: skills.map(skill => skill.name),
    diagnostics: diagnostics.map(item => item.message),
  })
  const protocol = createPiAuthProtocol({
    modelRuntime,
    modelRegistry,
    session,
    emit: writeEvent,
    initialModel: model,
  })
  await protocol.emitCatalogs()

  let buffer = ''
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', chunk => {
    buffer += chunk
    while (buffer.includes('\n')) {
      const newline = buffer.indexOf('\n')
      const raw = buffer.slice(0, newline)
      buffer = buffer.slice(newline + 1)
      if (!raw.trim()) continue
      void handleCommand(raw)
    }
  })

  async function handleCommand(raw) {
    let command
    try {
      command = JSON.parse(raw)
      await protocol.handle(command)
    } catch (error) {
      writeEvent({ type: 'command_error', message: error.message })
    }
  }

  await new Promise(resolve => process.stdin.on('end', resolve))
  protocol.dispose()
  unsubscribe()
  session.dispose()
}

const isEntrypoint = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href
if (isEntrypoint) {
  runBridge().catch(error => {
    writeEvent({ type: 'bridge_error', message: error.message })
    process.exitCode = 1
  })
}
