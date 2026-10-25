// UI checks for every level of the showcase metrics: what each tab shows, that every chart is
// well-formed (finite, inside its canvas, labels that fit), reachable by hover and keyboard, and
// that the drawn numbers agree with the table view next to them.
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
const { default: record } = await import('./showcaseRecord.js')
const { translate } = await import('../i18n/index.js')

const t = (key, params) => translate('en-US', key, params)
const METHODS = ['c3sql', 'dinsql', 'finsql', 'unisar']
const batch = record.batches[0]

// What each level must show, written down independently of the component.
const LEVELS = {
  l1: {
    cards: ['Answer quality', 'By difficulty (EX · questions)', 'By database type (EX · questions)'],
    charts: ['Answer quality chart', 'By difficulty (EX · questions) chart'],
    tables: ['Answer quality', 'By difficulty (EX · questions)', 'By database type (EX · questions)'],
  },
  l2: {
    cards: ['Cost'],
    charts: ['Cost vs quality chart', 'Latency avg (s) chart'],
    tables: ['Cost'],
  },
  l3: {
    cards: ['SQL structure delta (mean ± std)', 'By SQL feature (EX · questions)', 'By scenario (EX · questions)'],
    charts: ['SQL structure delta (mean ± std) chart', 'By SQL feature (EX · questions) chart', 'By scenario (EX · questions) chart'],
    tables: ['SQL structure delta (mean ± std)', 'By SQL feature (EX · questions)', 'By scenario (EX · questions)'],
  },
  l4: {
    cards: ['Error roots (count · share)', 'Outcomes', 'Bottlenecks (questions)'],
    charts: ['Error roots (count · share) chart', 'Outcomes chart'],
    tables: ['Error roots (count · share)', 'Bottlenecks (questions)'],
  },
  l5: {
    cards: ['Component F1', 'Stage metrics', 'Process metrics'],
    charts: ['Component F1 chart', 'Stage metrics chart', 'Generation quality chart'],
    tables: ['Component F1', 'Stage metrics', 'Process metrics'],
  },
  l6: {
    cards: ['Pairwise comparison (valid runs only)', 'Question by question'],
    charts: ['Pairwise comparison (valid runs only) chart'],
    tables: ['Pairwise comparison table', 'Question by question'],
  },
}

