# mcp-change-lens

An MCP server with one tool, `explain_change(base, head)`, that reviews a change between two Git
refs (commits, branches or tags). It goes beyond listing what differs. It assesses what the change
may mean in practice: what behaviour may shift, what it may affect, which tests cover it and which
are missing, whether the docs still match, and how confident that assessment is.

```
Behavioural impact
------------------
1. Failed requests may now be retried three times.
2. Request latency may increase during outages.
...
Confidence: Medium
```

Each report contains behavioural impact, existing tests, potentially affected components, missing
test scenarios and a confidence level. Findings are **hypotheses** grounded in the diff and tests,
not guaranteed discoveries.

## Install

```sh
uv tool install git+https://github.com/hilaryRope/mcp-change-lens.git
```

From a clone, `pip install .` also works.

Register with Claude Code:

```sh
claude mcp add change-lens -e CHANGE_LENS_ALLOWED_ROOTS=$HOME/code -- mcp-change-lens
```

Or add it to `.mcp.json` / the Claude Desktop config:

```json
{ "mcpServers": { "change-lens": {
    "command": "mcp-change-lens",
    "env": { "CHANGE_LENS_ALLOWED_ROOTS": "/Users/me/code" } } } }
```

A server keeps the environment it was started with. After changing it, reconnect (`/mcp` in Claude
Code) or restart the client.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHANGE_LENS_ALLOWED_ROOTS` | server's working directory | Repositories must be inside these directories. Separate several with `:` (`;` on Windows). Others are rejected. |
| `ANTHROPIC_API_KEY` | unset | Lets the server call the Anthropic API itself. Optional. |
| `CHANGE_LENS_MODEL` | `claude-sonnet-5-5` | Model for that API call. |

## Usage

Ask Claude Code:

> Use explain_change to compare `main` with `HEAD` in this repo.

Or run it from the shell, inside the repository:

```sh
claude -p "Use the change-lens explain_change tool to compare main with HEAD in this repo and print its summary verbatim." \
  --allowedTools "mcp__change-lens__explain_change"
```

Arguments: `base`, optionally `head` (default `HEAD`) and `repo_path` (default `.`). Both refs must
exist locally, so fetch first to compare against a remote branch. To explain the latest commit, use
`base` = `HEAD~1` and `head` = `HEAD`.

It compares committed content only. Identical refs are a normal result (`has_changes: false`), not
an error. The output is structured (`structuredContent`) plus a readable `summary`, and the tool is
read-only.

## What it covers

`base` and `head` can be any two refs, so you can review one commit (`HEAD~1` to `HEAD`), a whole
branch against `main`, or two releases. Every changed file is included and grouped as `code`, `test`,
`docs`, `config`, `ci` or `other`. The review covers:

- **Code:** how runtime behaviour may change.
- **Docs** (README, guides, changelog, licence): what readers are now told or asked to do, and
  whether the docs still match the code in the diff.
- **Config, dependencies, build and CI:** how building, installing, deploying or running may change.
- **Anything else** (data, assets, schemas): what consumers of the file may see differently.
- **Tests:** existing tests that plausibly cover the change and what they miss, plus concrete test
  scenarios that are missing. For prose changes it suggests a check a human could do instead.
- **User-facing effects:** because the model reads the whole diff, findings can also point out
  changed messages, flags or log lines, instructions that may now be stale, and where repository
  content is sent. For example, a review of this repository's own commits flagged that a log line
  changed and that earlier README troubleshooting advice referred to the old text.

Each finding names the file it comes from, and the model is told to hedge and to say when the
evidence is thin. Large diffs are size-capped, and the report says when it was truncated.

## Getting a full report

The server collects the facts itself. A language model then turns them into findings. The server
has two ways to reach one, tried in this order:

| Order | How | Needs | `analysis_source` |
|-------|-----|-------|-------------------|
| 1 | **Sampling**: the server asks the client to run the model | A client that supports sampling. No key on the server. | `client-sampling` |
| 2 | **Direct API call** | `ANTHROPIC_API_KEY` on the server | `anthropic-api` |
| 3 | Facts only | Nothing | `none` |

If a step is unavailable or fails, the server moves to the next one. It never fails the tool.

**Claude Code does not support sampling today.** There, a full report needs `ANTHROPIC_API_KEY` on
the server (`claude mcp add ... -e ANTHROPIC_API_KEY=...`). Without it you get facts only.

To get a full report with no server key, use a client that supports sampling. The repo includes one:

### Example sampling client

`examples/sampling_client.py` starts the server with no key and answers its sampling requests
itself. It has two backends:

| Backend | Command | Needs |
|---------|---------|-------|
| `api` (default) | `python examples/sampling_client.py HEAD~1 HEAD` | `ANTHROPIC_API_KEY` in your shell |
| `claude-cli` | `python examples/sampling_client.py HEAD~1 HEAD --backend claude-cli` | The `claude` command, logged in. No key. |

`claude-cli` runs your installed `claude` as a one-shot call with no tools, no MCP servers and no
project files. The repository text goes in on stdin. It runs as whichever account `claude` is
logged in to, so it is opt-in and never chosen automatically.

Setup, from a clone:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY=...        # api backend only
python examples/sampling_client.py HEAD~1 HEAD [--backend claude-cli]
```

