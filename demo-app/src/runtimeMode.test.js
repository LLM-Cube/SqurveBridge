import test from 'node:test'
import assert from 'node:assert/strict'
import { agentEnabledIn, deploymentTarget, featureEnabled, studioSurface } from './runtimeMode.js'

test('defaults to the complete local Demo App', () => {
  assert.equal(deploymentTarget(null), 'local')
  assert.equal(featureEnabled(null, 'agent_chat'), true)
  assert.equal(studioSurface(null), 'workspace')
})

test('uses hosted features returned by Flask', () => {
  const capabilities = {
    deployment: {
      target: 'hf-space',
      features: { live_sql: true, agent_chat: true, live_evaluation: false },
    },
  }
  assert.equal(deploymentTarget(capabilities), 'hf-space')
  assert.equal(featureEnabled(capabilities, 'live_sql'), true)
  assert.equal(featureEnabled(capabilities, 'agent_chat'), true)
  assert.equal(studioSurface(capabilities), 'live-sql')
})

test('only an explicit server off removes the Agent', () => {
  const agentChat = value => ({ deployment: { target: 'local', features: { agent_chat: value } } })
  assert.equal(agentEnabledIn(null), true)
  assert.equal(agentEnabledIn({ deployment: { target: 'local', features: {} } }), true)
  assert.equal(agentEnabledIn(agentChat(true)), true)
  assert.equal(agentEnabledIn(agentChat(false)), false)
})