const mount = level => {
  cleanup()
  render(React.createElement(ShowcaseMetrics, { t }))
  fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${level.toUpperCase()} `) }))
  return screen.getByRole('tabpanel')
}
test.after(() => { cleanup(); unregister(); closeDom() })

// ---- geometry ---------------------------------------------------------------------------
const num = value => (value === null || value === '' ? null : Number(value))
const GEOMETRY = ['x', 'y', 'cx', 'cy', 'r', 'width', 'height', 'x1', 'x2', 'y1', 'y2']

/** Rough rendered width of a text node: full-width CJK glyphs count 1em, others about 0.56em. */
const textWidth = element => {
  const size = num(element.getAttribute('font-size')) || 12
  return [...element.textContent].reduce((sum, char) => sum + (char.charCodeAt(0) > 0x2e80 ? size : size * 0.56), 0)
}

function inspect(svg) {
  const [, , width, height] = svg.getAttribute('viewBox').split(/\s+/).map(Number)
  const problems = []
  for (const element of svg.querySelectorAll('*')) {
    const tag = element.tagName.toLowerCase()
    for (const name of GEOMETRY) {
      const raw = element.getAttribute(name)
      if (raw === null || raw.endsWith('%')) continue
      if (!Number.isFinite(Number(raw))) problems.push(`${tag}@${name}=${raw}`)
    }
    const path = element.getAttribute('d')
    if (path && /NaN|undefined|Infinity/.test(path)) problems.push(`${tag}@d=${path}`)
    if (tag === 'rect' && !element.closest('pattern')) {
      const [x, y, w, h] = ['x', 'y', 'width', 'height'].map(name => num(element.getAttribute(name)) ?? 0)
      if (x < -1 || y < -1 || x + w > width + 1 || y + h > height + 1) problems.push(`rect outside canvas: ${x},${y},${w},${h}`)
    }
    if (tag === 'circle') {
      const [cx, cy, r] = ['cx', 'cy', 'r'].map(name => num(element.getAttribute(name)))
      if (cx - r < -1 || cx + r > width + 1 || cy - r < -1 || cy + r > height + 1) problems.push(`circle outside canvas: ${cx},${cy}`)
    }
    if (tag === 'text' && !element.getAttribute('transform')) {
      const x = num(element.getAttribute('x')) ?? 0
      const w = textWidth(element)
      const anchor = element.getAttribute('text-anchor') || 'start'
      const left = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x
      if (left < -2 || left + w > width + 2) problems.push(`text clipped: "${element.textContent}" (${Math.round(left)}..${Math.round(left + w)} of ${width})`)
    }
  }
  return problems
}

const cellText = (tableName, row, method, panel) => {
  const grid = within(panel).getByRole('table', { name: tableName })
  const column = within(grid).getAllByRole('columnheader').findIndex(header => header.textContent === method)
  const line = within(grid).getAllByRole('row').find(item => within(item).queryByRole('rowheader')?.textContent === row)
  return within(line).getAllByRole('cell')[column - 1].textContent
}

// ---- every level ------------------------------------------------------------------------
for (const [level, spec] of Object.entries(LEVELS)) {
  test(`${level}: the panel shows exactly its cards, charts and tables`, () => {
    const panel = mount(level)
    const selected = screen.getByRole('tab', { selected: true })
    assert.equal(panel.getAttribute('aria-labelledby'), selected.id, 'the panel is labelled by its tab')
    assert.equal(panel.dataset.level, level)
    assert.deepEqual([...panel.querySelectorAll('h3')].map(heading => heading.textContent.replace(/\s*$/, '')).sort(), [...spec.cards].sort())
    assert.deepEqual(within(panel).queryAllByRole('img').map(img => img.getAttribute('aria-label')).sort(), [...spec.charts].sort())
    for (const name of spec.tables) assert.ok(within(panel).getByRole('table', { name }), name)
  })

  test(`${level}: nothing renders as NaN, undefined or null, and no card is empty`, () => {
    const panel = mount(level)
    assert.doesNotMatch(panel.textContent, /NaN|undefined|null|Infinity/, 'visible text')
    for (const svg of panel.querySelectorAll('svg')) {
      assert.doesNotMatch(svg.outerHTML, /="(NaN|undefined|null|Infinity)"/, 'svg attributes')
    }
    for (const card of panel.querySelectorAll('.showcase-metrics-section')) {
      const heading = card.querySelector('h3').textContent
      assert.ok(card.querySelector('svg, table, p.showcase-metrics-empty'), `card "${heading}" has something to show`)
    }
  })

  test(`${level}: every chart is well-formed, fits its canvas and has a labelled, unique name`, () => {
    const panel = mount(level)
    const names = []
    for (const img of within(panel).queryAllByRole('img')) {
      names.push(img.getAttribute('aria-label'))
      const svgs = [...img.querySelectorAll('svg')]
      assert.ok(svgs.length >= 1, `${img.getAttribute('aria-label')} draws an svg`)
      for (const svg of svgs) assert.deepEqual(inspect(svg), [], img.getAttribute('aria-label'))
    }
    assert.equal(new Set(names).size, names.length, 'chart names are unique')
  })

  test(`${level}: marks can be reached by hover and by keyboard focus`, () => {
    const panel = mount(level)
    for (const img of within(panel).queryAllByRole('img')) {
      const marks = [...img.querySelectorAll('svg [tabindex="0"]')]
      assert.ok(marks.length > 0, `${img.getAttribute('aria-label')} has focusable marks`)
      const figure = img.closest('figure')
      for (const mark of marks.slice(0, 3)) {
        fireEvent.mouseEnter(mark, { clientX: 8, clientY: 8 })
        const tip = within(figure).getByRole('tooltip')
        assert.ok(tip.textContent.trim().length > 3, 'the tooltip says something')
        fireEvent.mouseLeave(mark)
        assert.equal(within(figure).queryByRole('tooltip'), null)
        fireEvent.focus(mark)
        assert.ok(within(figure).getByRole('tooltip'))
        fireEvent.blur(mark)
      }
    }
  })

  test(`${level}: every table view opens onto a real table with headers`, () => {
    const panel = mount(level)
    for (const view of panel.querySelectorAll('details.showcase-metrics-tableview')) {
      assert.ok(view.querySelector('summary').textContent.length > 0)
      const grid = view.querySelector('table')
      assert.ok(grid, 'a table sits inside the table view')
      assert.ok(grid.querySelectorAll('thead th').length >= 2, 'with column headers')
      assert.ok(grid.querySelectorAll('tbody tr').length >= 1, 'and at least one row')
    }
  })
}

// ---- the page frame ----------------------------------------------------------------------
test('the six level tabs are all there, each linked to a panel, and only the selected one is tabbable', () => {
  mount('l1')
  const tabs = screen.getAllByRole('tab')
  assert.equal(tabs.length, 6)
  for (const tab of tabs) {
    assert.match(tab.getAttribute('aria-controls'), /^showcase-panel-l[1-6]$/)
    assert.equal(tab.getAttribute('tabindex'), tab.getAttribute('aria-selected') === 'true' ? '0' : '-1')
  }
  for (const level of Object.keys(LEVELS)) {
    fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${level.toUpperCase()} `) }))
    assert.equal(screen.getAllByRole('tabpanel').length, 1, 'one panel at a time')
    assert.equal(screen.getByRole('tabpanel').id, `showcase-panel-${level}`)
  }
})

