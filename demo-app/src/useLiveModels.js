import { useState } from 'react'
import { fetchLiveModels, modelAfterRefresh } from './llmCatalog.js'

/** Live model listing state shared by both Configure LLM dialogs. */
export function useLiveModels(post) {
  const [live, setLive] = useState(null)
  const [refreshing, setRefreshing] = useState(false)

  /** Refresh and return the model to show: a chosen model is never replaced. */
  const refresh = async (args, currentModel) => {
    setRefreshing(true)
    const result = await fetchLiveModels(post, { ...args, refresh: true })
    setLive(result)
    setRefreshing(false)
    return modelAfterRefresh(currentModel, result)
  }

  const forProvider = (provider, builtIn) => {
    const mine = live?.provider === provider ? live : null
    const active = Boolean(mine && mine.source === 'live' && mine.models.length > 0)
    return { active, models: active ? mine.models : builtIn, error: mine?.error || null }
  }

  return { refreshing, refresh, forProvider, reset: () => setLive(null) }
}
