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

## Quick start in Claude Code, with no API key

This gets you a full report in Claude Code without giving the server a key. The Claude Code mod
runs the analysis once with your session's own model.

1. **Install the current server.** The mod needs a server that supports `defer_analysis` and
   `report`. An older installed copy only gives the facts-only report. From a clone of this repo:

   ```sh
   uv tool install --force .
   ```

   Or straight from GitHub: `uv tool install --force git+https://github.com/hilaryRope/mcp-change-lens.git`.
2. **Register the server once**, naming every directory that holds repositories you will review:

   ```sh
   claude mcp add change-lens -e CHANGE_LENS_ALLOWED_ROOTS=$HOME/code -- mcp-change-lens
   ```

   Already registered? Reconnect it with `/mcp` so it picks up the new version.
3. **Start Claude Code with the mod**, from inside the repository you want to review. Use the full
   path to this repo's `claude-mod` folder. It loads for that session only:

   ```sh
   claude --plugin-dir /path/to/mcp-change-lens/claude-mod
   ```

4. **Allow the tool once.** Without this Claude Code refuses the mod's call. Run `/permissions` and
   add the allow rule `mcp__change-lens__explain_change`, or start with
   `--allowedTools "mcp__change-lens__explain_change"`.
5. **Run it.**

   ```
   /change-lens
   ```

   With no arguments it reviews the latest commit. `/change-lens main feature/x` reviews a range.

It worked if the report ends with `(analysis by this session's model, <model>)`. If it shows the
facts-only report instead, the message under it says why. The most common causes are an old server
(step 1), a missing permission (step 4) and a repository outside the allowed roots (step 2).

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

Two optional arguments let a caller that has its own model run the analysis itself. Call with
`defer_analysis=true` to get the facts and an `analysis_request` (system prompt, prompt and a
suggested token cap) with no model run on the server. Run it with your model, then call again with
the same refs and the model's JSON reply as `report`. The server validates the reply and renders
the report, with `analysis_source: "host-model"`. The reply can be the JSON object or text that
contains it. Do not pass both arguments in one call.

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

## How the pieces fit together

There are three separate things in this repository. Only the first is the product.

1. **The server** (`src/change_lens`). This is what you install and register. It does all the
   analysis. You never run it by hand. Your AI assistant starts it and calls its one tool.
2. **The example client** (`examples/sampling_client.py`). Optional. A small stand-in for an AI
   assistant, used only to get a full report without an API key. It does no analysis.
3. **The Claude Code mod** (`claude-mod/`). Optional. Adds a `/change-lens` command and a
   post-commit hint to Claude Code. It calls the server and does no analysis either.

```
 You, through an AI assistant (the "client": Claude Code, Claude Desktop, ...)
   |
   |  calls explain_change(base, head)
   v
 change-lens server                          <- src/change_lens, what you install
   1. reads the Git diff, read-only          (gitops.py)
   2. finds changed files, functions, tests  (analysis.py)
   3. needs a model to write the findings:   (server.py, llm.py)
        a. sampling: ask the CLIENT's model  needs a client that supports sampling
        b. else call the Anthropic API       needs ANTHROPIC_API_KEY on the server
        c. else return the facts only        needs nothing
   4. builds the report and sends it back    (server.py)
```

Steps 1, 2 and 4 are the same every time. Only step 3 changes, depending on what is available.
There is also a two-step variant for callers that have their own model but cannot offer sampling:
the caller asks for the prompt (`defer_analysis`), runs it with its own model, and sends the reply
back (`report`). The server checks and renders it. The Claude Code mod works this way.

| | Server (`src/`) | Example client (`examples/`) |
|---|---|---|
| Installed and registered by you | yes | no |
| Reads Git, finds functions and tests | yes | no |
| Builds the prompt and parses the reply | yes | no |
| Decides sampling, API key or facts only | yes | no |
| Writes the findings | no, it asks a model | no, it passes the request to a model |
| Needed in normal use | yes | no |

**Normal use.** You register the server with your assistant (see Install). The assistant is the
client. The example client is not involved at all.

**Why the example client exists.** Claude Code cannot answer sampling requests yet, so there the
server falls back to an API key or to facts only. The example client can answer them, using your
API key or your logged-in `claude` command. It starts the server, passes the model call through,
and prints the report. Everything in the report still comes from the server. If Claude Code gains
sampling support, you will not need the example client.

### MCP server vs Claude Code mod

The server and the mod are different kinds of thing. The server is an MCP server that offers one
tool, `explain_change`. The mod only adds Claude Code features on top of it.

| | MCP server and tool | Claude Code mod |
|---|---|---|
| What it is | A program that offers tools to an AI client over the MCP protocol | Code that changes how Claude Code itself looks or behaves |
| Where it runs | Its own process, started by the client | Inside Claude Code's process |
| Language | Any. This one is Python | TypeScript or JavaScript only |
| Who can use it | Any MCP client: Claude Code, Claude Desktop, the example client | Claude Code only |
| What triggers it | The model decides to call the tool, or a client calls it | Events in Claude Code, such as a typed command or a finished tool call |
| What it adds | A new capability: reviewing a Git change | Interface and behaviour: commands, hooks, toasts, status line |
| Does the real work | Yes. It reads Git and builds the report | No. It only reacts to events and calls the server |
| In this repo | `src/change_lens` | `claude-mod/` |

