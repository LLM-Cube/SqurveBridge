import assert from 'node:assert/strict'
import { register as registerLoader } from 'node:module'
import test from 'node:test'
import React from 'react'
import { register } from 'tsx/esm/api'
import { installTestDom } from '../testDom.js'

const closeDom = installTestDom()
globalThis.React = React
const { cleanup, fireEvent, render, screen, within } = await import('@testing-library/react')
registerLoader('../cssTestLoader.mjs', import.meta.url)
const unregister = register()
const { default: ShowcaseMetrics } = await import('./ShowcaseMetrics.jsx')
const { PairBars } = await import('./ShowcaseCharts.jsx')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)
const mount = () => { cleanup(); render(React.createElement(ShowcaseMetrics, { t })) }
const table = name => screen.getByRole('table', { name })
const cell = (name, row, method) => {
  const grid = table(name)
  const column = within(grid).getAllByRole('columnheader').findIndex(header => header.textContent === method)
  const line = within(grid).getAllByRole('row').find(item => within(item).queryByRole('rowheader')?.textContent === row)
  return within(line).getAllByRole('cell')[column - 1]
}

test.after(() => { cleanup(); unregister(); closeDom() })

test('opens on the latest batch and shows every method with its validity', () => {
  mount()
  assert.equal(screen.getAllByRole('button', { name: /-question batch/ }).length, 2)
  assert.equal(screen.getByRole('button', { name: '50-question batch' }).getAttribute('aria-pressed'), 'true')
  const validity = screen.getByLabelText('Run validity')
  const item = method => validity.querySelector(`[data-method="${method}"]`)
  assert.ok(item('c3sql').className.includes('valid') && !item('c3sql').className.includes('invalid'))
  for (const method of ['dinsql', 'finsql', 'unisar']) assert.ok(item(method).className.includes('invalid'), method)
  assert.match(validity.textContent, /Ranking \(valid runs only\): c3sql$/)
  assert.match(validity.textContent, /Left out of ranking and comparisons: dinsql, finsql, unisar/)
})

test('the page proves the methods answered the same questions and says why a run failed', () => {
  mount()
  const validity = screen.getByLabelText('Run validity')
  assert.match(validity.textContent, /every method answered the same 50 questions \(fingerprint [0-9a-f]{12}\)/)
  assert.match(validity.querySelector('[data-method="unisar"]').textContent, /52% degenerate · 88 connection errors · 26 retries exhausted/)
  fireEvent.click(screen.getByRole('button', { name: '20-question batch' }))
  assert.match(screen.getByLabelText('Run validity').textContent, /same 20 questions/)
})

test('answer quality comes from the recorded runs, and an uncomputable metric is not a zero', () => {
  mount()
  assert.equal(cell('Answer quality', 'ex', 'c3sql').textContent, '0.760')
  assert.equal(cell('Answer quality', 'sc', 'c3sql').textContent, 'Not computable')
  assert.equal(cell('Answer quality', 'sc', 'c3sql').getAttribute('title'), 'require generate_num>=2')
  fireEvent.click(screen.getByRole('button', { name: '20-question batch' }))
  assert.equal(cell('Answer quality', 'ex', 'c3sql').textContent, '0.750')
})

test('a count metric is shown as a whole number, a ratio with three decimals', () => {
  mount()
  assert.equal(cell('Process metrics', 'outcome_observed', 'c3sql').textContent, '50')
  assert.equal(cell('Process metrics', 'outcome_coverage', 'c3sql').textContent, '1.000')
})

test('cost, breakdowns, errors, components, stages and process metrics are all shown', () => {
  mount()
  for (const name of ['Cost', 'By difficulty (EX · questions)', 'By database type (EX · questions)', 'By SQL feature (EX · questions)',
    'By scenario (EX · questions)', 'Error roots (count · share)', 'Component F1', 'SQL structure delta (mean ± std)',
    'Stage metrics', 'Process metrics', 'Bottlenecks (questions)']) {
    assert.ok(table(name), name)
  }
  assert.equal(cell('Cost', 'Total tokens', 'c3sql').textContent, '61269')
  assert.equal(cell('By difficulty (EX · questions)', 'easy', 'c3sql').textContent.includes(' · '), true)
  assert.equal(cell('Process metrics', 'refinement_fix_rate', 'c3sql').textContent, 'Not computable')
})

