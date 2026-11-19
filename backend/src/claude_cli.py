"""LLM provider ``claude-cli:<model>``: the Claude Code CLI on a Claude subscription.

Fork feature (docs/claude-cli-provider.md). Each model request runs
``claude -p`` with no tools and the agent's output schema as ``--json-schema``,
so pydantic-ai gets validated structured output. Authentication is the
subscription token from ``claude setup-token`` (CLAUDE_CODE_OAUTH_TOKEN); an
ANTHROPIC_API_KEY in the environment is removed so it never bills the API.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

CLAUDE_BIN = os.getenv("CLAUDE_CLI_BIN", "claude")
TIMEOUT_SECONDS = float(os.getenv("CLAUDE_CLI_TIMEOUT_SECONDS", "900"))
# Credentials the CLI would prefer over the subscription token.
_API_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def cli_available() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)) and all(isinstance(item, str) for item in content):
        return "\n".join(content)
    return json.dumps(content, default=str, ensure_ascii=False)


def render(messages: list[ModelMessage], instructions: str | None) -> tuple[str, str]:
    """Split a pydantic-ai conversation into (system prompt, prompt text)."""
    system: list[str] = [instructions] if instructions else []
    turns: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    system.append(part.content)
                elif isinstance(part, UserPromptPart):
                    turns.append(_text(part.content))
                elif isinstance(part, RetryPromptPart):
                    turns.append("Your previous answer was rejected:\n" + part.model_response())
                elif isinstance(part, ToolReturnPart):
                    turns.append(f"Result of {part.tool_name}:\n{_text(part.content)}")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart):
                    turns.append("Your previous answer:\n" + part.content)
                elif isinstance(part, ToolCallPart):
                    turns.append("Your previous answer:\n" + json.dumps(part.args_as_dict(), ensure_ascii=False))
    return "\n\n".join(s for s in system if s), "\n\n".join(turns)


def build_command(model: str, system: str, schema: dict | None) -> list[str]:
    command = [
        CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", system or "Follow the user's instructions.",
    ]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema)]
    return command


async def run_cli(command: list[str], prompt: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _API_CREDENTIALS}
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd="/tmp",  # no project CLAUDE.md
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"claude CLI timed out after {TIMEOUT_SECONDS:.0f}s") from None
    try:
        data = json.loads(out or b"{}")
    except json.JSONDecodeError:
        detail = (err or out).decode(errors="replace").strip()[-500:]
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {detail}") from None
    if data.get("is_error") or proc.returncode != 0:
        raise RuntimeError(f"claude CLI error: {str(data.get('result') or data)[:500]}")
    return data


def claude_cli_model(model: str) -> FunctionModel:
    """A pydantic-ai model whose requests run through ``claude -p``."""

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        system, prompt = render(messages, info.instructions)
        output_tool = info.output_tools[0] if info.output_tools else None
        output_object = getattr(info.model_request_parameters, "output_object", None)
        schema = (
            output_tool.parameters_json_schema if output_tool
            else output_object.json_schema if output_object
            else None
        )
        data = await run_cli(build_command(model, system, schema), prompt)
        structured = data.get("structured_output")
        if output_tool:
            if structured is None:
                raise RuntimeError("claude CLI returned no structured output")
            return ModelResponse(parts=[ToolCallPart(output_tool.name, structured)], model_name=f"claude-cli:{model}")
        if output_object and structured is not None:
            return ModelResponse(parts=[TextPart(json.dumps(structured))], model_name=f"claude-cli:{model}")
        return ModelResponse(parts=[TextPart(str(data.get("result", "")))], model_name=f"claude-cli:{model}")

    return FunctionModel(respond, model_name=f"claude-cli:{model}")
