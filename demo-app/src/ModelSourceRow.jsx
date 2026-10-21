import React from 'react'

/** Refresh button, source badge, and the empty-state hint under a model picker. */
export default function ModelSourceRow({ active, error, empty, refreshing, disabled, onRefresh }) {
  return <>
    <div className="model-source-row">
      <button className="button secondary" type="button" disabled={disabled || refreshing} onClick={onRefresh}>{refreshing ? 'Refreshing…' : 'Refresh models'}</button>
      <small className="model-source-badge" data-source={active ? 'live' : 'catalog'}>{active ? 'Live list from provider' : 'Built-in list'}{error ? ` · ${error}` : ''}</small>
    </div>
    {empty && <small role="status">No models listed. Type a model ID this provider supports.</small>}
  </>
}
