// Studio introduces; the experiment is one page that reads top to bottom as
// compose → run → compare & history; Query stands apart for single questions.
export const EXPERIMENT_SECTIONS = ['compose', 'board', 'evidence']
export const PROCESS_STEPS = ['configure', 'experiment', 'query']

const LEGACY_STEP_MAP = {
  run: 'board',
  diagnose: 'board',
  improve: 'board',
  inspect: 'evidence',
  visualize: 'evidence',
  archive: 'evidence',
}

const normalize = hashOrId => {
  const value = String(hashOrId || '').replace(/^#/, '')
  return LEGACY_STEP_MAP[value] || value
}

/** The experiment section a hash or id points at, or null. */
export function resolveSection(hashOrId) {
  const value = normalize(hashOrId)
  return EXPERIMENT_SECTIONS.includes(value) ? value : null
}

export function resolveProcessStep(hashOrId, fallback = PROCESS_STEPS[0]) {
  const value = normalize(hashOrId)
  if (EXPERIMENT_SECTIONS.includes(value)) return 'experiment'
  return PROCESS_STEPS.includes(value) ? value : fallback
}
