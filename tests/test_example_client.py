import asyncio
import importlib.util
import json
import stat
from pathlib import Path

import pytest
from mcp.types import CreateMessageRequestParams, SamplingMessage, TextContent

SPEC = importlib.util.spec_from_file_location(
    "sampling_client", Path(__file__).parent.parent / "examples" / "sampling_client.py")
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)

REPORT = {"behaviour_changes": ["fetch may now retry 3 times"], "existing_tests": [],
          "affected_components": [], "missing_tests": [], "confidence": "Low"}


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A stand-in `claude` that records how it was called and prints a canned report."""
    log = tmp_path / "call.json"
    script = tmp_path / "bin" / "claude"
    script.parent.mkdir()
    script.write_text(f"""#!/usr/bin/env python3
import json, os, sys
json.dump({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}}, open({str(log)!r}, "w"))
print({json.dumps(json.dumps(REPORT))})
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}:/usr/bin:/bin")
    monkeypatch.setattr(client, "BACKEND", "claude-cli")
    monkeypatch.setattr(client, "MODEL", None)
    return log


def params(text="SECRET-REPO-TEXT"):
    return CreateMessageRequestParams(
        messages=[SamplingMessage(role="user", content=TextContent(type="text", text=text))],
        systemPrompt="SYSTEM", maxTokens=3000)


def test_claude_cli_backend_is_locked_down(fake_claude):
    res = asyncio.run(client.sampling_callback(None, params()))
    assert json.loads(res.content.text)["behaviour_changes"] == ["fetch may now retry 3 times"]
    call = json.loads(fake_claude.read_text())
    argv = call["argv"]
    assert argv[0] == "-p"
    assert argv[argv.index("--tools") + 1] == ""                    # no tools at all
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--system-prompt") + 1] == "SYSTEM"
    assert call["stdin"] == "SECRET-REPO-TEXT"                      # repository text goes over stdin
    assert "SECRET-REPO-TEXT" not in " ".join(argv)                 # ...never on the command line
    assert Path(call["cwd"]) != Path.cwd()                          # runs outside the repository


def test_claude_cli_model_is_passed_when_set(fake_claude, monkeypatch):
    monkeypatch.setattr(client, "MODEL", "my-model")
    res = asyncio.run(client.sampling_callback(None, params()))
    argv = json.loads(fake_claude.read_text())["argv"]
    assert argv[argv.index("--model") + 1] == "my-model" and res.model == "my-model"


def test_claude_cli_failure_surfaces_stderr(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(client, "BACKEND", "claude-cli")
    with pytest.raises(RuntimeError, match="exited with 1: not logged in"):
        asyncio.run(client.sampling_callback(None, params()))


def test_backend_checks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "ANTHROPIC_API_KEY is not set" in client.check_backend("api")
    assert "--backend claude-cli" in client.check_backend("api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert client.check_backend("api") is None
    monkeypatch.setenv("PATH", "/nonexistent")
    assert "not found" in client.check_backend("claude-cli")
