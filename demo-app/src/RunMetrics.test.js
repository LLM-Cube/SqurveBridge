import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { register } from 'tsx/esm/api'
import { renderToStaticMarkup } from 'react-dom/server'
const unregister = register()
globalThis.React = React
const { default: RunMetrics } = await import('./RunMetrics.jsx')
test.after(unregister)
const view = data => renderToStaticMarkup(React.createElement(RunMetrics, { data, t: key => key }))
test('four layers bind real fields and only positive degradation highlights C2I', () => {
  const data = { metrics: { ex: 1, sf1: .5, rves: .8, ves: 2, em: 0 }, ex_variant: 'custom', process: { refinement_degradation_rate: .2 }, sample_evidence: [{ ex: 0, c2i: true }] }
  const html = view(data)
  for (const layer of ['L1', 'L2', 'L3', 'L4']) assert.ok(html.includes(layer))
  assert.ok(html.includes('EX · custom'))
  assert.equal((html.match(/class="metric-degradation"/g) || []).length, 2)
  assert.equal((view({ ...data, process: {} }).match(/class="metric-degradation"/g) || []).length, 0)
  assert.equal((html.match(/scope="col"/g) || []).length, 8)
})
