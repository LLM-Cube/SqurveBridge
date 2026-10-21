import React, { useEffect, useState } from 'react'
import { fetchLiveModels, modelAfterRefresh, modelsForProvider } from './llmCatalog.js'

const jsonOptions = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export default function SqlAuthDialog({ open, api, status, onStatusChange, onClose }) {
  const providers = status?.providers || []
  const initialProvider = providers.find(item => item.id === status?.provider) || providers[0]
  const [provider, setProvider] = useState(initialProvider?.id || '')
  const [model, setModel] = useState(status?.model || initialProvider?.default_model || '')
  const [endpointId, setEndpointId] = useState(status?.endpoint_id || initialProvider?.default_endpoint_id || '')
  const [workspaceId, setWorkspaceId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [currentStatus, setCurrentStatus] = useState(status || { configured: false, providers })
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [live, setLive] = useState(null)

  useEffect(() => {
    setCurrentStatus(status || { configured: false, providers })
    const selected = providers.find(item => item.id === (status?.provider || provider)) || providers[0]
    if (!selected) return
    setProvider(selected.id)
    setModel(status?.provider === selected.id && status?.model
      ? status.model
      : selected.default_model)
    setEndpointId(status?.provider === selected.id && status?.endpoint_id
      ? status.endpoint_id
      : selected.default_endpoint_id || '')
  }, [status, providers.map(item => `${item.id}:${item.models.join(',')}`).join('|')])

  const selectedProvider = providers.find(item => item.id === provider)
  if (!open) return null

  const clearSecret = () => {
    setApiKey('')
  }

  const selectProvider = item => {
    setProvider(item.id)
    setModel(item.default_model)
    setEndpointId(item.default_endpoint_id || '')
    setMessage('')
    setError('')
    setLive(null)
  }

  const refreshModels = async () => {
    setBusy('models')
    setError('')
    const result = await fetchLiveModels(
      (path, body) => api(path, jsonOptions('POST', body)),
      { provider, apiKey, endpointId, workspaceId, refresh: true },
    )
    setLive(result)
    setModel(modelAfterRefresh(model, result))
    setBusy('')
  }

  const submit = async method => {
    setBusy(method)
    setMessage('')
    setError('')
    try {
      const data = await api(
        method === 'test' ? '/api/sql-auth/test' : '/api/sql-auth',
        jsonOptions(method === 'test' ? 'POST' : 'PUT', {
          provider,
          model,
          api_key: apiKey.trim(),
          endpoint_id: endpointId,
          workspace_id: workspaceId.trim(),
        }),
      )
      if (method === 'test') {
        setMessage(`Connection verified for ${data.provider}/${data.model}.`)
      } else {
        clearSecret()
        setCurrentStatus(data)
        onStatusChange(data)
        setMessage(`Using ${data.provider}/${data.model} for this session.`)
      }
    } catch (requestError) {
      setError(requestError.message || 'SQL authentication failed.')
    } finally {
      setBusy('')
    }
  }

  const disconnect = async () => {
    setBusy('disconnect')
    setMessage('')
    setError('')
    try {
      const data = await api('/api/sql-auth', jsonOptions('DELETE'))
      clearSecret()
      setCurrentStatus(data)
      onStatusChange(data)
      setMessage('SQL credential disconnected from this session.')
    } catch (requestError) {
      setError(requestError.message || 'Could not disconnect SQL authentication.')
    } finally {
      setBusy('')
    }
  }

  const close = () => {
    clearSecret()
    setWorkspaceId('')
    setMessage('')
    setError('')
    onClose()
  }

  const liveActive = live?.provider === provider && live.source === 'live' && live.models.length > 0
  const catalogModels = liveActive ? live.models : modelsForProvider(selectedProvider, provider)
  const selectedEndpoint = selectedProvider?.endpoints?.find(item => item.id === endpointId)

  return <div className="flow-provider-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
    <section className="flow-provider-dialog session-provider-dialog" role="dialog" aria-modal="true" aria-labelledby="sql-auth-title">
      <div className="flow-provider-header">
        <div><span>Session credentials</span><h2 id="sql-auth-title">Configure LLM</h2></div>
        <button className="flow-provider-close" type="button" aria-label="Close" onClick={close}>×</button>
      </div>
      <p className="flow-provider-intro">Choose a provider and model ID. The key stays in server memory for the current browser session only.</p>
      <form className="flow-provider-body" autoComplete="off" onSubmit={event => event.preventDefault()}>
          <div className="flow-provider-status">
            <span>{currentStatus?.configured ? 'Connected' : 'Not connected'}</span>
            {currentStatus?.configured && <strong>{currentStatus.provider} / {currentStatus.model}</strong>}
          </div>
          <label className="field"><span>Provider</span><select aria-label="Provider" value={provider} onChange={event => selectProvider(providers.find(item => item.id === event.target.value))}>{providers.map(item => <option key={item.id} value={item.id}>{item.id}{currentStatus?.configured && currentStatus.provider === item.id ? ' · configured' : ' · needs key'}</option>)}</select></label>
          <div className="field model-id-field">
            <span>Model</span>
            {catalogModels.length > 0 && <ul className="model-suggestion-list" aria-label="Suggested models">{catalogModels.map(item => <li key={item}><button type="button" className={model === item ? 'active' : ''} aria-pressed={model === item} onClick={() => setModel(item)}>{item}</button></li>)}</ul>}
            <input aria-label="Model" list="session-model-catalog" value={model} onChange={event => setModel(event.target.value)} placeholder="Enter a model ID" autoComplete="off" spellCheck="false" />
            <datalist id="session-model-catalog">{catalogModels.map(item => <option key={item} value={item} />)}</datalist>
            <div className="model-source-row">
              <button className="button secondary" type="button" disabled={Boolean(busy) || !provider || (selectedEndpoint?.requires_workspace && !workspaceId.trim())} onClick={refreshModels}>{busy === 'models' ? 'Refreshing…' : 'Refresh models'}</button>
              <small className="model-source-badge" data-source={liveActive ? 'live' : 'catalog'}>{liveActive ? 'Live list from provider' : 'Built-in list'}{live?.provider === provider && live.error ? ` · ${live.error}` : ''}</small>
            </div>
            <small>Pick a model above, or type any model ID this provider supports. Enter your API key first to load the live list.</small>
          </div>
          {selectedProvider?.endpoints?.length > 0 && <label className="field"><span>API region</span><select aria-label="API region" value={endpointId} onChange={event => setEndpointId(event.target.value)}>{selectedProvider.endpoints.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select><small>Choose the region where this API key was created.</small></label>}
          {selectedEndpoint?.requires_workspace && <label className="field workspace-id-field"><span>Workspace ID</span><input aria-label="Workspace ID" autoComplete="off" spellCheck="false" value={workspaceId} onChange={event => setWorkspaceId(event.target.value)} placeholder="e.g. llm-xxxxxxxx" /><small>Found on the Alibaba Cloud Model Studio workspace details page.</small></label>}
          <label className="field"><span>API key · session only</span><input type="password" aria-label="API key" autoComplete="new-password" spellCheck="false" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={currentStatus?.configured && currentStatus.provider === provider ? 'Paste a new key to replace current session key' : 'Paste API key'} /></label>
          <div className="flow-provider-actions">
            <button className="button secondary" type="button" disabled={Boolean(busy) || !provider || !model || !apiKey.trim() || (selectedEndpoint?.requires_workspace && !workspaceId.trim())} onClick={() => submit('test')}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
            <button className="flow-provider-primary" type="button" disabled={Boolean(busy) || !provider || !model || !apiKey.trim() || (selectedEndpoint?.requires_workspace && !workspaceId.trim())} onClick={() => submit('save')}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
            {currentStatus?.configured && <button className="button danger" type="button" disabled={Boolean(busy)} onClick={disconnect}>{busy === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}</button>}
            <button className="flow-provider-secondary" type="button" onClick={close}>Close</button>
          </div>
          {message && <p className="flow-provider-note">{message}</p>}
          {error && <p className="error-banner" role="alert">{error}</p>}
      </form>
    </section>
  </div>
}
