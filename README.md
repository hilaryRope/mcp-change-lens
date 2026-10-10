# mcp-change-lens

An MCP server with one tool, `explain_change(base, head)`, that explains what changed between two
Git commits **in terms of behaviour**, not files and lines.

```
Behavioural impact
------------------
1. Failed requests may now be retried three times.
2. Request latency may increase during outages.
...
Confidence: Medium
```

Returns: behaviour that may have changed, existing tests covering it, potentially affected
components, missing test scenarios, and a confidence level. Findings are **hypotheses** grounded in
the diff and tests, not guaranteed discoveries.

## Install

```sh
uv tool install git+ssh://git@github.com/hilaryRope/mcp-change-lens.git
```

The repository is private, so this needs GitHub access to it. From a local clone, `pip install .` works too.

Register with Claude Code:

```sh
claude mcp add change-lens -e CHANGE_LENS_ALLOWED_ROOTS=$HOME/code -- mcp-change-lens
```

or in `.mcp.json` / Claude Desktop config:

```json
{ "mcpServers": { "change-lens": {
    "command": "mcp-change-lens",
    "env": { "CHANGE_LENS_ALLOWED_ROOTS": "/Users/me/code" } } } }
```

## Tool

`explain_change(base, head="HEAD", repo_path=".")` returns structured output (`outputSchema` +
`structuredContent`) and a readable `summary`. Annotated read-only, idempotent, non-destructive,
closed-world. Errors come back as `isError: true`. Progress notifications are sent while it runs.

## How the classification is produced

1. **Client sampling**: if the MCP client supports `sampling`, the client's own model classifies the
   change. No key needed on the server.
2. Else **Anthropic API** if `ANTHROPIC_API_KEY` is set (model: `CHANGE_LENS_MODEL`, default `claude-sonnet-5-5`).
3. Else the tool still returns the extracted facts (changed functions, candidate tests) with
   `analysis_source: "none"` and Low confidence.

Many clients do not support sampling yet. With such a client and no `ANTHROPIC_API_KEY`, you get the
facts-only report. The key is optional and is only ever read from the server's environment.

## Security model

Repository content is untrusted.

- `repo_path` must resolve inside `CHANGE_LENS_ALLOWED_ROOTS` (default: server's cwd); so must the repo root.
- Refs are regex-validated, rejected if they start with `-`, and resolved with `rev-parse --end-of-options`.
- Git runs via argv (no shell) with hooks, fsmonitor, external diff and textconv disabled, and `GIT_*` env stripped.
- Files are read from git objects at the given commits, not the working tree (no symlink tricks), with size caps.
- Nothing from the repo or commit messages is ever executed. The server runs no test or build commands.
- The LLM sees repo text wrapped as untrusted data and has no tools beyond a structured-output schema.
- Diffs are size-capped; truncation is reported.

## Limits

Function detection is regex/indentation based (Rust, Python, JS/TS, Go, Java/Kotlin-like, Ruby), not a
parser. Test matching is by name reference. Both are heuristics that feed the LLM, which is told to hedge.

## Development

```sh
pip install -e . pytest && pytest
```

## License

MIT. See [LICENSE](LICENSE).
