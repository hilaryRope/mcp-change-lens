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
command, or an `ANTHROPIC_API_KEY` entry under `env`. Without a key you get the facts-only report,
unless your client supports sampling, in which case the client's own model is used and no key is
needed. See [Configuration](#configuration) for all settings.

## Configuration

All settings are environment variables of the server process. A running server keeps the
environment it was started with, so reconnect the client after changing them.

| Variable | Read by | Default | Purpose |
|----------|---------|---------|---------|
| `CHANGE_LENS_ALLOWED_ROOTS` | server | the server's working directory | Directories that repositories must be inside, separated by `:` (`;` on Windows) |
| `ANTHROPIC_API_KEY` | server | unset | Enables the direct Anthropic API fallback when client sampling is unavailable or fails |
| `CHANGE_LENS_MODEL` | server | `claude-sonnet-5-5` | Model for that API fallback |
| `CHANGE_LENS_SAMPLING_MODEL` | example client only | `claude-sonnet-5-5` | Model the example client uses to answer sampling requests |

The key and model settings for the example client are read by the client process, not by the server.
The server is deliberately started by the example client without a key.

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
sampling request by calling Claude with its own `ANTHROPIC_API_KEY`, and prints the report.

Run it with a Python environment that has this package installed. That provides the `anthropic`
and `mcp` libraries, and puts `mcp-change-lens` on the PATH, which the client uses to start the
server. A system `python3` without them fails with `No module named 'anthropic'`, and a
`uv tool install` of the server does not help because it keeps its packages isolated. From a clone:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY=...
python examples/sampling_client.py HEAD~1 HEAD   # run inside the repo you want to analyse
```

`analysis_source: client-sampling` at the end of the output confirms the sampling path ran. The
callback is the part to copy into your own client: pass it as `sampling_callback=` to
`ClientSession`, and the client starts advertising the capability.

If `pip install -e .` fails while building `cryptography` (a Rust compile error such as
`can't find crate for core`), pip found no prebuilt wheel and tried to compile from source. This
happens with an Intel Python running on an Apple Silicon Mac. Upgrade pip and ask for a prebuilt
`cryptography`, or switch to a native arm64 Python:

```sh
pip install -U pip
pip install --only-binary=cryptography -e .
```

#### Telling "unsupported" from "failed"

The facts-only note lists every reason the analysis could not run, one per strategy:

```
Note: no behavioural analysis: the client does not support sampling; ANTHROPIC_API_KEY is not set.
Note: no behavioural analysis: client sampling failed (McpError: Invalid request parameters); ANTHROPIC_API_KEY is not set.
Note: no behavioural analysis: client sampling failed (ValueError: no JSON object in reply); Anthropic API call failed (...).
```

The first line means the client never advertised sampling. A `client sampling failed (...)` entry
means it did, and the request or the reply went wrong, with the error type and message in
brackets. When the client's callback itself raises, the MCP library reports only a generic
`McpError: Invalid request parameters` to the server, so the real cause (a missing key, a network
error) shows up only in the client's own log. Other failures, such as a reply with no JSON, carry
their own message. The server also logs each failure to its stderr and sends it to the client as a
`warning` log message, so clients that show server logs display it as it happens. Reasons are
short and never include repository content.

With the example client, the log line `[client] server requested sampling ... calling <model>`
confirms the request reached the callback. If it is followed by a `WARNING` containing `Could not
resolve authentication method`, the client's own `ANTHROPIC_API_KEY` is not set in the shell that ran it. The server cannot
supply one, because it is deliberately started without a key. Export the key there and run again.
If that line never appears, the request never reached the callback.

Whatever the reason, the tool does not fail. It falls through to the next strategy and finally
returns the facts-only report with `analysis_source: "none"`.

### The fallbacks

- **Anthropic API.** If sampling was unavailable or failed and `ANTHROPIC_API_KEY` is set, the server
  calls the API directly with the model named in `CHANGE_LENS_MODEL` (default `claude-sonnet-5-5`).
  It uses the same system prompt and untrusted-content wrapping, but gets its structured output
  through a forced tool call with a JSON schema, which is more reliable than parsing free text. That
  tool only carries the report back and cannot touch the repository. The key is only ever read from
  the server's environment, never from tool arguments or repository content.
- **Facts only.** If neither works, the tool still returns the extracted facts with
  `analysis_source: "none"` and Low confidence. The four findings lists are empty, and the summary
  ends with a note giving the reason each strategy did not run, so you can tell a client without
  sampling from a sampling call that failed (see above).

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

The C-style pattern reads any `type name(` line as a definition, after excluding statement keywords
such as `return`, `raise`, `assert`, `elif`, `with`, `and` and `or`. Unusual syntax can still produce
a wrong entry in "Changed functions", or miss a real one. Treat that list as a hint, not an index.

A sampling failure on the client is reported to the server only as a generic error, so the note
cannot always name the real cause (see [Telling "unsupported" from "failed"](#telling-unsupported-from-failed)).

## Development

Requires Python 3.10 or newer. Use a virtual environment, so the package, `pytest` and the example
client all resolve against the same interpreter:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest && pytest
```

Layout:

- `src/change_lens/server.py`: the tool, the sampling call and the fallback chain.
- `src/change_lens/analysis.py`: changed files, function detection and test matching.
- `src/change_lens/gitops.py`: safe, read-only Git access.
- `src/change_lens/llm.py`: prompts, schema and the direct API call.
- `tests/`: unit tests, plus end-to-end tests that connect a client to the server in memory, including
  fake sampling callbacks for the success, unsupported, error and unparseable-reply cases.
- `examples/sampling_client.py`: a client with a real sampling callback.

CI runs the tests on Python 3.10 and 3.13 for every push to `main` and every pull request.

## License

MIT. See [LICENSE](LICENSE).
