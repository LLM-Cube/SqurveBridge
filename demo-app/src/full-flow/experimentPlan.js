/** The experiment plan: what a run will execute, shared by Compose, Run and Compare.
 * Pure state transitions; the page renders the plan and sends its payload.
 */

import { normalizeStages, variantIdentity } from './variantModel.js'
import { configKey, SAMPLE_LIMITS } from './model.js'

export const initialPlan = () => ({
  cells: [],
  sampling: { limit: 20, mode: 'slice', seed: 42 },
  status: 'draft',
  experimentId: null,
  snapshot: null,
  problems: [],
})

// The server refuses experiments larger than this (MAX_CELLS in the Demo API).
export const MAX_PLAN_CELLS = 24

const keyOf = cell => `${cell.method}/${cell.dataset}`
const hasStages = cell => Boolean(cell.stages && Object.keys(cell.stages).length)
const plainCells = cells => cells.filter(cell => !hasStages(cell))

/** Connections: the method × benchmark pairs the plan runs without any Actor swap. */
export const connectionsOf = plan => plainCells(plan.cells).map(cell => ({
  method: cell.method,
  database: cell.dataset,
  key: keyOf(cell),
}))

/** Variants: the cells that swap Actors, with the swaps they make. */
export const variantsOf = plan => plan.cells.filter(hasStages).map(cell => ({
  method: cell.method,
  dataset: cell.dataset,
  stages: { ...cell.stages },
}))

export function planReducer(plan, action) {
  switch (action.type) {
    case 'toggleCell': {
      if (action.runnable && !action.runnable.has(`${action.method}/${action.dataset}`)) return plan
      const key = keyOf(action)
      const present = plainCells(plan.cells).some(cell => keyOf(cell) === key)
      if (present) {
        return { ...plan, status: 'draft', cells: plan.cells.filter(cell => hasStages(cell) || keyOf(cell) !== key) }
      }
      return { ...plan, status: 'draft', cells: [...plan.cells, { method: action.method, dataset: action.dataset, stages: {} }] }
    }
    case 'ensureCell': {
      const key = keyOf(action)
      if (plainCells(plan.cells).some(cell => keyOf(cell) === key)) return plan
      return { ...plan, status: 'draft', cells: [...plan.cells, { method: action.method, dataset: action.dataset, stages: {} }] }
    }
    case 'selectLine': {
      const line = [...action.runnable].filter(key => key.startsWith(`${action.method}/`) || (action.dataset && key.endsWith(`/${action.dataset}`)))
      if (!line.length) return plan
      const plain = plainCells(plan.cells)
      const selected = line.every(key => plain.some(cell => keyOf(cell) === key))
      if (selected) {
        const remaining = plain.filter(cell => !line.includes(keyOf(cell)))
        return { ...plan, status: 'draft', cells: [...plan.cells.filter(hasStages), ...remaining] }
      }
      const missing = line.filter(key => !plain.some(cell => keyOf(cell) === key))
      return {
        ...plan,
        status: 'draft',
        cells: [...plan.cells, ...missing.map(key => {
          const [method, dataset] = key.split('/')
          return { method, dataset, stages: {} }
        })],
      }
    }
    case 'selectAll':
      return {
        ...plan, status: 'draft',
        cells: [...[...action.runnable].map(key => {
          const [method, dataset] = key.split('/')
          return { method, dataset, stages: {} }
        }), ...plan.cells.filter(hasStages)],
      }
    case 'clear':
      return { ...plan, cells: [], status: 'draft', problems: [] }
    case 'addVariant': {
      const swaps = normalizeStages(action.config, action.stages)
      if (!Object.keys(swaps).length) return plan
      const cell = { method: action.config.method, dataset: action.config.dataset, stages: swaps }
      const identity = variantIdentity(cell)
      if (plan.cells.some(item => hasStages(item) && variantIdentity(item) === identity)) return plan
      return { ...plan, status: 'draft', cells: [...plan.cells, cell] }
    }
    case 'removeVariant':
      return { ...plan, status: 'draft', cells: plan.cells.filter(cell => !hasStages(cell) || variantIdentity(cell) !== action.identity) }
    case 'hydrate': {
      // A draft or a template replaces the plan's cells and sampling in one step.
      const connections = (action.connections || []).filter(item => typeof item?.method === 'string' && typeof item?.database === 'string').map(item => ({ method: item.method, dataset: item.database, stages: {} }))
      const variants = (action.variants || [])
        .filter(item => typeof item?.method === 'string' && typeof item?.dataset === 'string' && item.stages && typeof item.stages === 'object' && !Array.isArray(item.stages))
        .map(item => {
          const config = action.configs?.find(config => configKey(config.method, config.dataset) === configKey(item.method, item.dataset))
          const rawStages = Object.fromEntries(Object.entries(item.stages).filter(([, actor]) => typeof actor === 'string'))
          const stages = config ? normalizeStages(config, rawStages) : Object.fromEntries(Object.entries(rawStages).sort(([a], [b]) => a.localeCompare(b)))
          return { method: item.method, dataset: item.dataset, stages }
        }).filter(hasStages)
      const cells = [...new Map([...connections, ...variants].map(cell => [variantIdentity(cell), cell])).values()]
      return {
        ...initialPlan(),
        cells,
        sampling: validSampling(action.sampling),
      }
    }
    case 'submitStarted':
      return { ...plan, status: 'submitted', problems: [] }
    case 'submitAccepted':
      return { ...plan, status: 'running', experimentId: action.experimentId, problems: [] }
    case 'submitRejected':
      // A rejected plan keeps its cells, so the user can fix the reasons and send it again.
      return { ...plan, status: 'rejected', problems: action.problems || [] }
    case 'openExperiment':
      return { ...plan, experimentId: action.experimentId, snapshot: null, status: plan.status === 'draft' ? 'draft' : 'loading' }
    case 'experimentReceived': {
      const snapshot = action.experiment
      if (!snapshot?.experiment_id) return plan
      if (!action.adopt && snapshot.experiment_id !== plan.experimentId) return plan
      const running = ['running', 'queued', 'cancelling'].includes(snapshot.status)
      const status = !action.adopt && plan.status === 'draft' ? 'draft' : running ? 'running' : 'done'
      return { ...plan, snapshot, experimentId: snapshot.experiment_id, status }
    }
    case 'setSampling':
      return { ...plan, status: 'draft', sampling: validSampling(action.sampling) }
    default:
      return plan
  }
}

