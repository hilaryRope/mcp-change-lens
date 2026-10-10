"""Minimal MCP client that supports sampling, so change-lens can use it end to end.

Usage (from inside the repository you want to analyse):

    python examples/sampling_client.py HEAD~1 HEAD                       # needs ANTHROPIC_API_KEY
    python examples/sampling_client.py HEAD~1 HEAD --backend claude-cli  # no key, uses `claude`

The client starts the server over stdio and registers a sampling callback. When the server asks
for a classification, the callback forwards the request to a model and returns the answer. The
server is started without ANTHROPIC_API_KEY on purpose, so a report with
`analysis_source: "client-sampling"` proves the sampling path ran.

Backends (how THIS client reaches a model; the server never sees a key either way):

  api         Anthropic API with ANTHROPIC_API_KEY from this process (the default).
  claude-cli  Your installed `claude` command in one-shot mode, with no tools. It runs as whichever
              account `claude` is logged in to, so it is opt-in and never chosen automatically.

Set CHANGE_LENS_SAMPLING_MODEL to pick the model. api defaults to claude-sonnet-5-5; claude-cli
defaults to whatever `claude` is configured to use.
"""
import argparse
import asyncio
import os
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.context import RequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent

BACKEND = "api"
MODEL = os.environ.get("CHANGE_LENS_SAMPLING_MODEL")
CLI_TIMEOUT_SECONDS = 300


async def ask_api(system: str, messages: list[dict], max_tokens: int) -> tuple[str, str]:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("the 'anthropic' package is missing: activate the virtual environment "
                           "where this project is installed") from None
    resp = await anthropic.AsyncAnthropic().messages.create(
        model=MODEL or "claude-sonnet-5-5",  # the server's modelPreferences are hints; the client decides
        max_tokens=max_tokens, system=system, messages=messages)
    return "".join(b.text for b in resp.content if b.type == "text"), resp.model


async def ask_claude_cli(system: str, messages: list[dict], max_tokens: int) -> tuple[str, str]:
    """One-shot `claude -p` call with no tools, no MCP servers and no project files.

    The repository text goes in on stdin, not in the command line. max_tokens cannot be set here.
    """
    prompt = (messages[0]["content"] if len(messages) == 1 else
              "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages))
    cmd = ["claude", "-p", "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "text", "--system-prompt", system]
    if MODEL:
        cmd += ["--model", MODEL]
    with tempfile.TemporaryDirectory() as empty_dir:  # no CLAUDE.md, .mcp.json or settings found here
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=empty_dir, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), CLI_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"`claude -p` timed out after {CLI_TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"`claude -p` exited with {proc.returncode}: "
                           f"{err.decode(errors='replace').strip()[-300:]}")
    return out.decode(), MODEL or "claude-cli"


async def sampling_callback(
    context: RequestContext, params: CreateMessageRequestParams
) -> CreateMessageResult:
    """Called by the MCP library whenever the server sends sampling/createMessage."""
    messages = [
        {"role": m.role, "content": m.content.text}
        for m in params.messages
        if isinstance(m.content, TextContent)
    ]
    print(f"[client] server requested sampling ({len(messages)} message(s), "
          f"max_tokens={params.maxTokens}); backend={BACKEND}", file=sys.stderr)
    ask = ask_claude_cli if BACKEND == "claude-cli" else ask_api
    text, model = await ask(params.systemPrompt or "", messages, params.maxTokens)
    return CreateMessageResult(
        role="assistant", model=model, content=TextContent(type="text", text=text),
        stopReason="endTurn")


async def main(base: str, head: str, repo: str) -> None:
    server = StdioServerParameters(
        command="mcp-change-lens",
        env={"CHANGE_LENS_ALLOWED_ROOTS": repo},  # no ANTHROPIC_API_KEY for the server
    )
    async with stdio_client(server) as (read, write):
        # Passing sampling_callback is what makes the client advertise the sampling capability.
        async with ClientSession(read, write, sampling_callback=sampling_callback) as session:
            await session.initialize()
            result = await session.call_tool(
                "explain_change", {"base": base, "head": head, "repo_path": repo})
            if result.isError:
                sys.exit(result.content[0].text)
            print(result.structuredContent["summary"])
            print(f"\nanalysis_source: {result.structuredContent['analysis_source']}")


def check_backend(backend: str) -> str | None:
    """Return a problem description if the chosen backend cannot work, else None."""
    if backend == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        return ("ANTHROPIC_API_KEY is not set in this shell. Export it, or run with "
                "--backend claude-cli to answer sampling requests with your logged-in `claude` command.")
    if backend == "claude-cli" and not shutil.which("claude"):
        return "the `claude` command was not found on PATH."
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="change-lens example client with a sampling callback")
    ap.add_argument("base")
    ap.add_argument("head", nargs="?", default="HEAD")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--backend", choices=["api", "claude-cli"], default="api")
    a = ap.parse_args()
    BACKEND = a.backend
    if problem := check_backend(BACKEND):
        sys.exit(f"error: {problem}")
    asyncio.run(main(a.base, a.head, os.path.abspath(a.repo)))
