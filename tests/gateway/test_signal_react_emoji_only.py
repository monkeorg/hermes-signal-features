"""Safety net: an emoji-only reply becomes a reaction (platforms.signal.extra.auto_react)."""
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.event import MessageEvent, ProcessingOutcome

GROUP = "group:abc123=="
DM = "+15550001111"


@pytest.fixture(autouse=True)
def _reset_signal_scheduler():
    from gateway.platforms.signal_rate_limit import _reset_scheduler
    _reset_scheduler()
    yield
    _reset_scheduler()


def _adapter(monkeypatch, **extra):
    monkeypatch.setenv("SIGNAL_GROUP_ALLOWED_USERS", "*")
    monkeypatch.delenv("SIGNAL_AUTO_REACT", raising=False)
    from gateway.platforms.signal import SignalAdapter
    config = PlatformConfig()
    config.enabled = True
    config.extra = {"http_url": "http://localhost:8080", "account": "+15559998888", **extra}
    return SignalAdapter(config)


def _capture_rpc(adapter):
    calls = []

    async def rpc(method, params, rpc_id=None, **kwargs):
        calls.append({"method": method, "params": dict(params)})
        return {"timestamp": 1700000000000 + len(calls)} if method == "send" else {}

    adapter._rpc = rpc
    return calls


def _out(calls):
    """Outbound messages/reactions only (ignores the sendTyping stop / contact lookups around them)."""
    return [c for c in calls if c["method"] in ("send", "sendReaction")]

class TestBareEmojiSafetyNet:
    @pytest.mark.parametrize("text,ok", [("👍", True), ("  😂 \n", True), ("❤️", True), ("👍🏽", True),
                                         ("ok", False), ("👍 ok", False), (":)", False), ("...", False), ("", False)])
    def test_detect(self, text, ok):
        from gateway.platforms.signal import _is_bare_emoji_reply
        assert _is_bare_emoji_reply(text) is ok

    @pytest.mark.asyncio
    async def test_bare_emoji_with_reply_to_becomes_reaction(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(GROUP, 1000, "+15550001111", "thanks!")
        calls = _capture_rpc(a)
        result = await a.send(GROUP, "👍", reply_to="1000")
        assert result.success
        assert [c["method"] for c in _out(calls)] == ["sendReaction"]

    @pytest.mark.asyncio
    async def test_bare_emoji_without_reply_to_or_flag_stays_text(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        calls = _capture_rpc(a)
        await a.send(GROUP, "👍")
        assert [c["method"] for c in _out(calls)] == ["send"]
        b = _adapter(monkeypatch)
        b._remember_inbound(GROUP, 1000, "+15550001111", "x")
        calls = _capture_rpc(b)
        await b.send(GROUP, "👍", reply_to="1000")
        assert [c["method"] for c in _out(calls)] == ["send"]
