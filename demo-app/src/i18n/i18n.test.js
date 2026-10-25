import assert from 'node:assert/strict'
import test from 'node:test'

import { detectLocale, translate } from './index.js'
import enUS from './en-US.js'
import zhCN from './zh-CN.js'

test('defaults to Chinese when nothing is stored', () => {
  assert.equal(detectLocale(''), 'zh-CN')
  assert.equal(detectLocale(undefined), 'zh-CN')
})

test('a stored locale beats the Chinese default', () => {
  assert.equal(detectLocale('en-US'), 'en-US')
  assert.equal(detectLocale('zh-CN'), 'zh-CN')
  assert.equal(detectLocale('fr-FR'), 'zh-CN', 'unsupported stored values fall back to Chinese')
})

test('translates parameters and falls back to English', () => {
  assert.equal(translate('zh-CN', 'header.configCount', { count: 64 }), '64 个规范配置')
  assert.equal(translate('zh-CN', 'missing.key'), 'missing.key')
  assert.equal(translate('invalid', 'process.configure'), 'Studio')
})

test('keeps staging and accessibility keys shared across both dictionaries', () => {
  for (const key of [
    'process.query',
    'query.title',
    'query.lockedTitle',
    'boot.loading',
    'boot.error',
  ]) {
    assert.ok(Object.hasOwn(enUS, key), `missing English key ${key}`)
    assert.ok(Object.hasOwn(zhCN, key), `missing Chinese key ${key}`)
  }
  assert.deepEqual(Object.keys(enUS).sort(), Object.keys(zhCN).sort())
})