test('the summary tiles and the single legend stay above the tabs whichever level is open', () => {
  for (const level of Object.keys(LEVELS)) {
    mount(level)
    assert.equal(within(screen.getByRole('list', { name: 'Method summary' })).getAllByRole('listitem').length, 4, level)
    const legend = screen.getAllByLabelText('Method legend')
    assert.equal(legend.length, 1, level)
    assert.deepEqual(within(legend[0]).getAllByRole('listitem').map(item => item.dataset.method), METHODS)
  }
})

// ---- the drawn numbers agree with their tables --------------------------------------------
test('l1: every quality bar equals its table cell', () => {
  const panel = mount('l1')
  let checked = 0
  const quality = within(panel).getByRole('img', { name: 'Answer quality chart' })
  for (const bar of quality.querySelectorAll('[data-metric][data-method]')) {
    let text
    try { text = cellText('Answer quality', bar.dataset.metric, bar.dataset.method, panel) } catch (error) {
      throw new Error(`no table cell for ${bar.dataset.method}/${bar.dataset.metric}: ${error.message.slice(0, 80)}`)
    }
    assert.equal(Number(bar.dataset.value).toFixed(3), text, `${bar.dataset.method} ${bar.dataset.metric}`)
    checked += 1
  }
  assert.ok(checked >= 15, 'the five quality metrics of the methods that have them')
})

test('l1: difficulty bars equal the table percentages and group labels carry the question counts', () => {
  const panel = mount('l1')
  const chart = within(panel).getByRole('img', { name: 'By difficulty (EX · questions) chart' })
  for (const group of chart.querySelectorAll('[data-group]')) {
    const level = group.dataset.group
    for (const bar of group.querySelectorAll('[data-method]')) {
      const [percent, count] = cellText('By difficulty (EX · questions)', level, bar.dataset.method, panel).split(' · ')
      assert.equal((Number(bar.dataset.value) * 100).toFixed(1) + '%', percent, `${bar.dataset.method} ${level}`)
      assert.ok(Number(count) > 0)
    }
  }
  assert.match(chart.textContent, /n=29/)
})

test('l2: scatter tooltips repeat the table EX and total tokens', () => {
  const panel = mount('l2')
  const figure = within(panel).getByRole('img', { name: 'Cost vs quality chart' }).closest('figure')
  for (const method of METHODS) {
    fireEvent.mouseEnter(figure.querySelector(`g[data-method="${method}"]`), { clientX: 3, clientY: 3 })
    const tip = within(figure).getByRole('tooltip').textContent
    const row = Object.fromEntries(batch.runs[method] ? [[method, batch.runs[method]]] : [])[method]
    assert.ok(tip.includes(`EX ${row.quality.ex.value.toFixed(3)}`), `${method}: ${tip}`)
    assert.equal(cellText('Cost', 'Total tokens', method, panel), String(row.cost.total_tokens))
    assert.ok(tip.includes(String(row.cost.total_tokens)), `${method}: ${tip}`)
    fireEvent.mouseLeave(figure.querySelector(`g[data-method="${method}"]`))
  }
})

test('l3: heatmap cells equal the table percentages', () => {
  const panel = mount('l3')
  let checked = 0
  for (const [chartName, tableName] of [['By SQL feature (EX · questions) chart', 'By SQL feature (EX · questions)'], ['By scenario (EX · questions) chart', 'By scenario (EX · questions)']]) {
    const chart = within(panel).getByRole('img', { name: chartName })
    for (const cell of chart.querySelectorAll('[data-value][data-row]')) {
      const [percent] = cellText(tableName, cell.dataset.row, cell.dataset.method, panel).split(' · ')
      assert.equal((Number(cell.dataset.value) * 100).toFixed(1) + '%', percent, `${cell.dataset.row} ${cell.dataset.method}`)
      checked += 1
    }
  }
  assert.ok(checked > 20)
})

