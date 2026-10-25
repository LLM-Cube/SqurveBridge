import { useEffect, useState } from 'react'
import { workflowStages } from './model.js'
import { normalizeStages, stageChoices, variantIdentity, variantLabel } from './variantModel.js'

/** The focused config's stages; each stage can take another Actor of its type,
 * and the swaps are kept as a variant that runs next to the method. */
export default function ActorWorkflow({
  focusedConfig,
  stageActors,
  variants = [],
  onAddVariant,
  onRemoveVariant,
  t,
  testId = 'actor-workflow',
}) {
  const stages = workflowStages(focusedConfig)
  const [draft, setDraft] = useState({})
  const editable = Boolean(onAddVariant && stageActors)
  const swaps = normalizeStages(focusedConfig, draft)
  const changed = Object.keys(swaps).length > 0

  useEffect(() => { setDraft({}) }, [focusedConfig?.method, focusedConfig?.dataset])

  const addVariant = () => {
    onAddVariant?.(focusedConfig, swaps)
    setDraft({})
  }

  return (
    <div className="flow-actor-workflow" data-testid={testId}>
      <h3>{t('compose.actorWorkflow')}</h3>
      {stages.length ? (
        <ol>
          {stages.map((stage, index) => {
            const choices = editable ? stageChoices(stage, stageActors) : []
            const current = draft[stage.id] || stage.actor
            return (
              <li key={stage.id || `${stage.type || 'stage'}-${index}`} data-swapped={swaps[stage.id] ? 'true' : undefined}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div className="flow-actor-step-body">
                  {stage.id ? <code>{stage.id}</code> : null}
                  {stage.type ? <small>{stage.type}</small> : null}
                  {choices.length > 1 ? (
                    <select
                      aria-label={t('variants.stageActor', { stage: stage.id || stage.type })}
                      value={current}
                      onChange={event => setDraft(previous => ({ ...previous, [stage.id]: event.target.value }))}
                    >
                      {choices.map(actor => (
                        <option key={actor} value={actor}>
                          {actor === stage.actor ? t('variants.ownActor', { actor }) : actor}
                        </option>
                      ))}
                    </select>
                  ) : stage.actor ? <strong>{stage.actor}</strong> : null}
                </div>
              </li>
            )
          })}
        </ol>
      ) : (
        <p>{t('compose.noWorkflow')}</p>
      )}
      {editable && stages.length ? (
        <div className="flow-variant-actions">
          <button type="button" disabled={!changed} onClick={addVariant}>{t('variants.add')}</button>
          {changed ? (
            <button type="button" onClick={() => setDraft({})}>{t('variants.reset')}</button>
          ) : null}
          <p>{t('variants.hint')}</p>
        </div>
      ) : null}
      {editable && variants.length ? (
        <div className="flow-variant-list" data-testid="variant-list">
          <h4>{t('variants.title', { count: variants.length })}</h4>
          <ul>
            {variants.map(variant => {
              const identity = variantIdentity(variant)
              const label = `${variantLabel(variant.method, variant.stages)} / ${variant.dataset}`
              return (
                <li key={identity}>
                  <span>{label}</span>
                  <button
                    type="button"
                    aria-label={t('variants.remove', { label })}
                    onClick={() => onRemoveVariant?.(identity)}
                  >
                    ×
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
