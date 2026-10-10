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

/** The readable report from an `explain_change` result.
 *
 * The server returns the report as structured content and also as JSON text. `$.mcp.call` may hand
 * back only the text, so try, in order: the structured `summary`, the `summary` inside the JSON
 * text, then the first text block as it is.
 */
export function summaryOf(result: McpResult): string {
  const structured = result.structuredContent as { summary?: unknown } | undefined
  if (typeof structured?.summary === 'string') return structured.summary

  const text = result.content.find(block => block.type === 'text')?.text
  if (text === undefined) return '(the tool returned no text)'
  try {
    const parsed = JSON.parse(text) as { summary?: unknown }
    if (typeof parsed?.summary === 'string') return parsed.summary
  } catch {
    // not JSON: an error message or plain text, show it as it is
  }

  return text
}

/** The permission rule that lets the mod call the server's tool. */
export const PERMISSION = `mcp__${SERVER}__${TOOL}`

/** True when Claude Code refused the call for lack of permission, not because the server is down. */
export function isPermissionRefusal(message: string): boolean {
  return /permission|haven't granted|not granted/i.test(message)
}

export type AnalysisRequest = { system: string; prompt: string; max_tokens: number }

/** The structured report from an `explain_change` result: its structured content, else its JSON text. */
export function structuredOf(result: McpResult): Record<string, unknown> | undefined {
  if (result.structuredContent && typeof result.structuredContent === 'object') {
    return result.structuredContent as Record<string, unknown>
  }
  const text = result.content.find(block => block.type === 'text')?.text
  if (text === undefined) return undefined
  try {
    const parsed: unknown = JSON.parse(text)

    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : undefined
  } catch {
    return undefined
  }
}

/** The request the server wants run with the caller's own model, if it sent a well-formed one. */
export function analysisRequestOf(structured: Record<string, unknown> | undefined): AnalysisRequest | undefined {
  const request = structured?.analysis_request as Partial<AnalysisRequest> | null | undefined
  if (!request || typeof request.system !== 'string' || typeof request.prompt !== 'string') return undefined

  return {
    system: request.system,
    prompt: request.prompt,
    max_tokens: typeof request.max_tokens === 'number' && request.max_tokens > 0 ? request.max_tokens : 4000,
  }
}

/** Why a model call produced no text, in one line. */
export function whyNoAnswer(result: { isAnswered: boolean; reason?: string; status?: number | null; error?: string }): string {
  if (result.isAnswered) return 'answered'
  const detail = [result.status ? `HTTP ${result.status}` : undefined, result.error].filter(Boolean).join(', ')

  return `${result.reason ?? 'no reply'}${detail ? ` (${detail})` : ''}`
}