test('l3: structure delta dots equal the table means', () => {
  const panel = mount('l3')
  const chart = within(panel).getByRole('img', { name: 'SQL structure delta (mean ± std) chart' })
  for (const dot of chart.querySelectorAll('[data-mean]')) {
    const row = dot.closest('[data-row]').dataset.row
    const [mean] = cellText('SQL structure delta (mean ± std)', row, dot.dataset.method, panel).split(' ± ')
    assert.equal(Number(dot.dataset.mean).toFixed(2), mean, `${row} ${dot.dataset.method}`)
  }
})

test('l4: error bars equal the table counts, and outcomes add up to one', () => {
  const panel = mount('l4')
  const errors = within(panel).getByRole('img', { name: 'Error roots (count · share) chart' })
  for (const bar of errors.querySelectorAll('[data-error]')) {
    const [count] = cellText('Error roots (count · share)', bar.dataset.error, bar.dataset.method, panel).split(' · ')
    assert.equal(bar.dataset.value, count, `${bar.dataset.method} ${bar.dataset.error}`)
  }
  const outcomes = within(panel).getByRole('img', { name: 'Outcomes chart' })
  for (const method of METHODS) {
    const total = [...outcomes.querySelectorAll(`g[data-method="${method}"] [data-outcome]`)].reduce((sum, part) => sum + Number(part.dataset.value), 0)
    assert.ok(Math.abs(total - 1) < 1e-9, `${method} outcomes sum to ${total}`)
  }
})

test('l5: component F1 and stage bars equal their table cells', () => {
  const panel = mount('l5')
  const components = within(panel).getByRole('img', { name: 'Component F1 chart' })
  for (const cell of components.querySelectorAll('[data-value][data-row]')) {
    assert.equal(Number(cell.dataset.value).toFixed(3), cellText('Component F1', cell.dataset.row, cell.dataset.method, panel))
  }
  const stages = within(panel).getByRole('img', { name: 'Stage metrics chart' })
  const bars = [...stages.querySelectorAll('[data-stage]')]
  assert.ok(bars.length >= 6)
  for (const bar of bars) {
    assert.equal(Number(bar.dataset.value).toFixed(3), cellText('Stage metrics', bar.dataset.stage, bar.dataset.method, panel), bar.dataset.stage)
  }
})

test('l6: pair bars equal the pairwise table and each pair adds up to every question', () => {
  const panel = mount('l6')
  const chart = within(panel).getByRole('img', { name: 'Pairwise comparison (valid runs only) chart' })
  const rows = within(within(panel).getByRole('table', { name: 'Pairwise comparison table' })).getAllByRole('row').slice(1)
  assert.equal(rows.length, batch.pairwise.length)
  batch.pairwise.forEach((pair, index) => {
    const bars = Object.fromEntries([...chart.querySelectorAll(`[data-pair="${pair.a}-${pair.b}"] [data-part]`)].map(part => [part.dataset.part, Number(part.dataset.value)]))
    assert.deepEqual(bars, Object.fromEntries(Object.entries({ both: pair.both, only_a: pair.only_a, only_b: pair.only_b, neither: pair.neither }).filter(([, count]) => count > 0)), `${pair.a} vs ${pair.b}`)
    const cells = within(rows[index]).getAllByRole('cell').map(cell => cell.textContent)
    assert.deepEqual(cells.slice(0, 2), [pair.a, pair.b])
    assert.equal(pair.both + pair.only_a + pair.only_b + pair.neither, batch.sample_count)
  })
})

test('l6: matrix check marks add up to each tile numerator, and invalid columns are dimmed', () => {
  const panel = mount('l6')
  const grid = within(panel).getByRole('table', { name: 'Question by question' })
  const headers = within(grid).getAllByRole('columnheader').map(header => header.textContent)
  const rows = within(grid).getAllByRole('row').slice(1)
  assert.equal(rows.length, batch.sample_count)
  for (const method of METHODS) {
    const column = headers.indexOf(method)
    const hits = rows.filter(row => within(row).getAllByRole('cell')[column].textContent === '✓').length
    assert.equal(hits, batch.runs[method].quality.ex.numerator, `${method} check marks equal its EX numerator`)
    const dimmed = within(row0(rows)).getAllByRole('cell')[column].className.includes('invalid')
    assert.equal(dimmed, !batch.ranking.includes(method), `${method} dimmed exactly when invalid`)
  }
  assert.equal(within(panel).queryByText(/Fewer than two valid runs/), null, 'four valid runs give six pairs to compare')
})

const row0 = rows => rows[0]
