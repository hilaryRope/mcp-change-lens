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

`CHANGE_LENS_ALLOWED_ROOTS` must contain every repository you want to analyse (several are
separated by `:`, or `;` on Windows). A repo outside it is rejected with `... is outside the allowed roots`. To change it,
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

To explain just the latest commit, compare `HEAD~1` with `HEAD`:

```sh
claude -p "Use the change-lens explain_change tool with base HEAD~1 and head HEAD in this repo and print its summary verbatim." \
  --allowedTools "mcp__change-lens__explain_change"
```

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

The server gathers the facts itself (changed files, changed functions, candidate tests, commit
subjects) and then needs a language model to turn them into behavioural findings. It never runs the
code. It tries three strategies in order and reports which one produced the result in
`analysis_source`.

| Order | Strategy | `analysis_source` | Needs |
|-------|----------|-------------------|-------|
| 1 | Client sampling | `client-sampling` | A client that supports MCP sampling |
| 2 | Anthropic API | `anthropic-api` | `ANTHROPIC_API_KEY` in the server's environment |
| 3 | Facts only | `none` | Nothing |

### What sampling is

Sampling is an MCP feature that lets a server ask the *client* to run a model call on its behalf.
The server sends a `sampling/createMessage` request and the client answers with the model's
completion. The server never holds a key and never chooses the model. The user's own client,
subscription and model do the work, which is why this is the preferred path.

### How change-lens uses it

1. During initialisation the client declares its capabilities. Before classifying, the server checks
   whether `sampling` is among them. If not, it skips straight to strategy 2.
2. The server sends one request containing the extracted context wrapped in an
   `<untrusted_repository_content>` block, plus a system prompt that tells the model to treat that
   block as data, hedge its claims, and answer in a fixed JSON shape. The request caps the answer at
   3000 tokens.
3. It also passes model preferences (intelligence weighted above cost and speed). These are hints
   only. The client decides which model actually runs, and may ask you to approve the request first.
4. The reply is free text, so the server extracts the JSON object from it and checks that every
   required field is present. An unknown confidence value is downgraded to Low. The result has the
   same shape as the API path, so the report looks identical whichever strategy ran.
5. If the client declines, errors out, or returns text that cannot be parsed, the server does not fail.
   It falls through to strategy 2, and then to strategy 3.

The model gets no tools in this exchange. It receives text and returns text, and the server only
treats that text as data to parse.

### Trying it: the example client

Claude Code has no sampling handler, so the repo includes `examples/sampling_client.py`, a minimal
client with a real callback. It starts the server over stdio without any key, answers the server's
sampling request by calling Claude with its own `ANTHROPIC_API_KEY`, and prints the report. From
inside the repository you want to analyse:

```sh
export ANTHROPIC_API_KEY=...
python examples/sampling_client.py HEAD~1 HEAD
```

`analysis_source: client-sampling` at the end of the output confirms the sampling path ran. The
callback is the part to copy into your own client: pass it as `sampling_callback=` to
`ClientSession`, and the client starts advertising the capability.

### The fallbacks

- **Anthropic API.** If sampling was unavailable or failed and `ANTHROPIC_API_KEY` is set, the server
  calls the API directly with the model named in `CHANGE_LENS_MODEL` (default `claude-sonnet-5-5`).
  It uses the same system prompt and untrusted-content wrapping, but gets its structured output
  through a forced tool call with a JSON schema, which is more reliable than parsing free text. That
  tool only carries the report back and cannot touch the repository. The key is only ever read from
  the server's environment, never from tool arguments or repository content.
- **Facts only.** If neither works, the tool still returns the extracted facts with
  `analysis_source: "none"` and Low confidence. The four findings lists are empty, and the summary
  ends with a note saying the client does not support sampling and no key is set (or the call
  failed). One note covers both cases, so check the client and the key if you expected a full report.

### What to expect in practice

Sampling support varies a lot between clients, and many do not implement it. Claude Code does not at
the time of writing, so there you get the facts-only report unless you also set `ANTHROPIC_API_KEY`.
To check which path ran, look at `analysis_source` in the structured output.

Because the behaviour depends on the client, the sampling path is covered by a test that connects to
the server with a fake sampling callback (`test_classification_via_client_sampling`) rather than a real
model. The facts-only path is covered separately by `test_facts_without_llm`.

A facts-only report looks like this. It is the real output for commit `58fe62e` of this repository,
compared against its parent `26f2d4d`, and can be reproduced from a clone with:

```sh
claude -p "Use the change-lens explain_change tool with base 26f2d4d and head 58fe62e in this repo and print its summary verbatim." \
  --allowedTools "mcp__change-lens__explain_change"
```

```
Behavioural impact
------------------
(impact classification unavailable; showing extracted facts only)

Changed files
1. README.md [docs, modified, +7/-0]
2. src/change_lens/analysis.py [code, modified, +31/-1]
3. src/change_lens/llm.py [code, modified, +10/-2]
4. src/change_lens/server.py [code, modified, +44/-14]
5. tests/test_basic.py [test, modified, +25/-0]

Changed functions
1. src/change_lens/analysis.py:is_test_path
2. src/change_lens/analysis.py:file_kind
3. src/change_lens/analysis.py:collect
4. src/change_lens/server.py:ImpactReport
5. src/change_lens/server.py:_bullets
6. src/change_lens/server.py:build_context
7. src/change_lens/server.py:render
8. src/change_lens/server.py:_files
9. src/change_lens/server.py:explain_change
10. src/change_lens/server.py:ToolError
11. tests/test_basic.py:test_calls_and_control_flow_are_not_definitions
12. tests/test_basic.py:test_identical_refs_is_not_an_error
13. tests/test_basic.py:test_docs_and_config_changes_are_reported
14. tests/test_basic.py:test_file_kinds

Candidate existing tests
1. tests/test_basic.py (modified in this change)

Confidence: Low (no behavioural analysis performed)

Note: the client does not support sampling and ANTHROPIC_API_KEY is not set (or the call failed).

These findings are hypotheses grounded in the diff and tests, not guaranteed discoveries.
```

The structured content then has empty `behaviour_changes`, `existing_tests`, `affected_components`
and `missing_tests` lists, with `analysis_source: "none"`, plus `changed_files`,
`changed_files_by_kind` and `changed_functions` populated as above.

Comparing a ref with itself is not an error:

```
No differences: HEAD and HEAD point to the same commit (58fe62e). Nothing can behave differently.
To get a report, compare refs that differ, e.g. base='HEAD~1' for the last commit, or an older
commit, tag or branch. Only committed changes are compared; uncommitted edits are not included.
```

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

Requires Python 3.10 or newer.

```sh
pip install -e . pytest && pytest
```

## License

MIT. See [LICENSE](LICENSE).
