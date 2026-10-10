import type { Register } from 'claude-code'

import { SERVER, TOOL, confidenceOf, isGitCommit, parseRefs, summaryOf } from './lib'

/**
 * change-lens companion. It does exactly two things:
 *
 *  1. `/change-lens [base [head]]` calls the change-lens MCP server's explain_change tool
 *     directly and prints the report. Nothing is sent to the model, so the report is shown
 *     to you only. The status line shows the confidence level afterwards.
 *  2. After a successful `git commit` run through the Bash tool, it shows a toast that
 *     reminds you the command exists. It never runs the review by itself.
 *
 * It needs the change-lens MCP server registered under the name `change-lens`.
 */
export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'change-lens',
      description: 'Review the change between two Git refs with change-lens',
      argumentHint: '[base [head]]  (default: HEAD~1 HEAD)',
    })

    return next(e)
  })

  on('command.run', { command: 'change-lens' }, async ($, e) => {
    const refs = parseRefs(e.args)
    if ('error' in refs) return { text: `change-lens: ${refs.error}` }

    try {
      const result = await $.mcp.call(SERVER, TOOL, { base: refs.base, head: refs.head })
      const summary = summaryOf(result)
      if (result.isError) return { text: `change-lens: ${summary}` }

      const confidence = confidenceOf(summary)
      $.ui.status(confidence ? `change-lens: ${confidence} confidence` : undefined)

      return { text: summary }
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error)

      return {
        text:
          `change-lens: could not reach the "${SERVER}" MCP server (${why}). ` +
          `Register it with: claude mcp add ${SERVER} -e CHANGE_LENS_ALLOWED_ROOTS=$HOME/code -- mcp-change-lens`,
      }
    }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined && ran.isError !== true && isGitCommit(e.command)) {
      $.ui.toast('Committed. Run /change-lens to review this commit.')
    }

    return ran
  }).catch(($, e, next) => next(e)) // if this hint fails, the Bash call itself goes ahead untouched
}
