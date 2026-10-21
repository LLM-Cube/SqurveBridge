/** Sample sizes the Demo API accepts for runs and comparisons. */
export const SAMPLE_LIMITS = Object.freeze([3, 10, 20, 50, 100, 200])

export const METHODS = ['C3SQL', 'DINSQL', 'FinSQL', 'RESDSQL', 'E-SQL', 'SEDE', 'UNISAR', 'GPT Baseline']
export const DATABASES = ['Spider', 'BIRD', 'BookSQL', 'BULL-EN', 'BULL-CN', 'EHRSQL-2024', 'AmbiDB', 'Spider2']

const slug = value => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/[_\s]+/g, '-')
  .replace(/[^a-z0-9-]/g, '')

export const configKey = (method, database) => `${slug(database)}/${slug(method)}`
export const buildReadyKeys = configs => new Set((configs || []).map(item => configKey(item.method, item.dataset)))
export const buildConnections = (methods, databases) => methods.flatMap(
  method => databases.map(database => ({ method, database, key: configKey(method, database) })),
)
export const connectionKeyOf = connection => configKey(connection.method, connection.database)
export const hasConnection = (connections, method, database) =>
  (connections || []).some(item => connectionKeyOf(item) === configKey(method, database))
export const withConnectionKeys = connections => (connections || []).map(item => ({
  ...item,
  key: connectionKeyOf(item),
}))
export const toggleConnection = (connections, method, database) => {
  const current = connections || []
  if (hasConnection(current, method, database)) {
    if (current.length === 1) return current
    return current.filter(item => connectionKeyOf(item) !== configKey(method, database))
  }
  return [...current, { method, database }]
}
export const ensureConnection = (connections, method, database) => (
  hasConnection(connections, method, database)
    ? connections
    : [...(connections || []), { method, database }]
)
export const selectedMethodsFromConnections = connections =>
  METHODS.filter(method => (connections || []).some(item => item.method === method))
export const selectedDatabasesFromConnections = connections =>
  DATABASES.filter(database => (connections || []).some(item => item.database === database))
export const toggleMethodConnections = (connections, method) => {
  const current = connections || []
  const selectedDatabases = selectedDatabasesFromConnections(current)
  if (current.some(item => item.method === method)) {
    const next = current.filter(item => item.method !== method)
    return next.length ? next : current
  }
  const targets = selectedDatabases.length ? selectedDatabases : [DATABASES[0]]
  return targets.reduce(
    (next, database) => ensureConnection(next, method, database),
    current,
  )
}
export const toggleDatabaseConnections = (connections, database) => {
  const current = connections || []
  const selectedMethods = selectedMethodsFromConnections(current)
  if (current.some(item => item.database === database)) {
    const next = current.filter(item => item.database !== database)
    return next.length ? next : current
  }
  const sources = selectedMethods.length ? selectedMethods : [METHODS[0]]
  return sources.reduce(
    (next, method) => ensureConnection(next, method, database),
    current,
  )
}
export const resolveFocusedConfig = (configs, method, database) =>
  (configs || []).find(item => configKey(item.method, item.dataset) === configKey(method, database)) || null
/** Rows of the Compose experiment grid: which cells have a reproduce config,
 * which are selected, and the last recorded score keyed by configKey. */
export const experimentGrid = (configs, connections, scores = new Map()) => {
  const ready = buildReadyKeys(configs)
  return METHODS.map(method => ({
    method,
    cells: DATABASES.map(database => {
      const key = configKey(method, database)
      return {
        database,
        key,
        runnable: ready.has(key),
        selected: hasConnection(connections, method, database),
        score: scores.has(key) ? scores.get(key) : null,
      }
    }),
  }))
}

/** Toggle every runnable cell in one grid row ({ method }) or column ({ database }). */
export const selectGridLine = (connections, configs, { method, database }) => {
  const current = connections || []
  const ready = buildReadyKeys(configs)
  const line = buildConnections(method ? [method] : METHODS, database ? [database] : DATABASES)
    .filter(item => ready.has(item.key))
  if (!line.length) return current
  const allSelected = line.every(item => hasConnection(current, item.method, item.database))
  if (allSelected) {
    const keys = new Set(line.map(item => item.key))
    const next = current.filter(item => !keys.has(connectionKeyOf(item)))
    return next.length ? next : current
  }
  return line.reduce((next, item) => ensureConnection(next, item.method, item.database), current)
}

export const workflowStages = config => (config?.stages || []).map(stage => ({
  id: stage.id,
  type: stage.type,
  actor: stage.actor,
}))

export const normalizePublicGitHubUrl = value => {
  try {
    const parsed = new URL(String(value || '').trim())
    if (
      parsed.protocol !== 'https:'
      || parsed.hostname.toLowerCase() !== 'github.com'
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || parsed.port
    ) return ''
    const parts = parsed.pathname.split('/').filter(Boolean)
    if (parts.length !== 2) return ''
    const owner = parts[0]
    const repository = parts[1].replace(/\.git$/i, '')
    const publicSegment = /^[A-Za-z0-9_.-]+$/
    if (!publicSegment.test(owner) || !publicSegment.test(repository)) return ''
    return `https://github.com/${owner}/${repository}`
  } catch {
    return ''
  }
}
