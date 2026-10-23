import { useEffect, useId, useMemo, useState } from 'react'
import { DATABASES, SAMPLE_LIMITS, configKey, experimentGrid } from './model.js'
import { planSummary } from './experimentPlan.js'
import { experimentCells } from './variantModel.js'
import { variantIdentity, variantLabel } from './variantModel.js'

const unique = values => [...new Set(values.filter(Boolean))]

/** Compose as a grid: pick method × benchmark cells for one experiment. */
export default function ExperimentGrid({
  api,
  refreshKey,
  configs = [],
  connections = [],
  sampleLimit = 20,
  sampleMode = 'slice',
  sampleSeed = 42,
  onToggleConnection,
  onSelectAll,
  onClear,
  onRemoveVariant,
  readOnly = false,
  credentialConfigured,
  onConfigureSql,
  variants = [],
  onSampleLimitChange,
  onSampleModeChange,
  onSampleSeedChange,
  onSelectLine,
  onFocusConnection,
  onGoToRun,
  focusedMethod,
  focusedDatabase,
  t,
}) {
  const [scoreResult, setScoreResult] = useState(null)
  const [seedDraft, setSeedDraft] = useState(String(sampleSeed))
  const samplingId = useId()
  useEffect(() => { setSeedDraft(String(sampleSeed)) }, [sampleSeed])

  const params = useMemo(() => new URLSearchParams({
    methods: unique(configs.map(item => item.method)).join(','),
    datasets: unique(configs.map(item => item.dataset)).join(','),
    sample_limit: String(sampleLimit),
    sample_mode: sampleMode,
    sample_seed: String(sampleSeed),
  }).toString(), [configs, sampleLimit, sampleMode, sampleSeed])

  useEffect(() => {
    if (!api || !configs.length) return undefined
    let active = true
    api(`/api/results/matrix?${params}`)
      .then(data => {
        if (!active || !Array.isArray(data?.cells)) return
        const next = new Map()
        for (const cell of data.cells) {
          const value = cell?.values?.ex
          if (cell.status === 'ok' && typeof value === 'number') next.set(configKey(cell.method, cell.dataset), value)
        }
        setScoreResult({ params, scores: next })
      })
      .catch(() => {})
    return () => { active = false }
  }, [api, params, refreshKey])

  // Never label a score from the previous sampling request with the new settings.
  const scores = scoreResult?.params === params ? scoreResult.scores : new Map()
  const rows = experimentGrid(configs, connections, scores)
  const selectedCount = rows.reduce((total, row) => total + row.cells.filter(cell => cell.selected).length, 0)

  return (
    <section className="experiment-grid" data-testid="experiment-grid" aria-labelledby="experiment-grid-title">
      <header>
        <div>
          <h3 id="experiment-grid-title">{t('grid.title')}</h3>
          <p>{t('grid.description')}</p>
        </div>
        <strong>{t('grid.selected', { count: selectedCount })}</strong>
      </header>
      <div className="experiment-grid-sampling" role="group" aria-label={t('configure.sampling')}>
        <div className="experiment-grid-sampling-heading">
          <h4>{t('grid.parametersTitle')}</h4>
          <span>{t('grid.parametersScope')}</span>
        </div>
        <label>
          <span>{t('configure.sampleLimit')}</span>
          <select
            aria-label={t('configure.sampleLimit')}
            aria-describedby={`${samplingId}-limit`}
            value={sampleLimit}
            disabled={readOnly}
            onChange={event => onSampleLimitChange?.(Number(event.target.value))}
          >
            {SAMPLE_LIMITS.map(limit => <option key={limit} value={limit}>{t('grid.limitOption', { count: limit })}</option>)}
          </select>
          <small id={`${samplingId}-limit`}>{t('grid.limitHelp')}</small>
        </label>
        <label>
          <span>{t('configure.sampleMode')}</span>
          <select
            aria-label={t('configure.sampleMode')}
            aria-describedby={`${samplingId}-mode`}
            value={sampleMode}
            disabled={readOnly}
            onChange={event => onSampleModeChange?.(event.target.value)}
          >
            <option value="slice">{t('configure.sampleSlice')}</option>
            <option value="random">{t('configure.sampleRandom')}</option>
          </select>
          <small id={`${samplingId}-mode`}>{t(sampleMode === 'random' ? 'grid.randomHelp' : 'grid.sliceHelp')}</small>
        </label>
        <label>
          <span>{t('configure.sampleSeed')} <em>{t(sampleMode === 'random' ? 'grid.seedActive' : 'grid.seedInactive')}</em></span>
          <input
            type="number"
            aria-label={t('configure.sampleSeed')}
            aria-describedby={`${samplingId}-seed`}
            step="1"
            min="0"
            disabled={readOnly || sampleMode !== 'random'}
            value={seedDraft}
            onChange={event => {
              const value = event.target.value
              setSeedDraft(value)
              const seed = Number(value)
              if (value !== '' && Number.isSafeInteger(seed) && seed >= 0) onSampleSeedChange?.(seed)
            }}
            onBlur={() => setSeedDraft(String(sampleSeed))}
          />
          <small id={`${samplingId}-seed`}>{t(sampleMode === 'random' ? 'grid.seedHelp' : 'grid.seedUnusedHelp')}</small>
        </label>
        <p className="experiment-grid-sampling-note">{t('grid.fairComparison')}</p>
      </div>
      {readOnly && <p className="experiment-grid-sampling-note">{t('plan.hostedReadOnly')}</p>}
      <div className="experiment-grid-guide">
        <span><b>{t('grid.selectionTitle')}</b> {t('grid.selectionHelp')}</span>
        <span>{t('grid.scoreHelp')}</span>
      </div>
      <div className="experiment-grid-bulk">
        <button type="button" className="flow-secondary-action" disabled={readOnly || !configs.some(item => item.installed !== false)} onClick={onSelectAll}>{t('plan.selectAll')}</button>
        <button type="button" className="flow-secondary-action" disabled={readOnly || (!connections.length && !variants.length)} onClick={onClear}>{t('plan.clear')}</button>
      </div>
      <div className="experiment-grid-wrap">
        <table aria-label={t('grid.title')}>
          <thead>
            <tr>
              <th scope="col">{t('results.method')}</th>
              {DATABASES.map(database => (
                <th key={database} scope="col">
                  <button type="button" disabled={readOnly} onClick={() => onSelectLine?.({ database })} title={t('grid.selectColumn')}>
                    {database}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.method}>
                <th scope="row">
                  <button type="button" disabled={readOnly} onClick={() => onSelectLine?.({ method: row.method })} title={t('grid.selectRow')}>
                    {row.method}
                  </button>
                </th>
                {row.cells.map(cell => (
                  <td
                    key={cell.database}
                    data-runnable={cell.runnable}
                    data-installed={cell.installed}
                    data-selected={cell.selected}
                    data-focused={cell.selected && row.method === focusedMethod && cell.database === focusedDatabase ? 'true' : undefined}
                  >
                    {!cell.runnable ? (
                      <span className="experiment-grid-na">{t('grid.noConfig')}</span>
                    ) : !cell.installed ? (
                      <span className="experiment-grid-na">{t('grid.notInstalled')}</span>
                    ) : (
                      <div className="experiment-grid-cell">
                        <label>
                          <input
                            type="checkbox"
                            checked={cell.selected}
                            disabled={readOnly}
                            onChange={() => onToggleConnection?.(row.method, cell.database)}
                            aria-label={`${row.method} × ${cell.database}`}
                          />
                          <span>{cell.score === null ? t('grid.noScore') : `${(cell.score * 100).toFixed(0)}%`}</span>
                        </label>
                        {variantCount(variants, row.method, cell.database) > 0 ? (
                          <span className="experiment-grid-variants">+{variantCount(variants, row.method, cell.database)}</span>
                        ) : null}
                        {cell.selected ? (
                          <button
                            type="button"
                            className="experiment-grid-focus"
                            aria-label={t('grid.focusCell', { method: row.method, database: cell.database })}
                            aria-pressed={row.method === focusedMethod && cell.database === focusedDatabase}
                            onClick={() => onFocusConnection?.(row.method, cell.database)}
                          >
                            {t('grid.focusCellAction')}
                          </button>
                        ) : null}
                      </div>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {variants.length > 0 && <ul className="experiment-grid-variant-list" aria-label={t('variants.runLabel')}>
        {variants.map(variant => <li key={variantIdentity(variant)}>
          <span>{variantLabel(variant.method, variant.stages)} / {variant.dataset}</span>
          <button type="button" disabled={readOnly} onClick={() => onRemoveVariant?.(variantIdentity(variant))} aria-label={t('plan.removeVariant', { name: variantLabel(variant.method, variant.stages) })}>{t('plan.remove')}</button>
        </li>)}
      </ul>}
      <PlanFooter
        connections={connections}
        variants={variants}
        sampling={{ limit: sampleLimit, mode: sampleMode, seed: sampleSeed }}
        onGoToRun={onGoToRun}
        configs={configs}
        credentialConfigured={credentialConfigured}
        onConfigureSql={onConfigureSql}
        readOnly={readOnly}
        t={t}
      />
    </section>
  )
}

const variantCount = (variants, method, database) =>
  variants.filter(item => item.method === method && item.dataset === database).length

/** What the run will execute, and the way on to the Run page. */
function PlanFooter({ connections, variants, sampling, onGoToRun, configs, credentialConfigured, onConfigureSql, readOnly, t }) {
  const summary = planSummary({
    cells: experimentCells(
      (connections || []).map(item => ({ method: item.method, dataset: item.database })),
      variants,
    ),
    sampling: { limit: sampling.limit },
  }, { configs, credentialConfigured })
  return (
    <footer className="experiment-plan-footer" data-testid="experiment-plan-footer">
      <div className="experiment-plan-estimate" aria-live="polite">
        <span className="experiment-plan-eyebrow">{t('grid.workloadTitle')}</span>
        <p>
          {t('plan.summary', {
            cells: summary.cells,
            variants: summary.variants,
            limit: summary.questionsPerCell,
            questions: summary.questions,
          })}
        </p>
        <small>{t('grid.workloadHelp')}</small>
      </div>
      {summary.reason === 'credentialMissing' && <button type="button" onClick={onConfigureSql}>{t('header.configureLocalApi')}</button>}
      <button type="button" disabled={readOnly || !summary.runnable} onClick={() => onGoToRun?.()}>
        {t('compose.goToRun')}
      </button>
      {summary.reason ? <p role="status">{t(`grid.${summary.reason}`)}</p> : null}
    </footer>
  )
}
