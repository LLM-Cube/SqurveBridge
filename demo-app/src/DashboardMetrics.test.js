import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { register } from 'tsx/esm/api'
import { renderToStaticMarkup } from 'react-dom/server'
import messages from './i18n/en-US.js'
const unregister = register()
globalThis.React = React
const { default: DashboardMetrics } = await import('./DashboardMetrics.jsx')
test.after(unregister)
test('dashboard partitions correctness, cost, structure, process and sample evidence', () => {
  const html = renderToStaticMarkup(React.createElement(DashboardMetrics, {
    runs: [{ run_id: 'synthetic', method: 'method', ex_variant: 'custom', sample_count: 2, aggregate: { ex: { avg: .5, valid: 2, total: 2 } }, process: { refinement_degradation_rate: .1 } }],
    t: key => messages[key] || key,
  }))
  for (const heading of ['Experiment scope', 'Correctness', 'Efficiency and cost', 'Structural diagnostics', 'Process and errors', 'Sample evidence']) assert.ok(html.includes(heading), heading)
  assert.ok(html.includes('50.0%'))
  assert.ok(html.includes('Not observed / not computable'))
  assert.ok(html.includes('2/2 observed'))
  assert.ok(html.includes('EX · custom'))
  assert.ok(html.includes('metric-degradation'))
})
