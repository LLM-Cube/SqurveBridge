// Switching between tabs of different heights must not make the page jump: the panel area never shrinks.
import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { useStableHeight } = await import('./useStableHeight.js')
const { default: ShowcaseMetrics } = await import('./ShowcaseMetrics.jsx')
const { translate } = await import('../i18n/index.js')
const css = readFileSync(new URL('./showcaseMetrics.css', import.meta.url), 'utf8')

// jsdom has no layout, so give the measured box a height we choose.
let heightOf = () => 0
Object.defineProperty(globalThis.HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return heightOf(this) } })

function Probe({ height }) {
  const [ref, style] = useStableHeight()
  heightOf = () => height
  return React.createElement('div', { ref, style, 'data-testid': 'box' })
}

test.after(() => { cleanup(); unregister(); closeDom() })

test('the minimum height follows the tallest content seen and never goes back down', () => {
  cleanup()
  const { rerender } = render(React.createElement(Probe, { height: 400 }))
  assert.equal(screen.getByTestId('box').style.minHeight, '400px')
  rerender(React.createElement(Probe, { height: 900 }))
  assert.equal(screen.getByTestId('box').style.minHeight, '900px', 'taller content raises it')
  rerender(React.createElement(Probe, { height: 250 }))
  assert.equal(screen.getByTestId('box').style.minHeight, '900px', 'shorter content does not lower it')
})

test('with no measurable height (nothing rendered yet) no minimum is imposed', () => {
  cleanup()
  render(React.createElement(Probe, { height: 0 }))
  assert.equal(screen.getByTestId('box').style.minHeight, '')
})

test('the level panels keep a stable area while the tabs change', () => {
  cleanup()
  const heights = { l1: 1000, l2: 600, l3: 1100, l4: 1600, l5: 1300, l6: 800 }
  heightOf = element => (element.classList.contains('showcase-metrics-panels') ? heights[element.querySelector('[role=tabpanel]').dataset.level] : 0)
  render(React.createElement(ShowcaseMetrics, { t: (key, params) => translate('en-US', key, params) }))
  const area = () => document.querySelector('.showcase-metrics-panels')
  const seen = []
  for (const level of ['l1', 'l2', 'l4', 'l6', 'l5']) {
    fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${level.toUpperCase()} `) }))
    seen.push(Number.parseInt(area().style.minHeight, 10))
  }
  assert.deepEqual(seen, [1000, 1000, 1600, 1600, 1600], 'a shorter level never pulls the page up')
  assert.ok(seen.every((value, index) => index === 0 || value >= seen[index - 1]))
})

test('the tab bar is pinned while the panels scroll, and the scroll area reserves its scrollbar', () => {
  const rule = selector => new RegExp(`${selector.replace(/[.[\]:()]/g, '\\$&')}\\s*\\{[^}]*`).exec(css)?.[0] || ''
  assert.match(rule('.showcase-metrics-controls'), /position:\s*sticky/)
  assert.match(rule('.showcase-metrics-controls'), /top:\s*0/)
  assert.match(rule('.showcase-metrics-controls'), /background:/, 'opaque, so content does not show through it')
  assert.match(css, /scrollbar-gutter:\s*stable/)
})