test('the question matrix is anonymous and has one row per question of the batch', () => {
  mount()
  const rows = within(table('Question by question')).getAllByRole('row')
  assert.equal(rows.length, 1 + 50)
  assert.equal(within(rows[1]).getAllByRole('cell')[0].textContent, 'q1')
  fireEvent.click(screen.getByRole('button', { name: '20-question batch' }))
  assert.equal(within(table('Question by question')).getAllByRole('row').length, 1 + 20)
})

test('with a single valid run there is nothing to compare pairwise', () => {
  mount()
  assert.ok(screen.getByText(/Fewer than two valid runs/))
  assert.equal(screen.queryByRole('table', { name: /Pairwise comparison/ }), null)
})

const chart = name => screen.getByRole('img', { name })
const marks = (name, selector) => [...chart(name).querySelectorAll(selector)]

test('every metric group that compares methods has a chart, each with a table view', () => {
  mount()
  for (const title of ['Answer quality', 'Cost vs quality', 'Latency avg (s)', 'By difficulty (EX · questions)', 'By SQL feature (EX · questions)',
    'By scenario (EX · questions)', 'Error roots (count · share)', 'Component F1']) {
    assert.ok(chart(`${title} chart`), title)
  }
  const tableViews = [...document.querySelectorAll('details.showcase-metrics-tableview')]
  assert.ok(tableViews.length >= 7, 'the exact numbers stay one click away')
  assert.ok(tableViews.every(view => view.querySelector('table')))
})

test('quality bars are scaled to their value and carry the confidence interval', () => {
  mount()
  const bars = marks('Answer quality chart', '[data-metric="ex"]')
  const c3 = bars.find(bar => bar.dataset.method === 'c3sql')
  const dinsql = bars.find(bar => bar.dataset.method === 'dinsql')
  assert.equal(c3.dataset.value, '0.76')
  const height = bar => Number(/V([\d.]+)Q/.exec(bar.querySelector('path').getAttribute('d'))[1])
  const bottom = bar => Number(/M[\d.]+,([\d.]+)V/.exec(bar.querySelector('path').getAttribute('d'))[1])
  const px = bar => bottom(bar) - (height(bar) - 4)
  assert.ok(px(c3) > px(dinsql), 'a higher EX draws a taller bar')
  assert.ok(Math.abs(px(c3) / px(dinsql) - 0.76 / 0.66) < 0.02, 'bar height is proportional to the value')
  assert.equal(c3.querySelectorAll('line').length, 3, 'a whisker with two caps for the interval')
  assert.equal(dinsql.querySelectorAll('line').length, 3)
})

