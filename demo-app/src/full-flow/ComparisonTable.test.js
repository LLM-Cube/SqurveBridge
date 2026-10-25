import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, render, screen } = await import('@testing-library/react')
const unregister = register()
const { default: ComparisonTable } = await import('./ComparisonTable.jsx')

const t = (key, params = {}) => [key, ...Object.values(params)].join(':')

test.afterEach(cleanup)
test.after(() => {
  unregister()
  closeDom()
})

test('each metric column marks its best member and shows every other gap in points', () => {
  render(React.createElement(ComparisonTable, {
    benchmark: 'Spider',
    metrics: [{ id: 'ex', label: 'Execution accuracy' }],
    rows: [
      { method: 'C3SQL', label: 'C3SQL', values: { ex: 0.8 }, sampleCount: 20 },
      { method: 'DINSQL', label: 'DINSQL', values: { ex: 0.77 }, sampleCount: 20 },
      { method: 'FinSQL', label: 'FinSQL', values: { ex: null }, sampleCount: null },
    ],
    t,
  }))
  const best = screen.getByRole('row', { name: /C3SQL/ })
  assert.equal(best.getAttribute('data-best'), 'true')
  assert.match(screen.getByRole('row', { name: /DINSQL/ }).textContent, /-3\.0/)
  assert.match(screen.getByRole('row', { name: /FinSQL/ }).textContent, /—/)
  assert.equal(screen.getByRole('row', { name: /DINSQL/ }).getAttribute('data-best'), 'false')
})
