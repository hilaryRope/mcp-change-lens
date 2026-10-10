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
