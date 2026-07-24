import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent import cli_loop as cli_module
from app.agent.cli_loop import ClaudeCLILoop


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 1234
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def terminate(self):
        self.returncode = 0

    async def wait(self):
        return self.returncode


class FakeWriter:
    def __init__(self):
        self.closed = False
        self.payloads = []

    def is_closing(self):
        return self.closed

    def write(self, payload):
        self.payloads.append(payload)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_second_message_reuses_healthy_process(monkeypatch):
    loop = ClaudeCLILoop()
    process = FakeProcess()
    writer = FakeWriter()
    starts = 0

    async def fake_start(*args, **kwargs):
        nonlocal starts
        starts += 1
        loop._processes["user"] = process
        loop._stdin_writers["user"] = writer

    monkeypatch.setattr(loop, "_start_process", fake_start)
    monkeypatch.setattr(cli_module.feishu_client, "send_card", AsyncMock(return_value={}))

    first = asyncio.create_task(loop.send_and_wait("one", "user", "."))
    await asyncio.sleep(0)
    loop._response_futures["user"][0][0].set_result("first")
    assert await first == "first"
    loop._response_futures["user"].pop(0)

    second = asyncio.create_task(loop.send_and_wait("two", "user", "."))
    await asyncio.sleep(0)
    loop._response_futures["user"][0][0].set_result("second")
    assert await second == "second"

    assert starts == 1
    assert not writer.closed
    assert len(writer.payloads) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("exact-session", ["--resume", "exact-session"]),
        ("__continue__", ["--continue"]),
    ],
)
async def test_process_restart_uses_expected_session_flag(
    monkeypatch, tmp_path, session_id, expected,
):
    loop = ClaudeCLILoop()
    captured = []
    process = FakeProcess()

    async def fake_create(*args, **kwargs):
        captured.extend(args)
        process.stdin = FakeWriter()
        return process

    async def idle_reader(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(cli_module.shutil, "which", lambda _: "claude.exe")
    monkeypatch.setattr(cli_module, "_build_env", lambda *_: {})
    monkeypatch.setattr(cli_module, "_ensure_hook_config", AsyncMock())
    monkeypatch.setattr(cli_module.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(loop, "_read_loop", idle_reader)

    await loop._start_process(
        "user", str(tmp_path), "opus", "m", "glm",
        claude_session_id=session_id,
    )
    joined = " ".join(captured)
    assert " ".join(expected) in joined
    if session_id == "exact-session":
        assert "--continue" not in captured

    await loop.cancel_and_wait("user")