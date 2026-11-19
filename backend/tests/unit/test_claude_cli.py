import json
import stat

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

from src import claude_cli


class Pick(BaseModel):
    best: str
    score: int


def fake_cli(tmp_path, payload):
    """A stand-in `claude` that records its argv/stdin/env and prints ``payload``."""
    log = tmp_path / "call.json"
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'api_key': os.environ.get('ANTHROPIC_API_KEY')}}, open({str(log)!r}, 'w'))\n"
        f"print({json.dumps(json.dumps(payload))})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


@pytest.mark.asyncio
async def test_structured_output_through_an_agent(tmp_path, monkeypatch):
    script, log = fake_cli(tmp_path, {"type": "result", "is_error": False, "structured_output": {"best": "clutch", "score": 88}})
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")

    agent = Agent(claude_cli.claude_cli_model("sonnet"), output_type=Pick, system_prompt="Pick moments.")
    result = await agent.run("[00:05] olha esse clutch")

    assert result.output == Pick(best="clutch", score=88)
    call = json.loads(log.read_text())
    assert call["api_key"] is None  # subscription only, never the API
    assert call["argv"][call["argv"].index("--model") + 1] == "sonnet"
    assert call["argv"][call["argv"].index("--tools") + 1] == ""
    schema = json.loads(call["argv"][call["argv"].index("--json-schema") + 1])
    assert set(schema["required"]) == {"best", "score"}
    assert "Pick moments." in call["argv"][call["argv"].index("--system-prompt") + 1]
    assert "olha esse clutch" in call["stdin"]


@pytest.mark.asyncio
async def test_cli_error_is_raised(tmp_path, monkeypatch):
    script, _ = fake_cli(tmp_path, {"type": "result", "is_error": True, "result": "Not logged in"})
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", str(script))
    agent = Agent(claude_cli.claude_cli_model("haiku"), output_type=Pick)
    with pytest.raises(RuntimeError, match="Not logged in"):
        await agent.run("x")


@pytest.mark.asyncio
async def test_cancelled_request_kills_the_cli(tmp_path, monkeypatch):
    import asyncio
    import os

    pid_file = tmp_path / "pid"
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 60\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", str(script))

    task = asyncio.create_task(claude_cli.run_cli([str(script)], "x"))
    for _ in range(50):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_errors_never_carry_raw_output(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\necho 'transcript secret words'\necho 'more secret' >&2\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", str(script))
    with pytest.raises(RuntimeError) as error:
        await claude_cli.run_cli([str(script)], "x")
    assert "secret" not in str(error.value)


def test_provider_is_known_and_checks_the_binary(monkeypatch):
    from src.ai import _get_missing_llm_key_error
    from src.config import get_config

    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", "/nonexistent/claude")
    assert "not installed" in _get_missing_llm_key_error("claude-cli:sonnet", get_config())
    monkeypatch.setattr(claude_cli, "CLAUDE_BIN", "sh")
    assert _get_missing_llm_key_error("claude-cli:sonnet", get_config()) is None
