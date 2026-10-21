import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeProposal, pageContextFor } from './agentActions.js'

const configs = [
  { method: 'c3sql', dataset: 'spider' },
  { method: 'dinsql', dataset: 'spider' },
]

test('pageContextFor sends config ids for the page, experiment and selection', () => {
  assert.deepEqual(pageContextFor({
    activeStep: 'evidence',
    experimentId: 'exp-1',
    connections: [{ method: 'C3SQL', database: 'Spider' }, { method: 'DINSQL', database: 'Spider' }, { method: 'SEDE', database: 'BIRD' }],
    configs,
    focusedMethod: 'C3SQL',
    focusedDatabase: 'Spider',
  }), {
    page: 'evidence',
    experiment_id: 'exp-1',
    cells: [{ method: 'c3sql', dataset: 'spider' }, { method: 'dinsql', dataset: 'spider' }],
    focused_cell: { method: 'c3sql', dataset: 'spider' },
  })
  assert.deepEqual(pageContextFor({ activeStep: 'query', connections: [], configs }), { page: 'query' })
})

test('normalizeProposal accepts known actions with well-formed arguments', () => {
  assert.deepEqual(normalizeProposal({
    id: 'p1',
    action: 'start_experiment',
    args: { cells: [{ method: 'c3sql', dataset: 'spider' }], sampling: { limit: 20, mode: 'slice', seed: 42 } },
  }), {
    id: 'p1',
    action: 'start_experiment',
    args: { cells: [{ method: 'c3sql', dataset: 'spider' }], sampling: { limit: 20, mode: 'slice', seed: 42 } },
  })
  assert.deepEqual(
    normalizeProposal({ id: 'p2', action: 'open_query', args: { db_id: 'concert_singer', question: 'How many?', method: 'c3sql', dataset: 'spider' } }).args,
    { db_id: 'concert_singer', question: 'How many?', method: 'c3sql', dataset: 'spider' },
  )
})

test('normalizeProposal refuses unknown actions, bad ids, and runs on the public Space', () => {
  assert.equal(normalizeProposal({ id: 'x', action: 'delete', args: {} }), null)
  assert.equal(normalizeProposal({ id: 'x', action: 'open_cell', args: { method: '../x', dataset: 'spider' } }), null)
  assert.equal(normalizeProposal({ id: 'x', action: 'start_experiment', args: { cells: [] } }), null)
  assert.equal(normalizeProposal({
    id: 'x', action: 'start_experiment', args: { cells: [{ method: 'c3sql', dataset: 'spider' }], sampling: { limit: 20, mode: 'slice', seed: 42 } },
  }, { hosted: true }), null)
  assert.equal(normalizeProposal({ id: 'x', action: 'start_experiment', args: { cells: [{ method: 'c3sql', dataset: 'spider' }], sampling: { limit: 7 } } }), null)
})
