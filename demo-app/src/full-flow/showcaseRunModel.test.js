import assert from 'node:assert/strict'
import test from 'node:test'

import record from './showcaseRecord.js'
import { allDone, jobsAt, MOCK_SECONDS, mockJobs, totalSeconds } from './showcaseRunModel.js'

const batch = record.batches.at(-1)
const jobs = mockJobs(batch)
const byId = Object.fromEntries(jobs.map(job => [job.id, job]))

test('there is one job per recorded method, answering the recorded number of questions', () => {
  assert.deepEqual(jobs.map(job => job.id), Object.keys(batch.runs))
  assert.ok(jobs.every(job => job.total === batch.sample_count))
})

test('durations follow the recorded wall times, slowest method longest, all within the mock range', () => {
  const wall = id => batch.runs[id].source.wall_seconds
  const slowest = jobs.reduce((a, b) => (wall(a.id) >= wall(b.id) ? a : b))
  assert.equal(byId[slowest.id].seconds, MOCK_SECONDS.slowest)
  for (const job of jobs) assert.ok(job.seconds >= MOCK_SECONDS.fastest && job.seconds <= MOCK_SECONDS.slowest, job.id)
  assert.ok(byId.c3sql.seconds > byId.unisar.seconds, 'c3sql took longer than unisar in the real run')
  assert.equal(totalSeconds(jobs), MOCK_SECONDS.slowest)
})

test('nothing has progressed before the run starts, everything has when it ends', () => {
  const start = jobsAt(jobs, 0)
  assert.ok(start.every(job => job.progress === 0 && job.completed === 0 && !job.done))
  assert.equal(allDone(jobs, 0), false)
  const end = jobsAt(jobs, totalSeconds(jobs))
  assert.ok(end.every(job => job.progress === 1 && job.completed === job.total && job.done))
  assert.equal(allDone(jobs, totalSeconds(jobs)), true)
  assert.equal(allDone(jobs, totalSeconds(jobs) - 0.01), false, 'the slowest job is still running just before the end')
})

test('progress only moves forward and faster methods finish first', () => {
  let last = 0
  for (let t = 0; t <= 9; t += 0.5) {
    const now = jobsAt(jobs, t).find(job => job.id === 'c3sql')
    assert.ok(now.progress >= last)
    last = now.progress
  }
  const mid = jobsAt(jobs, byId.unisar.seconds + 0.01)
  assert.equal(mid.find(job => job.id === 'unisar').done, true)
  assert.equal(mid.find(job => job.id === 'c3sql').done, false)
})

test('negative or oversized elapsed times are clamped', () => {
  assert.ok(jobsAt(jobs, -5).every(job => job.progress === 0))
  assert.ok(jobsAt(jobs, 999).every(job => job.progress === 1))
})
