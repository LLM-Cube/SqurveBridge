/** The experiment plan: what a run will execute, shared by Compose, Run and Compare.
 * Pure state transitions; the page renders the plan and sends its payload.
 */

export const initialPlan = () => ({
  cells: [],
  sampling: { limit: 20, mode: 'slice', seed: 42 },
  status: 'draft',
  experimentId: null,
  problems: [],
})

// The server refuses experiments larger than this (MAX_CELLS in the Demo API).
export const MAX_PLAN_CELLS = 24

const keyOf = cell => `${cell.method}/${cell.dataset}`

export function planReducer(plan, action) {
  switch (action.type) {
    case 'toggleCell': {
      const key = keyOf(action)
      const present = plan.cells.some(cell => keyOf(cell) === key)
      if (present) {
        // The plan always has something to run.
        if (plan.cells.length < 2) return plan
        return { ...plan, cells: plan.cells.filter(cell => keyOf(cell) !== key) }
      }
      return { ...plan, cells: [...plan.cells, { method: action.method, dataset: action.dataset, stages: {} }] }
    }
    case 'selectLine': {
      const line = [...action.runnable].filter(key => key.startsWith(`${action.method}/`) || (action.dataset && key.endsWith(`/${action.dataset}`)))
      if (!line.length) return plan
      const selected = line.every(key => plan.cells.some(cell => keyOf(cell) === key))
      if (selected) {
        const remaining = plan.cells.filter(cell => !line.includes(keyOf(cell)))
        return remaining.length ? { ...plan, cells: remaining } : plan
      }
      const missing = line.filter(key => !plan.cells.some(cell => keyOf(cell) === key))
      return {
        ...plan,
        cells: [...plan.cells, ...missing.map(key => {
          const [method, dataset] = key.split('/')
          return { method, dataset, stages: {} }
        })],
      }
    }
    case 'submitStarted':
      return { ...plan, status: 'submitted', problems: [] }
    case 'submitAccepted':
      return { ...plan, status: 'running', experimentId: action.experimentId, problems: [] }
    case 'submitRejected':
      // A rejected plan keeps its cells, so the user can fix the reasons and send it again.
      return { ...plan, status: 'rejected', problems: action.problems || [] }
    case 'setSampling':
      return { ...plan, sampling: { ...action.sampling } }
    default:
      return plan
  }
}

/** What the plan will run, and whether it can be sent as it stands. */
export function planSummary(plan) {
  const cells = plan.cells.length
  const variants = plan.cells.filter(cell => cell.stages && Object.keys(cell.stages).length).length
  const questionsPerCell = plan.sampling.limit
  let reason = null
  if (!cells) reason = 'empty'
  else if (cells > MAX_PLAN_CELLS) reason = 'tooManyCells'
  return {
    cells,
    variants,
    questionsPerCell,
    questions: cells * questionsPerCell,
    runnable: reason === null,
    reason,
  }
}
