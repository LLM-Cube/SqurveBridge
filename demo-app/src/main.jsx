import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import MatrixStudio from './MatrixStudio.jsx'
import SqlAuthDialog from './SqlAuthDialog.jsx'
import { deploymentTarget, featureEnabled } from './runtimeMode.js'
import { detectLocale, translate } from './i18n/index.js'
import { sanitizeRunError } from './full-flow/RunWorkspace.jsx'
import { modelsForProvider } from './llmCatalog.js'
import ModelSourceRow from './ModelSourceRow.jsx'
import { useLiveModels } from './useLiveModels.js'

const api = async (path, options = {}) => {
  const response = await fetch(path, options)
  const data = await response.json().catch(() => ({ message: response.statusText }))
  if (!response.ok) throw new Error(sanitizeRunError({ message: data.message || 'Request failed' }))
  return data
}

const postJson = (path, body, options = {}) => api(path, {
  ...options,
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

function ProviderConfig({
  health,
  capabilities,
  refresh,
  controlledOpen,
  onOpenChange,
  showTrigger = true,
}) {
  const providers = capabilities?.llm_providers || []
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const setOpen = value => {
    const next = typeof value === 'function' ? value(open) : value
    if (controlledOpen === undefined) setInternalOpen(next)
    onOpenChange?.(next)
  }
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [persist, setPersist] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const liveModels = useLiveModels(postJson)
  const panelRef = useRef(null)
  const selected = providers.find(item => item.id === provider)
  const source = liveModels.forProvider(provider, modelsForProvider(selected, provider))
  const catalogModels = source.models

  useEffect(() => {
    const current = health?.provider?.provider
    const next = providers.find(item => item.id === current)?.id || providers.find(item => item.configured)?.id || providers[0]?.id || ''
    if (!next) return
    setProvider(next)
    const preferred = health?.provider?.provider === next ? health?.provider?.model : null
    const catalog = providers.find(item => item.id === next)
    setModel(preferred || catalog?.default_model || '')
  }, [health?.provider?.provider, health?.provider?.model, providers.map(item => item.id).join('|')])

  useEffect(() => {
    if (!open) return
    const onPointer = event => {
      if (showTrigger && !panelRef.current?.contains(event.target)) setOpen(false)
    }
    const onKey = event => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, showTrigger])

  const selectProvider = nextId => {
    setProvider(nextId)
    const catalog = providers.find(item => item.id === nextId)
    const preferred = health?.provider?.provider === nextId ? health?.provider?.model : null
    setModel(preferred || catalog?.default_model || '')
    setMessage('')
    setError('')
    liveModels.reset()
  }

  const refreshModels = async () => {
    setError('')
    setModel(await liveModels.refresh({ provider, apiKey }, model))
  }

  const close = () => {
    setApiKey('')
    setMessage('')
    setError('')
    setOpen(false)
  }

  const save = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const payload = { provider, model: model.trim(), persist }
      if (apiKey.trim()) payload.api_key = apiKey.trim()
      const data = await postJson('/api/provider', payload)
      setApiKey('')
      setMessage(data.provider?.configured ? `Saved ${data.provider.provider}/${data.provider.model}` : 'Saved')
      await refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const fields = <>
    <label className="field"><span>Provider</span><select aria-label="Provider" value={provider} onChange={event => selectProvider(event.target.value)}>{providers.map(item => <option key={item.id} value={item.id}>{item.id}{item.configured ? ' · configured' : ' · needs key'}</option>)}</select></label>
    <div className="field model-id-field">
      <span>Model</span>
      {catalogModels.length > 0 && (
        <ul className="model-suggestion-list" aria-label="Suggested models">
          {catalogModels.map(item => (
            <li key={item}>
              <button
                type="button"
                className={model === item ? 'active' : ''}
                aria-pressed={model === item}
                onClick={() => setModel(item)}
              >
                {item}
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        aria-label="Model"
        list="local-model-catalog"
        value={model}
        onChange={event => setModel(event.target.value)}
        placeholder="Enter a model ID"
        autoComplete="off"
        spellCheck="false"
      />
      <datalist id="local-model-catalog">
        {catalogModels.map(item => <option key={item} value={item} />)}
      </datalist>
      <ModelSourceRow active={source.active} error={source.error} empty={catalogModels.length === 0} refreshing={liveModels.refreshing} disabled={busy || !provider} onRefresh={refreshModels} />
      <small>Pick a model above, or type any model ID this provider supports. Uses the key above, or the saved one.</small>
    </div>
    <label className="field"><span>API key{selected?.env_var ? ` · ${selected.env_var}` : ''}</span><input type="password" aria-label={selected?.env_var ? `API key · ${selected.env_var}` : 'API key'} autoComplete="off" spellCheck="false" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={selected?.configured ? 'Leave blank to keep current key' : 'Paste API key'} /></label>
    <label className="persist-toggle"><input type="checkbox" checked={persist} onChange={event => setPersist(event.target.checked)} /><span>Write to repo-root .env</span></label>
  </>

  if (!showTrigger) {
    if (!open) return null
    return <div
      className="flow-provider-backdrop"
      role="presentation"
      onMouseDown={event => { if (event.target === event.currentTarget) close() }}
    >
      <section
        className="flow-provider-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="LLM provider configuration"
        aria-labelledby="flow-provider-title"
        ref={panelRef}
      >
        <div className="flow-provider-header">
          <div>
            <span>Local credentials</span>
            <h2 id="flow-provider-title">Configure LLM</h2>
          </div>
          <button type="button" className="flow-provider-close" aria-label="Close" onClick={close}>×</button>
        </div>
        <p className="flow-provider-intro">Choose a provider and model ID. Keys stay on localhost and are never returned by the API.</p>
        <div className="flow-provider-body">
          <div className="flow-provider-status">
            <span>{health?.provider?.configured ? 'Connected' : 'Not connected'}</span>
            {health?.provider?.configured && <strong>{health.provider.provider} / {health.provider.model}</strong>}
          </div>
          {fields}
          <div className="flow-provider-actions">
            <button type="button" className="flow-provider-primary" disabled={busy || !provider || !model.trim()} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
            <button type="button" className="flow-provider-secondary" onClick={close}>Close</button>
          </div>
          {message && <p className="flow-provider-note">{message}</p>}
          {error && <p className="error-banner" role="alert">{error}</p>}
        </div>
      </section>
    </div>
  }

  return <div className="provider-config" ref={panelRef}>
    <button className="button compact secondary" type="button" aria-expanded={open} onClick={() => setOpen(current => !current)}>Configure LLM</button>
    {open && <div className="provider-config-panel" role="dialog" aria-label="LLM provider configuration">
      <div className="panel-title"><div><span>LLM credentials</span><small>Keys stay on localhost · never returned by API</small></div></div>
      {fields}
      <div className="provider-config-actions"><button className="button primary compact" disabled={busy || !provider || !model.trim()} onClick={save}>{busy ? 'Saving…' : 'Save'}</button><button className="button compact" type="button" onClick={close}>Close</button></div>
      {message && <p className="provider-config-note">{message}</p>}
      {error && <p className="error-banner">{error}</p>}
    </div>}
  </div>
}

function App() {
  const [health, setHealth] = useState(null)
  const [capabilities, setCapabilities] = useState(null)
  const [databases, setDatabases] = useState([])
  const [sqlAuth, setSqlAuth] = useState(null)
  const [sqlAuthOpen, setSqlAuthOpen] = useState(false)
  const [localProviderOpen, setLocalProviderOpen] = useState(false)
  const [bootError, setBootError] = useState(false)
  const [bootLocale] = useState(() => detectLocale(window.localStorage.getItem('squrve-demo-locale')))
  const hosted = deploymentTarget(capabilities) === 'hf-space'
  const sessionSqlAuth = hosted && featureEnabled(capabilities, 'session_sql_auth')
  const refresh = async () => {
    setBootError(false)
    try {
      // Boot on health + capabilities only. /api/databases can scan hundreds of
      // bundled SQLite files and must not block the first paint.
      const [healthData, capabilityData] = await Promise.all([
        api('/api/health'),
        api('/api/capabilities'),
      ])
      const hostedData = deploymentTarget(capabilityData) === 'hf-space'
        && featureEnabled(capabilityData, 'session_sql_auth')
        ? await api('/api/sql-auth')
        : null
      setHealth(healthData)
      setCapabilities(capabilityData)
      setSqlAuth(hostedData)
      api('/api/databases')
        .then(databaseData => setDatabases(databaseData.databases || []))
        .catch(() => setDatabases([]))
    } catch (error) {
      setBootError(true)
      setHealth({ status: 'error', provider: { configured: false, ready: false, message: error.message } })
    }
  }
  useEffect(() => { refresh() }, [])
  if (!capabilities) return <main className="deployment-gate" role={bootError ? 'alert' : 'status'}>
    {translate(bootLocale, bootError ? 'boot.error' : 'boot.loading')}
  </main>
  const localSqlAuth = {
    configured: Boolean(health?.provider?.configured && health?.provider?.ready),
    provider: health?.provider?.provider || '',
    model: health?.provider?.model || '',
  }
  const activeSqlAuth = hosted ? sqlAuth : localSqlAuth
  const configureSql = hosted
    ? () => setSqlAuthOpen(true)
    : () => setLocalProviderOpen(true)
  return <div className="hosted-matrix-shell">
    <MatrixStudio
      capabilities={capabilities}
      databases={databases}
      sqlAuth={activeSqlAuth}
      api={api}
      postJson={postJson}
      onConfigureSql={configureSql}
      credentialMode={hosted ? 'session' : 'local'}
    />
    {sessionSqlAuth && <SqlAuthDialog open={sqlAuthOpen} api={api} status={sqlAuth} onStatusChange={setSqlAuth} onClose={() => setSqlAuthOpen(false)} />}
    {!hosted && <ProviderConfig
      health={health}
      capabilities={capabilities}
      refresh={refresh}
      controlledOpen={localProviderOpen}
      onOpenChange={setLocalProviderOpen}
      showTrigger={false}
    />}
  </div>
}

const root = globalThis.__SQURVE_DEMO_ROOT__ || createRoot(document.getElementById('root'))
globalThis.__SQURVE_DEMO_ROOT__ = root
root.render(<App />)
