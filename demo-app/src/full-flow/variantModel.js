/** Variants: a reproduce config with some stages swapped to another Actor.
 * Pure helpers shared by Compose (building variants), Run (sending them as
 * experiment cells) and Query (replaying one). The server owns validation and
 * the variant id; these only shape what the page sends and shows.
 */

/** Swaps that change the config's own Actors, sorted by stage id. */
export function normalizeStages(config, stages) {
  const own = new Map((config?.stages || []).map(stage => [stage.id, stage.actor]))
  return Object.fromEntries(
    Object.entries(stages || {})
      .filter(([stageId, actor]) => own.has(stageId) && actor && actor !== own.get(stageId))
      .sort(([a], [b]) => a.localeCompare(b)),
  )
}

const stageText = stages => Object.entries(stages).map(([stage, actor]) => `${stage}→${actor}`).join(', ')

/** Same wording as the server's label for a variant column. */
export function variantLabel(method, stages) {
  const swaps = stageText(stages || {})
  return swaps ? `${method} · ${swaps}` : method
}

export const variantIdentity = variant => `${variant.dataset}/${variant.method}|${stageText(variant.stages || {})}`

/** Add a variant once; a swap that changes nothing is not a variant. */
export function addVariant(variants, config, stages) {
  const normalized = normalizeStages(config, stages)
  if (!config || !Object.keys(normalized).length) return variants
  const variant = { method: config.method, dataset: config.dataset, stages: normalized }
  const identity = variantIdentity(variant)
  return variants.some(item => variantIdentity(item) === identity) ? variants : [...variants, variant]
}

export const removeVariant = (variants, identity) => variants.filter(item => variantIdentity(item) !== identity)

/** Experiment cells for the selected configs plus every variant. */
export function experimentCells(configs, variants) {
  return [
    ...(configs || []).map(config => ({ method: config.method, dataset: config.dataset })),
    ...(variants || []).map(variant => ({ method: variant.method, dataset: variant.dataset, stages: variant.stages })),
  ]
}

/** Matrix axes for an experiment: one column per cell (method or variant). */
export function experimentAxes(experiment) {
  const cells = experiment?.cells || []
  const unique = values => [...new Set(values.filter(Boolean))]
  return {
    methods: unique(cells.map(cell => cell.variant || cell.method)),
    datasets: unique(cells.map(cell => cell.dataset)),
  }
}

/** Actors a stage may take, its own first; the server lists them by task type. */
export function stageChoices(stage, stageActors) {
  const allowed = stageActors?.[stage?.type] || []
  return [...new Set([stage?.actor, ...allowed].filter(Boolean))]
}
