import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import fixture from './showcaseFixture.js'

const contract = JSON.parse(readFileSync(new URL('../../../tests/fixtures/run_timeline_contract.json', import.meta.url), 'utf8'))

// The same file is checked against the live timeline in tests/test_run_timeline.py.
test('every recorded question and method run has the fields a live run provides', () => {
  const missing = []
  const need = (where, value, keys) => keys.forEach(key => { if (!(key in value)) missing.push(`${where}.${key}`) })
  for (const question of fixture.questions) {
    need(question.id, { ...question, question: question.question }, contract.question)
    for (const [id, run] of Object.entries(question.methods)) {
      need(`${question.id}/${id}`, run, contract.run)
      run.stages.forEach(stage => {
        need(`${question.id}/${id}/${stage.name}`, stage, contract.stage)
        assert.ok(contract.stage_kinds.includes(stage.kind), `${stage.name} kind ${stage.kind}`)
      })
    }
  }
  assert.deepEqual(missing, [])
})
