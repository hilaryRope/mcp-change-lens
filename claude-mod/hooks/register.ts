import type { Register } from 'claude-code'

import {
  PERMISSION,
  SERVER,
  TOOL,
  analysisRequestOf,
  confidenceOf,
  isGitCommit,
  isPermissionRefusal,
  parseRefs,
  structuredOf,
  summaryOf,
  whyNoAnswer,
} from './lib'

/**
 * change-lens companion. It does exactly two things:
 *
 *  1. `/change-lens [base [head]]` reviews a change and prints the report to you only:
 *       a. asks the change-lens MCP server for the facts and the analysis prompt
 *          (`defer_analysis`), running no model on the server;
 *       b. runs that prompt once through THIS Claude Code session's own model with
 *          `$.model.complete`: no tools, no history, no API key anywhere;
 *       c. sends the model's reply back (`report`) so the server checks and renders it.
 *     If any step fails you still get the facts-only report, with the reason. The status
 *     line shows the confidence level afterwards.
 *  2. After a successful `git commit` run through the Bash tool, it shows a toast that
 *     reminds you the command exists. It never runs the review by itself.
 *
 * It needs the change-lens MCP server registered under the name `change-lens`, at a version that
 * understands `defer_analysis` and `report`. Older servers just give the facts-only report.
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
      const refsArgs = { base: refs.base, head: refs.head }
      const first = await $.mcp.call(SERVER, TOOL, { ...refsArgs, defer_analysis: true })
      const factsOnly = summaryOf(first)
      if (first.isError) return { text: `change-lens: ${factsOnly}` }

      const request = analysisRequestOf(structuredOf(first))
      if (!request) {
        // Nothing to analyse (identical refs), or a server too old to defer the analysis.
        const isOldServer = structuredOf(first)?.has_changes === true
        const hint = isOldServer
          ? '\n\n(This change-lens server cannot hand the analysis to Claude Code. Update it: see the README.)'
          : ''

        return { text: factsOnly + hint }
      }

      $.ui.status('change-lens: analysing with this session\'s model...')
      const model = await $.session.model()
      const answer = await $.model.complete({
        model,
        system: request.system,
        prompt: request.prompt,
        maxTokens: request.max_tokens,
        timeoutMs: 180_000,
      })
      if (!answer.isAnswered) {
        $.ui.status(undefined)

        return { text: `${factsOnly}\n\nchange-lens: the session's model gave no analysis: ${whyNoAnswer(answer)}.` }
      }

      const second = await $.mcp.call(SERVER, TOOL, { ...refsArgs, report: answer.text })
      if (second.isError) {
        $.ui.status(undefined)

        return { text: `${factsOnly}\n\nchange-lens: the model's reply could not be used: ${summaryOf(second)}` }
      }

      const summary = summaryOf(second)
      const confidence = confidenceOf(summary)
      $.ui.status(confidence ? `change-lens: ${confidence} confidence` : undefined)

      return { text: `${summary}\n\n(analysis by this session's model, ${model})` }
    } catch (error) {
      $.ui.status(undefined)
      const why = error instanceof Error ? error.message : String(error)
      if (isPermissionRefusal(why)) {
        return {
          text:
            `change-lens: Claude Code has not been allowed to call ${PERMISSION}. ` +
            `Allow it with /permissions (add the rule ${PERMISSION}), or start Claude Code with ` +
            `--allowedTools "${PERMISSION}". Details: ${why}`,
        }
      }

      return {
        text:
          `change-lens: could not call the "${SERVER}" MCP server (${why}). ` +
          `If it is not registered yet: claude mcp add ${SERVER} -e CHANGE_LENS_ALLOWED_ROOTS=$HOME/code -- mcp-change-lens`,
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