test('invalid runs are hatched everywhere, valid runs are solid', () => {
  mount()
  const bars = marks('Answer quality chart', '[data-metric="ex"]')
  assert.equal(bars.find(bar => bar.dataset.method === 'c3sql').querySelector('path').getAttribute('fill'), 'var(--series-1)')
  for (const method of ['dinsql', 'finsql', 'unisar']) {
    const bar = bars.find(item => item.dataset.method === method)
    assert.equal(bar.dataset.valid, 'false')
    assert.match(bar.querySelector('path').getAttribute('fill'), /^url\(#.+-h-/, method)
  }
  const legend = within(screen.getAllByLabelText('Method legend')[0]).getAllByRole('listitem')
  assert.equal(legend.length, 4, 'a legend for two or more series')
  assert.deepEqual(legend.map(item => item.dataset.valid), ['true', 'false', 'false', 'false'])
  assert.match(legend[1].textContent, /dinsql · invalid run/)
})

test('a method keeps its colour in every chart', () => {
  mount()
  const colour = (name, selector, method) => marks(name, `${selector}[data-method="${method}"]`)[0]
  assert.equal(colour('Cost vs quality chart', 'g', 'unisar').querySelector('circle').getAttribute('stroke'), 'var(--series-7)')
  assert.equal(colour('Latency avg (s) chart', 'g', 'unisar').querySelector('line').getAttribute('stroke'), 'var(--series-7)')
  assert.equal(colour('Cost vs quality chart', 'g', 'c3sql').querySelector('circle').getAttribute('fill'), 'var(--series-1)')
})

test('the cost chart places methods by tokens (x) and EX (y) on one axis each', () => {
  mount()
  const point = method => {
    const circle = marks('Cost vs quality chart', `g[data-method="${method}"]`)[0].querySelector('circle')
    return { x: Number(circle.getAttribute('cx')), y: Number(circle.getAttribute('cy')) }
  }
  const [c3, din, fin, uni] = ['c3sql', 'dinsql', 'finsql', 'unisar'].map(point)
  assert.ok(uni.x < fin.x && fin.x < c3.x && c3.x < din.x, 'fewer tokens sit further left')
  assert.ok(c3.y < din.y && din.y < fin.y && fin.y < uni.y, 'higher EX sits higher (smaller y)')
})

test('hovering a mark shows its value and an invalid run says so', () => {
  mount()
  const dinsql = marks('Answer quality chart', '[data-metric="ex"]').find(bar => bar.dataset.method === 'dinsql')
  fireEvent.mouseEnter(dinsql, { clientX: 10, clientY: 10 })
  const tip = screen.getByRole('tooltip')
  assert.match(tip.textContent, /dinsql · EX 0\.660/)
  assert.match(tip.textContent, /invalid run/)
  fireEvent.mouseLeave(dinsql)
  assert.equal(screen.queryByRole('tooltip'), null)
})

test('features without any question are left out rather than drawn as zero', () => {
  mount()
  assert.equal(marks('By scenario (EX · questions) chart', '[data-row="complex_predicate"]').length, 0)
  assert.equal(marks('By SQL feature (EX · questions) chart', '[data-row="window>0"]').length, 0)
  const nested = marks('By scenario (EX · questions) chart', '[data-row="nested_or_set"][data-method="c3sql"]')[0]
  assert.ok(Math.abs(Number(nested.dataset.value) - 9 / 11) < 1e-9, '9 of 11 nested/set questions were correct')
  assert.equal(nested.textContent, '82%')
})

test('component F1 cells show the recorded value', () => {
  mount()
  const join = marks('Component F1 chart', '[data-row="join"][data-method="c3sql"]')[0]
  assert.ok(Math.abs(Number(join.dataset.value) - 0.97) < 1e-9)
  assert.equal(join.textContent, '0.97')
})

test('each method gets its own error panel on a shared scale', () => {
  mount()
  const panels = [...chart('Error roots (count · share) chart').querySelectorAll('.showcase-panel-mini')]
  assert.deepEqual(panels.map(panel => panel.dataset.method), ['c3sql', 'dinsql', 'finsql', 'unisar'])
  const longest = panel => Math.max(...[...panel.querySelectorAll('[data-error]')].map(bar => Number(bar.dataset.value)))
  assert.equal(longest(panels[0]), 7)
  assert.equal(longest(panels[3]), 45)
})

test('summary tiles show each method headline and mark the invalid ones', () => {
  mount()
  const tiles = within(screen.getByRole('list', { name: 'Method summary' })).getAllByRole('listitem')
  assert.deepEqual(tiles.map(tile => tile.dataset.method), ['c3sql', 'dinsql', 'finsql', 'unisar'])
  assert.match(tiles[0].textContent, /0\.76.*EX · 38\/50/)
  assert.deepEqual(tiles.map(tile => tile.dataset.valid), ['true', 'false', 'false', 'false'])
  assert.ok(tiles[1].className.includes('invalid'))
})

test('structure delta: biggest strays first, zero-gap features folded into a note', () => {
  mount()
  const name = 'SQL structure delta (mean ± std) chart'
  const rows = [...new Set(marks(name, '[data-row]').map(row => row.dataset.row))]
  assert.deepEqual(rows.slice(0, 2), ['predicate', 'query_fields'], 'unisar strays furthest on predicates (-0.96), then query fields (-0.94)')
  assert.ok(!rows.includes('window') && !rows.includes('math_compute'), 'features with no gap are not drawn')
  assert.match(screen.getByText(/more show no difference/).textContent, /window/)
  const dot = method => Number(marks(name, `[data-row="predicate"] [data-method="${method}"] circle`)[0].getAttribute('cx'))
  assert.ok(dot('unisar') < dot('finsql') && dot('finsql') < dot('c3sql') && dot('c3sql') < dot('dinsql'), 'negative gaps sit left of the zero line, positive right')
})

test('stage metrics are drawn per method, only for stages that were computed', () => {
  mount()
  const name = 'Stage metrics chart'
  const stages = method => marks(name, `[data-method="${method}"][data-stage]`)
  assert.deepEqual(stages('c3sql').map(item => [item.dataset.stage, Number(item.dataset.value)]),
    [['c3sql_reduce · reduce_rate', 1], ['c3sql_generate · execute_accuracy', 0.75]])
  assert.equal(stages('finsql').length, 3)
  assert.equal(marks(name, '[data-method="dinsql"][data-stage]').length, 0)
  assert.ok(within(chart(name)).getByText('No computable stage metrics.'))
})

test('outcomes add up to every question and the unobserved process metrics are named, not zeroed', () => {
  mount()
  const parts = method => Object.fromEntries(marks('Outcomes chart', `g[data-method="${method}"] [data-outcome]`).map(part => [part.dataset.outcome, Number(part.dataset.value)]))
  assert.deepEqual(parts('c3sql'), { correct: 0.76, wrong: 0.22, error: 0.02 })
  const unisar = parts('unisar')
  assert.ok(unisar.error > 0.85, 'most UniSAR questions ended in an execution error')
  assert.ok(Math.abs(Object.values(unisar).reduce((a, b) => a + b, 0) - 1) < 1e-9)
  assert.match(screen.getByTestId('unobserved').textContent, /not a score of 0.*refinement_fix_rate/)
  assert.ok(chart('Generation quality chart'))
})

test('pair bars split the questions into both / only one / neither in proportion', () => {
  cleanup()
  const pairs = [{ a: 'c3sql', b: 'dinsql', both: 30, only_a: 5, only_b: 10, neither: 5 }]
  render(React.createElement(PairBars, { label: 'pairs', pairs, t }))
  const widths = Object.fromEntries([...document.querySelectorAll('[data-part]')].map(part => [part.dataset.part, Number(part.querySelector('rect').getAttribute('width'))]))
  assert.deepEqual(Object.keys(widths), ['both', 'only_a', 'only_b', 'neither'])
  assert.ok(Math.abs((widths.both + 2) / (widths.only_b + 2) - 3) < 0.01, '30 vs 10 questions is a 3:1 width')
  assert.ok(Math.abs((widths.only_a + 2) - (widths.neither + 2)) < 0.01, 'equal counts, equal widths')
  const colour = part => document.querySelector(`[data-part="${part}"] rect`).getAttribute('fill')
  assert.equal(colour('only_a'), 'var(--series-1)')
  assert.equal(colour('only_b'), 'var(--series-2)')
})

test('the page shows no benchmark questions, SQL, identifiers or paths', () => {
  mount()
  const text = document.body.textContent
  assert.doesNotMatch(text, /\bSELECT\b[^.]*\bFROM\b|dev_\d+|\/Users\/|Connection error/)
})