The output ends with `analysis_source: client-sampling` when it worked. Set
`CHANGE_LENS_SAMPLING_MODEL` to choose the model. Use the venv's Python: the system `python3` has
neither `anthropic` nor `mcp-change-lens`.

If `pip install -e .` fails building `cryptography` (a Rust error such as `can't find crate for
core`), you are probably on an Intel Python on an Apple Silicon Mac. Run
`pip install -U pip && pip install --only-binary=cryptography -e .`, or use an arm64 Python.

To add sampling to your own client, pass a callback to `ClientSession(..., sampling_callback=cb)`.
That is what makes the client advertise the capability.

### When it does not work

The facts-only report ends with a note listing why each step did not run:

```
Note: no behavioural analysis: the client does not support sampling; ANTHROPIC_API_KEY is not set.
Note: no behavioural analysis: client sampling failed (McpError: Invalid request parameters); ANTHROPIC_API_KEY is not set.
Note: no behavioural analysis: client sampling failed (...); Anthropic API call failed (...).
```

- **"does not support sampling"**: the client never offered it.
- **"client sampling failed"**: it offered it, but the call or the reply went wrong. If the client's
  callback raised, the server only sees a generic `McpError`. The real cause is in the client's own
  log, for example `Could not resolve authentication method` means the client has no API key.
- **"Anthropic API call failed"**: the server's own call failed. The error is in the note.

The server also logs each failure to stderr and to the client as a warning. Logs never contain
repository content.

## Example output

A full report. The layout is real tool output. The findings are hand-written to show the format,
since real wording comes from the model:

```
Behavioural impact
------------------
1. Documentation, configuration and CI file changes are now reported alongside code, so a docs-only or CI-only diff no longer comes back empty.
2. Comparing two identical refs may now return a normal result saying nothing differs, instead of an error.
3. Test files are now matched by path and by name, which may change which existing tests are listed as covering a change.

Existing tests
1. tests/test_basic.py (modified in this change) now includes tests for identical refs, docs and config changes, and file kinds.

Potentially affected components
1. src/change_lens/analysis.py: file_kind, collect, is_test_path
2. src/change_lens/server.py: build_context, render, explain_change

Missing test scenarios
1. A diff that mixes code, docs and CI changes in one commit.
2. Renamed or deleted files in the changed-file groups.

Changed files
1. README.md [docs, modified, +7/-0]
2. src/change_lens/analysis.py [code, modified, +31/-1]
3. src/change_lens/llm.py [code, modified, +10/-2]
4. src/change_lens/server.py [code, modified, +44/-14]
5. tests/test_basic.py [test, modified, +25/-0]

Confidence: Medium (the diff is fully visible, but behaviour is inferred from source and tests, not by running them)

These findings are hypotheses grounded in the diff and tests, not guaranteed discoveries.
```

A facts-only report, real output for commit `58fe62e` against its parent `26f2d4d`:

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
10. tests/test_basic.py:test_calls_and_control_flow_are_not_definitions
11. tests/test_basic.py:test_identical_refs_is_not_an_error
12. tests/test_basic.py:test_docs_and_config_changes_are_reported
13. tests/test_basic.py:test_file_kinds

Candidate existing tests
1. tests/test_basic.py (modified in this change)

Confidence: Low (no behavioural analysis performed)

Note: no behavioural analysis: the client does not support sampling; ANTHROPIC_API_KEY is not set.

These findings are hypotheses grounded in the diff and tests, not guaranteed discoveries.
```

Identical refs:

```
No differences: HEAD and HEAD point to the same commit (58fe62e). Nothing can behave differently.
To get a report, compare refs that differ, e.g. base='HEAD~1' for the last commit, or an older
commit, tag or branch. Only committed changes are compared; uncommitted edits are not included.
```

## Security

Repository content is untrusted.

- `repo_path` must be inside `CHANGE_LENS_ALLOWED_ROOTS`.
- Refs are validated, rejected if they start with `-`, and resolved with `rev-parse --end-of-options`.
- Git runs without a shell, with hooks, fsmonitor, external diff and textconv disabled and `GIT_*` env stripped.
- Files are read from git objects at the given commits, not the working tree, with size caps.
- Nothing from the repo or commit messages is executed. The server runs no tests or builds.
- The model sees repository text wrapped as untrusted data and has no tools.
- Diffs are size-capped, and truncation is reported.
- The API key is read only from the server's environment.

## Limits

Function detection is regex based (Rust, Python, JS/TS, Go, Java/Kotlin-like, Ruby), not a parser,
and test matching is by name. Treat "Changed functions" as a hint: unusual syntax can add a wrong
entry or miss a real one. The model is told to hedge.

## Development

Needs Python 3.10 or newer. Use a virtual environment:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest && pytest
```

- `src/change_lens/server.py`: the tool, sampling and the fallbacks.
- `src/change_lens/analysis.py`: changed files, function detection, test matching.
- `src/change_lens/gitops.py`: read-only Git access.
- `src/change_lens/llm.py`: prompts, schema and the API call.
- `examples/sampling_client.py`: the example client.

CI runs the tests on Python 3.10 and 3.13 for pushes to `main` and for pull requests.

## License

MIT. See [LICENSE](LICENSE).
