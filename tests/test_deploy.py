"""Settings and behaviour the server deployment relies on: model-call timeout and the heartbeat."""

import asyncio
import logging
import time
from types import SimpleNamespace

import httpx
import pytest

from stockbot import config
from stockbot.bot import handlers
from stockbot.llm.client import LLM

from .conftest import make_settings

HC = "https://hc-ping.com/abc-123"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    for key in ("OLLAMA_TIMEOUT", "HEARTBEAT_URL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_ollama_timeout_defaults_to_180_and_can_be_overridden(env):
    assert config.load_settings().ollama_timeout == 180
    env.setenv("OLLAMA_TIMEOUT", "420")
    assert config.load_settings().ollama_timeout == 420


def test_heartbeat_url_is_off_by_default_and_trailing_slash_is_dropped(env):
    assert config.load_settings().heartbeat_url == ""
    env.setenv("HEARTBEAT_URL", HC + "/")
    assert config.load_settings().heartbeat_url == HC


def test_model_call_times_out_instead_of_hanging():
    """An Ollama that accepts the connection but never answers must not block the bot."""

    async def run():
        async def silent(reader, writer):
            await asyncio.sleep(30)

        server = await asyncio.start_server(silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        llm = LLM(f"http://127.0.0.1:{port}", "test", 2048, timeout=0.3)
        start = time.monotonic()
        try:
            with pytest.raises(httpx.TimeoutException):
                await llm.chat([{"role": "user", "content": "hi"}])
        finally:
            server.close()
        return time.monotonic() - start

    assert asyncio.run(run()) < 5


# --- heartbeat ---


def monitor_ctx(monkeypatch, *, url=HC, check_fails=False, ping_fails=False):
    """A monitor_job context whose engine and heartbeat transport are stand-ins; returns (ctx, pinged urls)."""
    pinged = []

    def respond(request: httpx.Request) -> httpx.Response:
        pinged.append(str(request.url))
        if ping_fails:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, text="OK")

    async def check(chat_ids, send):
        if check_fails:
            raise RuntimeError("yahoo down")
        return 0

    monkeypatch.setattr(handlers, "heartbeat_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    eng = SimpleNamespace(settings=make_settings(heartbeat_url=url), check=check)
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"engine": eng}), bot=None)
    return ctx, pinged


def test_successful_monitor_pass_pings_the_heartbeat(monkeypatch):
    ctx, pinged = monitor_ctx(monkeypatch)
    asyncio.run(handlers.monitor_job(ctx))
    assert pinged == [HC]


def test_failed_monitor_pass_pings_fail(monkeypatch):
    ctx, pinged = monitor_ctx(monkeypatch, check_fails=True)
    asyncio.run(handlers.monitor_job(ctx))
    assert pinged == [HC + "/fail"]


def test_unreachable_heartbeat_is_logged_not_raised(monkeypatch, caplog):
    ctx, pinged = monitor_ctx(monkeypatch, ping_fails=True)
    with caplog.at_level(logging.WARNING):
        asyncio.run(handlers.monitor_job(ctx))
    assert pinged == [HC]
    assert "heartbeat ping failed (ConnectError)" in caplog.text
    assert "abc-123" not in caplog.text  # the check's URL is a secret


def test_no_heartbeat_url_sends_nothing(monkeypatch):
    ctx, pinged = monitor_ctx(monkeypatch, url="")
    asyncio.run(handlers.monitor_job(ctx))
    assert pinged == []
