/** Model IDs offered in the Configure LLM / SQL auth pickers.
 *
 * The API is the source of truth: the backend reads most provider catalogs from
 * the embedded Pi SDK, so upgrading the pinned SDK refreshes the picker. These
 * constants only cover the case where the catalog has not loaded yet or the
 * backend could not reach the SDK, and are deliberately short.
 */
export const OFFICIAL_LLM_MODELS = Object.freeze({
  qwen: Object.freeze(['qwen3.7-flash', 'qwen3.8-max', 'qwen-plus']),
  deepseek: Object.freeze(['deepseek-v4-flash', 'deepseek-v4-pro']),
  zhipu: Object.freeze(['glm-4.7-flashx', 'glm-5.2']),
  openai: Object.freeze(['gpt-5-mini', 'gpt-4.1-mini']),
  claude: Object.freeze(['claude-haiku-4-5', 'claude-sonnet-4-5']),
  gemini: Object.freeze(['gemini-2.5-flash', 'gemini-2.0-flash']),
})

export function officialModelsFor(providerId) {
  return OFFICIAL_LLM_MODELS[providerId] || []
}

/** Prefer the catalog the backend resolved; fall back to the bundled list. */
export function modelsForProvider(providerEntry, providerId) {
  const resolved = providerEntry?.models
  if (Array.isArray(resolved) && resolved.length) return resolved
  return officialModelsFor(providerId || providerEntry?.id)
}

/** Ask the backend which models this key can use; never throws.
 *
 * `post(path, body)` is the caller's JSON poster. The key is sent only when the
 * user typed one, so a server-held key (local .env or the hosted session) is
 * used otherwise.
 */
export async function fetchLiveModels(post, { provider, apiKey = '', endpointId = '', workspaceId = '', refresh = false }) {
  const body = { provider, endpoint_id: endpointId, workspace_id: workspaceId, refresh }
  if (apiKey.trim()) body.api_key = apiKey.trim()
  try {
    const data = await post('/api/llm/models', body)
    return {
      provider,
      models: Array.isArray(data?.models) ? data.models : [],
      source: data?.models_source === 'live' ? 'live' : 'fallback',
      error: data?.models_error || null,
    }
  } catch (error) {
    return { provider, models: [], source: 'fallback', error: error?.message || 'Could not list models.' }
  }
}