/** What the plan will run, and whether it can be sent as it stands. */
export function planSummary(plan, { configs, credentialConfigured } = {}) {
  const cells = plan.cells.length
  const variants = plan.cells.filter(hasStages).length
  const questionsPerCell = plan.sampling.limit
  let reason = null
  if (!cells) reason = 'empty'
  else if (cells > MAX_PLAN_CELLS) reason = 'tooManyCells'
  else if (configs && plan.cells.some(cell => {
    const config = configs.find(item => configKey(item.method, item.dataset) === configKey(cell.method, cell.dataset))
    return !config || config.installed === false
  })) reason = 'unavailableCells'
  else if (credentialConfigured === false) reason = 'credentialMissing'
  return {
    cells,
    variants,
    questionsPerCell,
    questions: cells * questionsPerCell,
    runnable: reason === null,
    reason,
  }
}

/** Validate persisted sampling before it can reach a run request. */
export function validSampling(sampling) {
  return {
    limit: SAMPLE_LIMITS.includes(sampling?.limit) ? sampling.limit : 20,
    mode: sampling?.mode === 'random' ? 'random' : 'slice',
    seed: Number.isSafeInteger(sampling?.seed) && sampling.seed >= 0 ? sampling.seed : 42,
  }
}

/** Resolve display names to canonical configs and normalize every Actor swap. */
export function planPayload(plan, configs) {
  const cells = plan.cells.map(cell => {
    const config = configs.find(item => configKey(item.method, item.dataset) === configKey(cell.method, cell.dataset))
    const stages = normalizeStages(config, cell.stages)
    return { method: config?.method || cell.method, dataset: config?.dataset || cell.dataset, ...(Object.keys(stages).length ? { stages } : {}) }
  })
  return { cells: [...new Map(cells.map(cell => [variantIdentity(cell), cell])).values()], sampling: validSampling(plan.sampling) }
}

/** An earlier experiment as the starting point of a new plan: its pairs, its variants and its sampling. */
export function templateFromExperiment(experiment) {
  const cells = experiment?.cells || []
  const pairs = new Map()
  for (const cell of cells) {
    if (hasStages(cell)) continue
    const key = `${cell.method}/${cell.dataset}`
    if (!pairs.has(key)) pairs.set(key, { method: cell.method, database: cell.dataset })
  }
  return {
    connections: [...pairs.values()],
    variants: cells
      .filter(cell => cell.stages && Object.keys(cell.stages).length)
      .map(cell => ({ method: cell.method, dataset: cell.dataset, stages: { ...cell.stages } })),
    sampling: { ...experiment.sampling },
  }
}