There are three ways to reach the same tool:

- **Ask Claude.** The model decides to call `explain_change`.
- **Run `/change-lens`.** The mod gets the facts and the prompt from the tool, runs the analysis
  once with your Claude Code session's own model, and sends the reply back to the tool to be
  checked and rendered. No API key is needed.
- **Run the example client.** It calls the tool directly and also answers the server's model requests.

Without the server the mod does nothing useful. Without the mod the server works fully.

## Claude Code mod (optional)

`claude-mod/` is a small Claude Code mod that gets you a full report inside Claude Code with no API
key. It needs the server registered under the name `change-lens` (see the quick start). It does
exactly two things, and nothing else:

| What | When | Effect |
|------|------|--------|
| `/change-lens [base [head]]` | You type it. Defaults to `HEAD~1 HEAD`, the latest commit. | Reviews the change and prints the report. See the three steps below. The status line then shows the confidence level. |
| A reminder toast | After a successful `git commit` run through Claude Code's Bash tool. | Shows "Committed. Run /change-lens to review this commit." Nothing is run for you. |

What `/change-lens` does, in order:

1. Calls the server's `explain_change` with `defer_analysis`. The server reads Git and returns the
   facts plus the prompt. It runs no model.
2. Runs that prompt once with your Claude Code session's own model (the one `/model` shows), using
   the mod's model call: no tools, no history, no API key. The diff goes to that model.
3. Calls `explain_change` again with the model's reply as `report`. The server checks the reply and
   renders the report, which is printed to you.

If step 2 or 3 fails, you get the facts-only report from step 1 and the reason. If the server is too
old to defer the analysis, you get its facts-only report and a hint to update it.

What it does **not** do:
- It does not read Git or analyse code itself. The server does that, and the mod only runs the
  prompt through your model and shows the result.
- It does not run a review automatically. No repository content is sent to a model unless you type
  `/change-lens`.
- It does not hide the report from your session. The printed report is part of the conversation
  like any command output, so Claude can read it on later turns. Tested: asked afterwards, the
  session's model quoted the report's first finding. The model call that wrote the report is a
  separate request with no history. It is not part of the conversation.
- It does not change settings, register the server or touch your global configuration.

Cost and privacy: each `/change-lens` is one extra model call on your session, with your session's
model and account. It sends the diff, wrapped as untrusted data, to that model and nowhere else.
The findings are text written from repository content, so once the report is in your conversation
treat it as untrusted data, as you would the output of asking Claude to call the tool.

Load it for a session from a clone:

```sh
claude --plugin-dir ./claude-mod
```

Then run `/change-lens`, `/change-lens main`, or `/change-lens main feature/x`. Refs are checked
before the call, and the server validates them again. To run the mod's tests, use
`claude plugin test claude-mod`.

## Getting a full report

The server collects the facts itself. A language model then turns them into findings. The server
has these ways to reach one. The first three are tried in order; the fourth is used when the caller
asks for it:

| Order | How | Needs | `analysis_source` |
|-------|-----|-------|-------------------|
| 1 | **Sampling**: the server asks the client to run the model | A client that supports sampling. No key on the server. | `client-sampling` |
| 2 | **Direct API call** | `ANTHROPIC_API_KEY` on the server | `anthropic-api` |
| 3 | Facts only | Nothing | `none` |
| Caller asks | **Two-step**: the caller runs the prompt with its own model and sends the reply back | A caller that uses `defer_analysis` and `report`, such as the Claude Code mod. No key. | `host-model` |

If a step is unavailable or fails, the server moves to the next one. It never fails the tool.

**Claude Code does not support sampling today.** When Claude calls the tool itself, a full report
needs `ANTHROPIC_API_KEY` on the server (`claude mcp add ... -e ANTHROPIC_API_KEY=...`). Without it
you get facts only. The `/change-lens` command from the mod avoids this and needs no key (see the
quick start).

Outside Claude Code, to get a full report with no server key, use a client that supports sampling.
The repo includes a simple one for this purpose:

### Example sampling client

**Optional, and not the product.** `examples/sampling_client.py` is a stand-in client. It starts the
server with no key and answers its sampling requests by passing them to a model. The server still
does all the analysis (see [How the pieces fit together](#how-the-pieces-fit-together)). It has two
backends:

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
- In Claude Code, a report printed by `/change-lens` joins your conversation, where the session's
  model can read it. Its text comes from repository content, so it is untrusted data.
- A `report` from a caller is only data: it must be a JSON object with the required fields and at
  most 100,000 characters, it is never executed, and a bad one is an error.

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
- `claude-mod/`: the Claude Code mod. `hooks/register.ts` holds the hooks, `hooks/lib.ts` the pure helpers.

CI runs the tests on Python 3.10 and 3.13 for pushes to `main` and for pull requests.

## License

MIT. See [LICENSE](LICENSE).
