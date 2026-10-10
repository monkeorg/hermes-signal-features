"""Tests for agent reactions instead of text (platforms.signal.extra.auto_react, ``[[react:<emoji>]]``)."""
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


class TestTokenParsing:
    @pytest.mark.parametrize("text,token,rest", [
        ("[[react:👍]]", "👍", ""),
        ("  [[react: ❤️ ]] thank you", "❤️", "thank you"),
        ("[[react:👍🏽]]\nok", "👍🏽", "ok"),
        ("no token [[react:👍]]", None, "no token [[react:👍]]"),
        ("plain", None, "plain")])
    def test_split(self, text, token, rest):
        from gateway.platforms.signal import _split_react_token
        assert _split_react_token(text) == (token, rest)

    def test_emoji_validation(self):
        from gateway.platforms.signal import _is_reaction_emoji
        assert _is_reaction_emoji("👍") and _is_reaction_emoji("❤️")
        assert not _is_reaction_emoji("thumbsup") and not _is_reaction_emoji("") and not _is_reaction_emoji("a👍")

    def test_config_flag(self, monkeypatch):
        assert _adapter(monkeypatch).auto_react is False
        assert _adapter(monkeypatch, auto_react=True).auto_react is True


class TestReactInsteadOfText:
    @pytest.mark.asyncio
    async def test_reaction_only_reply_sends_reaction_and_no_text(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(GROUP, 1000, "+15550001111", "thanks!")
        calls = _capture_rpc(a)
        result = await a.send(GROUP, "[[react:👍]]", reply_to="1000")
        assert result.success
        assert [c["method"] for c in _out(calls)] == ["sendReaction"]
        assert _out(calls)[0]["params"] == {
            "account": "+15559998888", "emoji": "👍", "targetAuthor": "+15550001111",
            "targetTimestamp": 1000, "groupId": "abc123=="}

    @pytest.mark.asyncio
    async def test_reaction_plus_text_sends_both(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True, quote_replies="never")
        a._remember_inbound(DM, 1000, DM, "ok")
        calls = _capture_rpc(a)
        await a.send(DM, "[[react:✅]] done", reply_to="1000")
        methods = [c["method"] for c in _out(calls)]
        assert methods.count("sendReaction") == 1 and methods.count("send") == 1
        assert [c for c in calls if c["method"] == "send"][0]["params"]["message"] == "done"

    @pytest.mark.asyncio
    async def test_falls_back_to_newest_inbound_without_anchor(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(DM, 1000, DM, "a")
        a._remember_inbound(DM, 2000, DM, "b")
        calls = _capture_rpc(a)
        await a.send(DM, "[[react:👍]]")
        assert _out(calls)[0]["params"]["targetTimestamp"] == 2000

    @pytest.mark.asyncio
    async def test_disabled_or_invalid_token_is_dropped_never_sent_as_text(self, monkeypatch):
        off = _adapter(monkeypatch)
        off._remember_inbound(DM, 1000, DM, "x")
        calls = _capture_rpc(off)
        assert (await off.send(DM, "[[react:👍]]", reply_to="1000")).success
        assert _out(calls) == []
        on = _adapter(monkeypatch, auto_react=True)
        on._remember_inbound(DM, 1000, DM, "x")
        calls = _capture_rpc(on)
        await on.send(DM, "[[react:thumbsup]]", reply_to="1000")
        assert _out(calls) == []

    @pytest.mark.asyncio
    async def test_unknown_target_sends_nothing_and_does_not_crash(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        calls = _capture_rpc(a)
        assert (await a.send(DM, "[[react:👍]]", reply_to="5")).success
        assert _out(calls) == []

    @pytest.mark.asyncio
    async def test_add_reaction_tool_entry_point(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(DM, 1000, DM, "x")
        calls = _capture_rpc(a)
        assert await a.add_reaction(DM, "🎉") == {"success": True}
        assert _out(calls)[0]["params"]["targetTimestamp"] == 1000
        assert (await a.add_reaction(DM, "nope"))["success"] is False
        assert (await a.add_reaction("+1999", "🎉"))["success"] is False


class TestCompletionSwap:
    def _event(self, adapter, chat_id, ts):
        source = adapter.build_source(chat_id=chat_id, chat_type="dm", user_id=DM, user_name="T")
        return MessageEvent(source=source, text="x", message_id=str(ts),
                            raw_message={"sender": DM, "timestamp_ms": ts})

    @pytest.mark.asyncio
    async def test_checkmark_does_not_overwrite_agent_reaction(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(DM, 1000, DM, "thanks")
        calls = _capture_rpc(a)
        await a.send(DM, "[[react:👍]]", reply_to="1000")
        calls.clear()
        await a.on_processing_complete(self._event(a, DM, 1000), ProcessingOutcome.SUCCESS)
        assert calls == []

    @pytest.mark.asyncio
    async def test_checkmark_still_sent_when_agent_answered_with_text(self, monkeypatch):
        a = _adapter(monkeypatch, auto_react=True)
        a._remember_inbound(DM, 1000, DM, "question")
        calls = _capture_rpc(a)
        await a.send(DM, "answer", reply_to="1000")
        calls.clear()
        await a.on_processing_complete(self._event(a, DM, 1000), ProcessingOutcome.SUCCESS)
        assert [c["params"].get("emoji") for c in calls] == ["", "✅"]
