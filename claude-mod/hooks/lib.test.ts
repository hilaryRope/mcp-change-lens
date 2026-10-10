import { describe, expect, test } from 'claude-code/testing'

import {
  PERMISSION,
  analysisRequestOf,
  confidenceOf,
  isGitCommit,
  isPermissionRefusal,
  parseRefs,
  structuredOf,
  summaryOf,
  whyNoAnswer,
} from './lib'

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

describe('report from JSON text', () => {
  test('summaryOf reads the summary out of the JSON text when there is no structured content', () => {
    const json = JSON.stringify({ summary: 'READABLE', confidence: 'Low' })
    expect(summaryOf({ content: [{ type: 'text', text: json }], isError: false })).toBe('READABLE')
  })
  test('summaryOf leaves a plain error message alone', () => {
    expect(summaryOf({ content: [{ type: 'text', text: 'Invalid ref: x' }], isError: true })).toBe('Invalid ref: x')
  })
})

describe('permission refusals', () => {
  test('are told apart from other failures', () => {
    expect(
      isPermissionRefusal(
        '$.mcp.call(change-lens, explain_change) refused: Claude requested permissions to use mcp__change-lens__explain_change, but you have not granted it yet.',
      ),
    ).toBe(true)
    expect(isPermissionRefusal("you haven't granted it")).toBe(true)
    expect(isPermissionRefusal('server "change-lens" is not connected')).toBe(false)
  })
  test('the rule to allow is the tool name Claude Code uses', () => {
    expect(PERMISSION).toBe('mcp__change-lens__explain_change')
  })
})

describe('two-step analysis helpers', () => {
  const request = { system: 'S', prompt: 'P', max_tokens: 3000 }
  test('structuredOf reads structured content, else the JSON text, else nothing', () => {
    expect(structuredOf({ content: [], isError: false, structuredContent: { a: 1 } })).toEqual({ a: 1 })
    expect(structuredOf({ content: [{ type: 'text', text: '{"a":2}' }], isError: false })).toEqual({ a: 2 })
    expect(structuredOf({ content: [{ type: 'text', text: 'plain error' }], isError: true })).toBeUndefined()
    expect(structuredOf({ content: [], isError: false })).toBeUndefined()
  })
  test('analysisRequestOf returns a well-formed request and defaults the cap', () => {
    expect(analysisRequestOf({ analysis_request: request })).toEqual(request)
    expect(analysisRequestOf({ analysis_request: { system: 'S', prompt: 'P' } })?.max_tokens).toBe(4000)
  })
  test('analysisRequestOf returns nothing when there is no usable request', () => {
    expect(analysisRequestOf(undefined)).toBeUndefined()
    expect(analysisRequestOf({ analysis_request: null })).toBeUndefined()
    expect(analysisRequestOf({ analysis_request: { system: 1, prompt: 'P' } })).toBeUndefined()
    expect(analysisRequestOf({ has_changes: true })).toBeUndefined() // an old server: no such field
  })
  test('whyNoAnswer gives one readable line', () => {
    expect(whyNoAnswer({ isAnswered: false, reason: 'api-error', status: 529, error: 'overloaded' })).toBe(
      'api-error (HTTP 529, overloaded)',
    )
    expect(whyNoAnswer({ isAnswered: false, reason: 'aborted' })).toBe('aborted')
  })
})
