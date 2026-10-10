"""Minimal MCP client that supports sampling, so change-lens can use it end to end.

Usage (from inside the repository you want to analyse):

    export ANTHROPIC_API_KEY=...        # used by THIS client, never by the server
    python examples/sampling_client.py HEAD~1 HEAD

The client starts the server over stdio and registers a sampling callback. When the server asks
for a classification, the callback forwards the request to Claude and returns the answer. The
server is started without ANTHROPIC_API_KEY on purpose, so a report with
`analysis_source: "client-sampling"` proves the sampling path ran.

Set CHANGE_LENS_SAMPLING_MODEL to change the model (default: claude-sonnet-5-5).
"""
import asyncio
import os
import sys

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.context import RequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent

MODEL = os.environ.get("CHANGE_LENS_SAMPLING_MODEL", "claude-sonnet-5-5")
claude = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from this process


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
          f"max_tokens={params.maxTokens}); calling {MODEL}", file=sys.stderr)
    resp = await claude.messages.create(
        model=MODEL,  # modelPreferences from the server are hints; the client decides
        max_tokens=params.maxTokens,
        system=params.systemPrompt or "",
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return CreateMessageResult(
        role="assistant", model=resp.model, content=TextContent(type="text", text=text),
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: sampling_client.py BASE [HEAD] [REPO_PATH]")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "HEAD",
                     os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else ".")))
