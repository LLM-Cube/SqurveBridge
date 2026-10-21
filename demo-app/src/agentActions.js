/** Agent ↔ page glue: what the Agent is told about the page, and which of its
 * proposed actions the page is willing to carry out after the user confirms.
 */
import { resolveFocusedConfig } from './full-flow/model.js'

const ID = /^[A-Za-z0-9._-]{1,128}$/
const SAMPLE_LIMITS = new Set([3, 10, 20, 50, 100, 200])
const MAX_CELLS = 24

export function pageContextFor({ activeStep, experimentId, connections = [], configs = [], focusedMethod, focusedDatabase }) {
  const context = { page: activeStep }
  if (experimentId) context.experiment_id = experimentId
  const cells = connections
    .map(item => resolveFocusedConfig(configs, item.method, item.database))
    .filter(Boolean)
    .slice(0, MAX_CELLS)
    .map(config => ({ method: config.method, dataset: config.dataset }))
  if (cells.length) context.cells = cells
  const focused = resolveFocusedConfig(configs, focusedMethod, focusedDatabase)
  if (focused) context.focused_cell = { method: focused.method, dataset: focused.dataset }
  return context
}

const isId = value => typeof value === 'string' && ID.test(value)
const cellOf = value => (isId(value?.method) && isId(value?.dataset) ? { method: value.method, dataset: value.dataset } : null)

function normalizeArgs(action, args = {}) {
  if (action === 'start_experiment') {
    const cells = (Array.isArray(args.cells) ? args.cells : []).map(cellOf)
    const sampling = args.sampling || {}
    if (!cells.length || cells.length > MAX_CELLS || cells.some(cell => !cell)) return null
    if (!SAMPLE_LIMITS.has(Number(sampling.limit)) || !['slice', 'random'].includes(sampling.mode)) return null
    return { cells, sampling: { limit: Number(sampling.limit), mode: sampling.mode, seed: Number.isInteger(sampling.seed) ? sampling.seed : 42 } }
  }
  if (action === 'open_experiment') return isId(args.experiment_id) ? { experiment_id: args.experiment_id } : null
  if (action === 'open_cell') return cellOf(args)
  if (action === 'open_query') {
    const cell = cellOf(args)
    if (!cell || !isId(args.db_id) || typeof args.question !== 'string' || !args.question.trim()) return null
    return { db_id: args.db_id, question: args.question.trim().slice(0, 2000), ...cell }
  }
  return null
}

/** The proposal in the shape the page executes, or null when it must not run here. */
export function normalizeProposal(proposal, { hosted = false } = {}) {
  const action = String(proposal?.action || '')
  if (hosted && action === 'start_experiment') return null
  const args = normalizeArgs(action, proposal?.args)
  return args ? { id: proposal.id, action, args } : null
}
