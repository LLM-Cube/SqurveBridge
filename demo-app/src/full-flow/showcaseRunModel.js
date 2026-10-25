// A mock run of the recorded experiment: each method's job takes as long, relative to the others,
// as it really did, squeezed into a few seconds so the results can appear "after the run".
export const MOCK_SECONDS = { slowest: 9, fastest: 2.5 }

const clamp = (value, low, high) => Math.min(high, Math.max(low, value))

export function mockJobs(batch) {
  const wall = Object.values(batch.runs).map(run => run.source.wall_seconds || 0)
  const longest = Math.max(...wall, 1)
  return Object.entries(batch.runs).map(([id, run]) => ({
    id,
    total: batch.sample_count,
    seconds: clamp((MOCK_SECONDS.slowest * (run.source.wall_seconds || 0)) / longest, MOCK_SECONDS.fastest, MOCK_SECONDS.slowest),
  }))
}

export const totalSeconds = jobs => Math.max(...jobs.map(job => job.seconds))

export function jobsAt(jobs, elapsed) {
  return jobs.map(job => {
    const progress = clamp(elapsed / job.seconds, 0, 1)
    return { ...job, progress, completed: Math.floor(progress * job.total), done: progress >= 1 }
  })
}

export const allDone = (jobs, elapsed) => jobsAt(jobs, elapsed).every(job => job.done)
