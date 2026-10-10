/** Pure helpers for the change-lens companion mod. No engine access, so they are easy to test. */

/** The MCP server's name as `/mcp` lists it, and its tool. Register the server under this name. */
export const SERVER = 'change-lens'
export const TOOL = 'explain_change'

const REF = /^[A-Za-z0-9._/~^@{}-]+$/

export type Refs = { base: string; head: string }

/** `/change-lens [base [head]]`: defaults to the latest commit (HEAD~1 against HEAD). */
export function parseRefs(args: string): Refs | { error: string } {
  const parts = args.trim().split(/\s+/).filter(Boolean)
  if (parts.length > 2) return { error: 'usage: /change-lens [base [head]]  (default: HEAD~1 HEAD)' }
  const base = parts[0] ?? 'HEAD~1'
  const head = parts[1] ?? 'HEAD'
  for (const ref of [base, head]) {
    if (ref.startsWith('-') || !REF.test(ref)) return { error: `not a valid Git ref: ${ref}` }
  }
  return { base, head }
}

/** True for a Bash command that makes a real `git commit` (a dry run does not count). */
export function isGitCommit(command: string): boolean {
  return /\bgit\s+commit(\s|$)/.test(command) && !/--dry-run/.test(command)
}

/** The "Confidence: Low|Medium|High" level in a change-lens summary, if there is one. */
export function confidenceOf(summary: string): string | undefined {
  return /^Confidence: (Low|Medium|High)/m.exec(summary)?.[1]
}

type McpResult = {
  content: ReadonlyArray<{ type: string; text?: string }>
  isError: boolean
  structuredContent?: unknown
}

/** The readable report from an `explain_change` result: its `summary`, else its first text block. */
export function summaryOf(result: McpResult): string {
  const structured = result.structuredContent as { summary?: unknown } | undefined
  if (typeof structured?.summary === 'string') return structured.summary
  return result.content.find(block => block.type === 'text')?.text ?? '(the tool returned no text)'
}
