import subprocess
import pytest
from change_lens import gitops, analysis
from change_lens.server import mcp
from mcp.shared.memory import create_connected_server_and_client_session
import asyncio


def sh(cwd, *a):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    sh(tmp_path, "git", "init", "-q", "-b", "main")
    (tmp_path / "svc.py").write_text("def fetch(url):\n    return get(url)\n")
    (tmp_path / "test_svc.py").write_text("from svc import fetch\ndef test_fetch_ok():\n    fetch('x')\n")
    sh(tmp_path, "git", "add", "."); sh(tmp_path, "git", "commit", "-qm", "init")
    (tmp_path / "svc.py").write_text("def fetch(url):\n    for _ in range(3):\n        r = get(url)\n    return r\n")
    sh(tmp_path, "git", "commit", "-qam", "retry")
    monkeypatch.setenv("CHANGE_LENS_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def call(args):
    async def run():
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            return await client.call_tool("explain_change", args)
    return asyncio.run(run())


def test_facts_without_llm(repo):
    res = call({"base": "HEAD~1", "head": "HEAD", "repo_path": str(repo)})
    assert not res.isError
    s = res.structuredContent
    assert s["analysis_source"] == "none"
    assert "svc.py:fetch" in s["changed_functions"]
    assert "test_svc.py" in s["summary"]


def test_rejects_option_injection(repo):
    res = call({"base": "--output=/tmp/x", "repo_path": str(repo)})
    assert res.isError and "Invalid ref" in res.content[0].text


def test_rejects_outside_root(repo):
    res = call({"base": "a", "head": "b", "repo_path": "/"})
    assert res.isError and "outside the allowed roots" in res.content[0].text


def test_tool_metadata():
    async def run():
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            return (await client.list_tools()).tools[0]
    tool = asyncio.run(run())
    assert tool.annotations.readOnlyHint and not tool.annotations.destructiveHint
    assert "behaviour_changes" in tool.outputSchema["properties"]


def test_classification_via_client_sampling(repo):
    from mcp.types import CreateMessageResult, TextContent
    seen = {}

    async def fake_llm(context, params):
        seen["prompt"] = params.messages[0].content.text
        return CreateMessageResult(role="assistant", model="fake", content=TextContent(type="text", text=
            '{"behaviour_changes":["fetch may now retry 3 times"],"existing_tests":[],'
            '"affected_components":["svc"],"missing_tests":["non-retryable errors"],"confidence":"Medium"}'))

    async def run():
        async with create_connected_server_and_client_session(mcp._mcp_server, sampling_callback=fake_llm) as c:
            return await c.call_tool("explain_change", {"base": "HEAD~1", "repo_path": str(repo)})
    res = asyncio.run(run())
    assert res.structuredContent["analysis_source"] == "client-sampling"
    assert "retry 3 times" in res.structuredContent["summary"]
    assert "<untrusted_repository_content>" in seen["prompt"] and "range(3)" in seen["prompt"]


def test_note_says_sampling_unsupported(repo):
    res = call({"base": "HEAD~1", "head": "HEAD", "repo_path": str(repo)})
    note = res.structuredContent["summary"].split("Note: ", 1)[1]
    assert "the client does not support sampling" in note
    assert "ANTHROPIC_API_KEY is not set" in note
    assert "failed" not in note


def test_failed_sampling_is_logged_and_reported(repo):
    from mcp.types import ErrorData
    logs = []

    async def broken_llm(context, params):
        return ErrorData(code=-32603, message="Could not resolve authentication method")

    async def on_log(params):
        logs.append(params.data)

    async def run():
        async with create_connected_server_and_client_session(
                mcp._mcp_server, sampling_callback=broken_llm, logging_callback=on_log) as c:
            return await c.call_tool("explain_change", {"base": "HEAD~1", "repo_path": str(repo)})
    res = asyncio.run(run())
    s = res.structuredContent
    assert s["analysis_source"] == "none"
    note = s["summary"].split("Note: ", 1)[1]
    assert "client sampling failed" in note and "Could not resolve authentication method" in note
    assert "does not support sampling" not in note
    assert any("client sampling failed" in str(m) for m in logs)
    assert not any("range(3)" in str(m) for m in logs)  # repository content is never logged


def test_unparseable_sampling_reply_falls_back(repo):
    from mcp.types import CreateMessageResult, TextContent

    async def chatty_llm(context, params):
        return CreateMessageResult(role="assistant", model="fake",
                                   content=TextContent(type="text", text="Sure! No JSON here."))

    async def run():
        async with create_connected_server_and_client_session(mcp._mcp_server, sampling_callback=chatty_llm) as c:
            return await c.call_tool("explain_change", {"base": "HEAD~1", "repo_path": str(repo)})
    res = asyncio.run(run())
    assert not res.isError
    assert res.structuredContent["analysis_source"] == "none"
    assert "client sampling failed (ValueError: no JSON object in reply)" in res.structuredContent["summary"]


@pytest.mark.parametrize("line", [
    "        raise ValueError(f'x')", "    assert any(m for m in logs)", "    elif check(x):",
    "    with open(path) as f:", "    except ValueError(", "        or check(x)", "        and check(x)",
    "    not check(x)", "    print foo(x)", "    try something(", "    pass helper(", "    del cache.pop(k)",
    "    global foo(", "    goto next(x)", "    typeof foo(",
])
def test_statement_keywords_are_not_return_types(line):
    assert analysis.func_name(line) is None, line


@pytest.mark.parametrize("line,name", [
    ("    public static void main(String[] a) {", "main"),
    ("    private int count(List<X> l) {", "count"),
    ("    public void fromJson(String s) {", "fromJson"),   # starts with a keyword, is still a name
    ("    int isValid(int x) {", "isValid"),
    ("  static async load(url) {", "load"),
])
def test_real_definitions_still_detected(line, name):
    assert analysis.func_name(line) == name


def test_calls_and_control_flow_are_not_definitions():
    for line in ["        match http_call(req) {", "    if check(x) {", "    return get(url)", "    http_call(req)"]:
        assert analysis.func_name(line) is None, line
    assert analysis.func_name("pub async fn send(req: &Request) -> Result<()> {") == "send"


def test_identical_refs_is_not_an_error(repo):
    res = call({"base": "main", "head": "HEAD", "repo_path": str(repo)})
    assert not res.isError
    assert res.structuredContent["has_changes"] is False
    assert "same commit" in res.structuredContent["summary"]


def test_docs_and_config_changes_are_reported(repo):
    (repo / "README.md").write_text("# Svc\nRetries three times.\n")
    (repo / "settings.toml").write_text("retries = 3\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    sh(repo, "git", "add", "."); sh(repo, "git", "commit", "-qm", "docs and config")
    s = call({"base": "HEAD~1", "repo_path": str(repo)}).structuredContent
    assert s["changed_files_by_kind"] == {"ci": [".github/workflows/ci.yml"], "docs": ["README.md"],
                                          "config": ["settings.toml"]}
    assert "README.md [docs, added, +2/-0]" in s["summary"]


def test_file_kinds():
    k = analysis.file_kind
    assert [k("src/a.rs"), k("tests/a_test.rs"), k("LICENSE"), k("docs/guide/x.html"), k("pyproject.toml"),
            k("img/logo.png")] == ["code", "test", "docs", "docs", "config", "other"]


# --- direct Anthropic API path (strategy 2), with a fake client instead of the real API ---

REPORT = {"behaviour_changes": ["fetch may now retry 3 times"], "existing_tests": [],
          "affected_components": ["svc"], "missing_tests": ["non-retryable errors"],
          "confidence": "Medium", "confidence_reason": "fake"}


class FakeAnthropic:
    """Stands in for anthropic.Anthropic; records the request and returns a canned reply."""
    calls: list = []
    reply = None   # list of content blocks, or an Exception to raise

    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        if isinstance(FakeAnthropic.reply, Exception):
            raise FakeAnthropic.reply
        return type("Resp", (), {"content": FakeAnthropic.reply})()


def tool_use(payload):
    return type("Block", (), {"type": "tool_use", "input": payload})()


@pytest.fixture
def fake_api(repo, monkeypatch):
    import anthropic
    FakeAnthropic.calls, FakeAnthropic.reply = [], [tool_use(REPORT)]
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CHANGE_LENS_MODEL", raising=False)
    return repo


def call_with(repo, sampling_callback=None, logs=None):
    async def on_log(params):
        logs.append(params.data)

    async def run():
        async with create_connected_server_and_client_session(
                mcp._mcp_server, sampling_callback=sampling_callback,
                logging_callback=on_log if logs is not None else None) as c:
            return await c.call_tool("explain_change", {"base": "HEAD~1", "repo_path": str(repo)})
    return asyncio.run(run())


def test_api_used_when_client_has_no_sampling(fake_api):
    res = call_with(fake_api)
    s = res.structuredContent
    assert not res.isError
    assert s["analysis_source"] == "anthropic-api"
    assert "retry 3 times" in s["summary"] and s["confidence"] == "Medium"
    assert "Note:" not in s["summary"]


def test_api_request_shape(fake_api):
    call_with(fake_api)
    (req,) = FakeAnthropic.calls
    assert req["model"] == "claude-sonnet-5-5"
    assert req["max_tokens"] == 4000
    assert req["tool_choice"] == {"type": "tool", "name": "report_impact"}
    assert [t["name"] for t in req["tools"]] == ["report_impact"]
    (msg,) = req["messages"]
    assert msg["role"] == "user"
    assert msg["content"].startswith("<untrusted_repository_content>")
    assert "range(3)" in msg["content"] and msg["content"].rstrip().endswith("</untrusted_repository_content>")
    assert "ANTHROPIC_API_KEY" not in str(req) and "sk-test" not in str(req)   # key is never sent as content


def test_api_model_is_configurable(fake_api, monkeypatch):
    monkeypatch.setenv("CHANGE_LENS_MODEL", "my-model")
    call_with(fake_api)
    assert FakeAnthropic.calls[0]["model"] == "my-model"


def test_sampling_takes_priority_over_the_api(fake_api):
    from mcp.types import CreateMessageResult, TextContent

    async def llm(context, params):
        import json
        return CreateMessageResult(role="assistant", model="fake",
                                   content=TextContent(type="text", text=json.dumps(REPORT)))
    res = call_with(fake_api, sampling_callback=llm)
    assert res.structuredContent["analysis_source"] == "client-sampling"
    assert FakeAnthropic.calls == []          # key is set, but the API is never called


def test_api_is_the_fallback_when_sampling_fails(fake_api):
    from mcp.types import ErrorData

    async def broken(context, params):
        return ErrorData(code=-32603, message="boom")
    res = call_with(fake_api, sampling_callback=broken)
    s = res.structuredContent
    assert s["analysis_source"] == "anthropic-api"
    assert len(FakeAnthropic.calls) == 1
    assert "Note:" not in s["summary"]        # a successful fallback leaves no failure note


def test_api_failure_is_logged_and_reported(fake_api):
    FakeAnthropic.reply = RuntimeError("401 invalid x-api-key")
    logs = []
    res = call_with(fake_api, logs=logs)
    s = res.structuredContent
    assert not res.isError
    assert s["analysis_source"] == "none" and s["confidence"] == "Low"
    note = s["summary"].split("Note: ", 1)[1]
    assert "the client does not support sampling" in note
    assert "Anthropic API call failed (RuntimeError: 401 invalid x-api-key)" in note
    assert "ANTHROPIC_API_KEY is not set" not in note      # it is set, so do not claim otherwise
    assert any("Anthropic API call failed" in str(m) for m in logs)
    assert not any("range(3)" in str(m) or "sk-test" in str(m) for m in logs)


def test_api_reply_without_a_structured_report_is_a_failure(fake_api):
    FakeAnthropic.reply = [type("Block", (), {"type": "text", "text": "I refuse"})()]
    res = call_with(fake_api)
    assert res.structuredContent["analysis_source"] == "none"
    assert "model returned no structured report" in res.structuredContent["summary"]


# --- two-step mode: the caller runs the analysis with its own model (defer_analysis / report) ---

def args_for(repo, **extra):
    return {"base": "HEAD~1", "head": "HEAD", "repo_path": str(repo), **extra}


def test_defer_analysis_returns_a_request_and_runs_no_model(fake_api):
    from mcp.types import CreateMessageResult, TextContent
    asked = []

    async def sampler(context, params):
        asked.append(1)
        return CreateMessageResult(role="assistant", model="x", content=TextContent(type="text", text="{}"))

    async def run():
        async with create_connected_server_and_client_session(mcp._mcp_server, sampling_callback=sampler) as c:
            return await c.call_tool("explain_change", args_for(fake_api, defer_analysis=True))
    res = asyncio.run(run())
    s = res.structuredContent
    assert not res.isError
    assert s["analysis_source"] == "none" and s["has_changes"] is True
    assert "analysis deferred" in s["summary"]
    assert asked == [] and FakeAnthropic.calls == []           # neither sampling nor the API key was used
    req = s["analysis_request"]
    assert req["prompt"].startswith("<untrusted_repository_content>")
    assert "range(3)" in req["prompt"] and req["prompt"].rstrip().endswith("</untrusted_repository_content>")
    assert "behaviour_changes" in req["system"]                # the JSON format the reply must follow
    assert req["max_tokens"] > 0


def test_report_is_validated_and_rendered(fake_api):
    import json
    res = call(args_for(fake_api, report=json.dumps(REPORT)))
    s = res.structuredContent
    assert not res.isError
    assert s["analysis_source"] == "host-model" and s["analysis_request"] is None
    assert "retry 3 times" in s["summary"] and s["confidence"] == "Medium"
    assert "Note:" not in s["summary"]
    assert FakeAnthropic.calls == []


def test_report_may_be_wrapped_in_prose(fake_api):
    import json
    res = call(args_for(fake_api, report="Here is the analysis:\n" + json.dumps(REPORT) + "\nDone."))
    assert res.structuredContent["analysis_source"] == "host-model"


@pytest.mark.parametrize("bad", ["no json here", "{}", '{"behaviour_changes": []}', "x" * 100_001])
def test_invalid_report_is_a_tool_error(fake_api, bad):
    res = call(args_for(fake_api, report=bad))
    assert res.isError and "not a valid analysis reply" in res.content[0].text


def test_defer_and_report_together_are_rejected(fake_api):
    import json
    res = call(args_for(fake_api, defer_analysis=True, report=json.dumps(REPORT)))
    assert res.isError and "not both" in res.content[0].text


def test_default_call_has_no_analysis_request(repo):
    res = call(args_for(repo))
    assert res.structuredContent["analysis_request"] is None
    assert res.structuredContent["analysis_source"] == "none"


def test_report_may_be_passed_as_an_object(fake_api):
    res = call(args_for(fake_api, report=REPORT))   # a dict, as the MCP layer delivers a JSON-looking string
    assert not res.isError and res.structuredContent["analysis_source"] == "host-model"
