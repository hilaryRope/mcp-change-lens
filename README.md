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

`CHANGE_LENS_ALLOWED_ROOTS` must contain every repository you want to analyse (colon-separated
for several). A repo outside it is rejected with `... is outside the allowed roots`. To change it,
re-add the server and reconnect (`/mcp` in Claude Code, or restart the client); a running server
keeps the environment it was started with.

To get the LLM classification (see below) add `-e ANTHROPIC_API_KEY=...` to the `claude mcp add`
command, or an `ANTHROPIC_API_KEY` entry under `env`.

## Usage

In an interactive Claude Code session, ask it to compare two refs:

> Use explain_change to compare `main` with `HEAD` in this repo.

From the shell, run it non-interactively from inside the repository:

```sh
claude -p "Use the change-lens explain_change tool to compare main with HEAD in this repo and print its summary verbatim." \
  --allowedTools "mcp__change-lens__explain_change"
```

`--allowedTools` pre-approves the tool so the run does not stop for a permission prompt. The tool
takes `base`, optionally `head` (default `HEAD`) and `repo_path` (default `.`, i.e. the directory
the client started the server from). Both refs must exist locally; fetch first if you want to
compare against a remote branch.

## Tool

`explain_change(base, head="HEAD", repo_path=".")` returns structured output (`outputSchema` +
`structuredContent`) and a readable `summary`. Annotated read-only, idempotent, non-destructive,
closed-world. Errors come back as `isError: true`. Progress notifications are sent while it runs.

It covers everything in the diff, not only code: source files (runtime behaviour), documentation
(what readers are now told), and config, dependency, build and CI files (how building, installing or
running may change). Changed files are grouped as `code`, `test`, `docs`, `config`, `ci` or `other`.

Only committed content is compared. If `base` and `head` are identical the tool says so
(`has_changes: false`) instead of failing; use `base="HEAD~1"` to explain the latest commit.

## How the classification is produced

1. **Client sampling**: if the MCP client supports `sampling`, the client's own model classifies the
   change. No key needed on the server.
2. Else **Anthropic API** if `ANTHROPIC_API_KEY` is set (model: `CHANGE_LENS_MODEL`, default `claude-sonnet-5-5`).
3. Else the tool still returns the extracted facts (changed functions, candidate tests) with
   `analysis_source: "none"` and Low confidence.

Many clients, including Claude Code at the time of writing, do not support sampling. With such a
client and no `ANTHROPIC_API_KEY`, you get the facts-only report. The key is optional and is only
ever read from the server's environment.

A facts-only report looks like this:

```
Behavioural impact
------------------
(behavioural classification unavailable; showing extracted facts only)

Changed functions
1. src/change_lens/analysis.py:is_test_path
2. src/change_lens/gitops.py:log_subjects

Candidate existing tests
(none identified)

Confidence: Low (no behavioural analysis performed)

Note: the client does not support sampling and ANTHROPIC_API_KEY is not set (or the call failed).
```

The structured content then has empty `behaviour_changes`, `existing_tests`, `affected_components`
and `missing_tests` lists, with `analysis_source: "none"`.

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
