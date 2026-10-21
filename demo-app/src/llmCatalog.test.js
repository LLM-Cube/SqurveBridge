import assert from 'node:assert/strict'
import test from 'node:test'

import { OFFICIAL_LLM_MODELS, fetchLiveModels, modelsForProvider, officialModelsFor } from './llmCatalog.js'

test('bundled catalog covers every configurable provider', () => {
  assert.deepEqual(Object.keys(OFFICIAL_LLM_MODELS).sort(), [
    'claude',
    'deepseek',
    'gemini',
    'openai',
    'qwen',
    'zhipu',
  ])
  assert.equal(officialModelsFor('qwen').includes('qwen3-custom-latest'), false)
  assert.deepEqual(officialModelsFor('nonexistent'), [])
})

test('prefers the model catalog the backend resolved from the Pi SDK', () => {
  const entry = { id: 'deepseek', models: ['deepseek-v4-flash', 'deepseek-v4-pro'] }
  assert.deepEqual(modelsForProvider(entry, 'deepseek'), ['deepseek-v4-flash', 'deepseek-v4-pro'])
})

test('falls back to the bundled catalog when the backend sends no models', () => {
  assert.deepEqual(modelsForProvider({ id: 'qwen', models: [] }, 'qwen'), officialModelsFor('qwen'))
  assert.deepEqual(modelsForProvider(undefined, 'qwen'), officialModelsFor('qwen'))
  assert.deepEqual(modelsForProvider({ id: 'qwen' }, undefined), officialModelsFor('qwen'))
})

test('fetchLiveModels posts the provider and key and returns the live list', async () => {
  const calls = []
  const post = async (path, body) => {
    calls.push({ path, body })
    return { models: ['deepseek-flash'], models_source: 'live', models_error: null }
  }
  const live = await fetchLiveModels(post, {
    provider: 'deepseek', apiKey: ' k ', endpointId: '', workspaceId: '', refresh: true,
  })
  assert.deepEqual(live, { provider: 'deepseek', models: ['deepseek-flash'], source: 'live', error: null })
  assert.equal(calls[0].path, '/api/llm/models')
  assert.deepEqual(calls[0].body, {
    provider: 'deepseek', api_key: 'k', endpoint_id: '', workspace_id: '', refresh: true,
  })
})

test('fetchLiveModels omits an empty key so the server can use its own', async () => {
  const calls = []
  await fetchLiveModels(async (_path, body) => { calls.push(body); return { models: [] } }, { provider: 'qwen', apiKey: '  ' })
  assert.equal('api_key' in calls[0], false)
})

test('fetchLiveModels reports a failed request as a fallback without throwing', async () => {
  const live = await fetchLiveModels(async () => { throw new Error('offline') }, { provider: 'openai', apiKey: 'k' })
  assert.deepEqual(live, { provider: 'openai', models: [], source: 'fallback', error: 'offline' })
})
