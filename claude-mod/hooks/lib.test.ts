import { describe, expect, test } from 'claude-code/testing'

import { confidenceOf, isGitCommit, parseRefs, summaryOf } from './lib'

describe('parseRefs', () => {
  test('defaults to the latest commit', () => {
    expect(parseRefs('')).toEqual({ base: 'HEAD~1', head: 'HEAD' })
  })
  test('takes base and head', () => {
    expect(parseRefs('main feature/x')).toEqual({ base: 'main', head: 'feature/x' })
    expect(parseRefs('  v1.2.0  ')).toEqual({ base: 'v1.2.0', head: 'HEAD' })
  })
  test('rejects option-like refs, odd characters and extra arguments', () => {
    expect(parseRefs('--output=/tmp/x')).toEqual({ error: 'not a valid Git ref: --output=/tmp/x' })
    expect(parseRefs('main; rm -rf /')).toHaveProperty('error')
    expect(parseRefs('a b c')).toHaveProperty('error')
  })
})

describe('isGitCommit', () => {
  test('matches real commits', () => {
    expect(isGitCommit('git commit -m "x"')).toBe(true)
    expect(isGitCommit('git add . && git commit -qm x')).toBe(true)
  })
  test('ignores everything else', () => {
    expect(isGitCommit('git commit --dry-run')).toBe(false)
    expect(isGitCommit('git status')).toBe(false)
    expect(isGitCommit('git commit-tree abc')).toBe(false)
    expect(isGitCommit('echo commit')).toBe(false)
  })
})

describe('report helpers', () => {
  test('confidenceOf reads the level', () => {
    expect(confidenceOf('x\n\nConfidence: Medium (reason)\n')).toBe('Medium')
    expect(confidenceOf('Confidence: Low (no behavioural analysis performed)')).toBe('Low')
    expect(confidenceOf('no level here')).toBeUndefined()
  })
  test('summaryOf prefers the structured summary', () => {
    const block = [{ type: 'text', text: '{"json":true}' }]
    expect(summaryOf({ content: block, isError: false, structuredContent: { summary: 'S' } })).toBe('S')
    expect(summaryOf({ content: block, isError: false })).toBe('{"json":true}')
    expect(summaryOf({ content: [], isError: true })).toBe('(the tool returned no text)')
  })
})
