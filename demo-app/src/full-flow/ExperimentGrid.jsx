import { useEffect, useMemo, useState } from 'react'
import { DATABASES, configKey, experimentGrid } from './model.js'

const unique = values => [...new Set(values.filter(Boolean))]

/** Compose as a grid: pick method × benchmark cells for one experiment. */
export default function ExperimentGrid({
  api,
  configs = [],
  connections = [],
  sampleLimit = 20,
  sampleMode = 'slice',
  sampleSeed = 42,
  onToggleConnection,
  onSelectLine,
  onFocusConnection,
  focusedMethod,
  focusedDatabase,
  t,
}) {
  const [scores, setScores] = useState(() => new Map())

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
        setScores(next)
      })
      .catch(() => {})
    return () => { active = false }
  }, [api, params])

  const rows = experimentGrid(configs, connections, scores)
  const selectedCount = rows.reduce((total, row) => total + row.cells.filter(cell => cell.selected).length, 0)

  return (
    <section className="experiment-grid" data-testid="experiment-grid" aria-labelledby="experiment-grid-title">
      <header>
        <div>
          <h3 id="experiment-grid-title">{t('grid.title')}</h3>
          <p>{t('grid.description', { limit: sampleLimit, mode: sampleMode })}</p>
        </div>
        <strong>{t('grid.selected', { count: selectedCount })}</strong>
      </header>
      <div className="experiment-grid-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t('results.method')}</th>
              {DATABASES.map(database => (
                <th key={database} scope="col">
                  <button type="button" onClick={() => onSelectLine?.({ database })} title={t('grid.selectColumn')}>
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
                  <button type="button" onClick={() => onSelectLine?.({ method: row.method })} title={t('grid.selectRow')}>
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
                            onChange={() => onToggleConnection?.(row.method, cell.database)}
                            aria-label={`${row.method} × ${cell.database}`}
                          />
                          <span>{cell.score === null ? t('grid.noScore') : `${(cell.score * 100).toFixed(0)}%`}</span>
                        </label>
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
    </section>
  )
}
