export function deploymentTarget(capabilities) {
  return capabilities?.deployment?.target || 'local'
}

export function featureEnabled(capabilities, name) {
  const features = capabilities?.deployment?.features
  return features ? features[name] === true : true
}

// Only an explicit server "off" removes the Agent; older payloads keep it.
export function agentEnabledIn(capabilities) {
  return capabilities?.deployment?.features?.agent_chat !== false
}

export function studioSurface(capabilities) {
  return deploymentTarget(capabilities) === 'hf-space' ? 'live-sql' : 'workspace'
}
