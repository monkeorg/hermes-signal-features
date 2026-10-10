"""Tests for outbound Signal quote-replies (platforms.signal.extra.quote_replies)."""
import pytest

from gateway.config import PlatformConfig

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
    monkeypatch.delenv("SIGNAL_QUOTE_REPLIES", raising=False)
    from gateway.platforms.signal import SignalAdapter
    config = PlatformConfig()
    config.enabled = True
    config.extra = {"http_url": "http://localhost:8080", "account": "+15559998888", **extra}
    return SignalAdapter(config)


def _capture_rpc(adapter, fail_quoted=False):
    calls = []

    async def rpc(method, params, rpc_id=None, **kwargs):
        calls.append({"method": method, "params": dict(params)})
        if method == "send" and fail_quoted and "quoteTimestamp" in params:
            return None
        return {"timestamp": 1700000000000 + len(calls)} if method == "send" else None

    adapter._rpc = rpc
    return calls


class TestQuotePolicyParsing:
    @pytest.mark.parametrize("raw,expected", [
        (None, "groups"), ("", "groups"), ("groups", "groups"), ("ALWAYS", "always"), ("never", "never"),
        (True, "always"), (False, "never"), ("off", "never"), ("bogus", "groups")])
    def test_parse(self, raw, expected):
        from gateway.platforms.signal import _parse_quote_policy
        assert _parse_quote_policy(raw) == expected

    def test_config_key_and_default(self, monkeypatch):
        assert _adapter(monkeypatch).quote_replies == "groups"
        assert _adapter(monkeypatch, quote_replies="never").quote_replies == "never"
        assert _adapter(monkeypatch, quote_replies=False).quote_replies == "never"


class TestQuoteParams:
    def test_group_always_quotes(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._remember_inbound(GROUP, 1000, "+15550001111", "hello chatbot")
        a._remember_inbound(GROUP, 2000, "+15550002222", "newer")
        assert a._quote_params(GROUP, "2000") == {
            "quoteTimestamp": 2000, "quoteAuthor": "+15550002222", "quoteMessage": "newer"}
        assert a._quote_params(GROUP, "1000")["quoteTimestamp"] == 1000

    def test_dm_quotes_only_older_messages(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._remember_inbound(DM, 1000, DM, "first")
        a._remember_inbound(DM, 2000, DM, "second")
        assert a._quote_params(DM, "2000") == {}
        assert a._quote_params(DM, "1000")["quoteAuthor"] == DM

    def test_always_and_never(self, monkeypatch):
        a = _adapter(monkeypatch, quote_replies="always")
        a._remember_inbound(DM, 1000, DM, "x")
        assert a._quote_params(DM, "1000")["quoteTimestamp"] == 1000
        n = _adapter(monkeypatch, quote_replies="never")
        n._remember_inbound(GROUP, 1000, DM, "x")
        assert n._quote_params(GROUP, "1000") == {}

    def test_unknown_or_malformed_anchor_is_ignored(self, monkeypatch):
        a = _adapter(monkeypatch)
        assert a._quote_params(GROUP, None) == {}
        assert a._quote_params(GROUP, "999") == {}
        assert a._quote_params(GROUP, "not-a-ts") == {}

    def test_preview_is_truncated_and_cache_bounded(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._max_inbound_by_ts = 3
        for ts in range(1, 6):
            a._remember_inbound(GROUP, ts, DM, "x" * 500)
        assert len(a._inbound_by_ts) == 3 and "1" not in a._inbound_by_ts
        assert len(a._quote_params(GROUP, "5")["quoteMessage"]) == 200


class TestQuoteSend:
    @pytest.mark.asyncio
    async def test_group_reply_carries_quote_on_first_chunk_only(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._remember_inbound(GROUP, 1000, "+15550001111", "question?")
        calls = _capture_rpc(a)
        a.MAX_MESSAGE_LENGTH = 20
        result = await a.send(GROUP, "word " * 20, reply_to="1000")
        sends = [c["params"] for c in calls if c["method"] == "send"]
        assert result.success and len(sends) > 1
        assert sends[0]["groupId"] == "abc123==" and sends[0]["quoteTimestamp"] == 1000
        assert sends[0]["quoteAuthor"] == "+15550001111" and sends[0]["quoteMessage"] == "question?"
        assert all("quoteTimestamp" not in p for p in sends[1:])

    @pytest.mark.asyncio
    async def test_reply_without_known_anchor_sends_plain(self, monkeypatch):
        a = _adapter(monkeypatch)
        calls = _capture_rpc(a)
        await a.send(GROUP, "hi", reply_to="42")
        assert "quoteTimestamp" not in calls[-1]["params"]

    @pytest.mark.asyncio
    async def test_failed_quoted_send_is_retried_without_quote(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._remember_inbound(GROUP, 1000, "+15550001111", "q")
        calls = _capture_rpc(a, fail_quoted=True)
        result = await a.send(GROUP, "answer", reply_to="1000")
        sends = [c["params"] for c in calls if c["method"] == "send"]
        assert result.success and len(sends) == 2
        assert "quoteTimestamp" in sends[0] and "quoteTimestamp" not in sends[1]
        assert "quoteAuthor" not in sends[1] and "quoteMessage" not in sends[1]


class TestEnvelopeAnchor:
    @pytest.mark.asyncio
    async def test_inbound_sets_message_id_and_remembers_author(self, monkeypatch):
        a = _adapter(monkeypatch)
        captured = {}

        async def fake_handle(event):
            captured["event"] = event

        a.handle_message = fake_handle
        await a._handle_envelope({"envelope": {
            "sourceNumber": "+15550001111", "sourceUuid": "uuid-1", "sourceName": "Tester", "timestamp": 1234,
            "dataMessage": {"message": "hello", "timestamp": 1234}}})
        ev = captured["event"]
        assert ev.message_id == "1234"
        assert a._inbound_by_ts["1234"] == ("+15550001111", "hello")
        assert a._last_inbound_ts_by_chat["+15550001111"] == "1234"
