"""Tests for real Signal @mentions in outgoing group text (platforms.signal.extra.real_mentions)."""
import asyncio

import pytest

from gateway.config import PlatformConfig

GROUP = "group:abc123=="
GID = "abc123=="
DM = "+15550001111"
BOT = "+15559998888"
CHARLIE, CHARLIE_UUID = "+15550001111", "11111111-1111-4111-8111-111111111111"
ALICE, ALICE_UUID = "+15550002222", "22222222-2222-4222-8222-222222222222"
OUTSIDER_UUID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _reset_signal_scheduler():
    from gateway.platforms.signal_rate_limit import _reset_scheduler
    _reset_scheduler()
    yield
    _reset_scheduler()


def _adapter(monkeypatch, **extra):
    monkeypatch.setenv("SIGNAL_GROUP_ALLOWED_USERS", "*")
    monkeypatch.delenv("SIGNAL_REAL_MENTIONS", raising=False)
    from gateway.platforms.signal import SignalAdapter
    config = PlatformConfig()
    config.enabled = True
    config.extra = {"http_url": "http://localhost:8080", "account": BOT, "real_mentions": True, **extra}
    return SignalAdapter(config)


def _rpc_stub(adapter, members=None, contacts=None, reject_mentions=False, reject_quote=False):
    calls = []
    members = [{"number": CHARLIE, "uuid": CHARLIE_UUID}, {"number": ALICE, "uuid": ALICE_UUID},
               {"number": BOT, "uuid": "99999999-9999-4999-8999-999999999999"}] if members is None else members

    async def rpc(method, params, rpc_id=None, **kwargs):
        calls.append({"method": method, "params": dict(params)})
        if method == "listGroups":
            return None if members is False else [{"id": GID, "name": "g", "members": members}]
        if method == "listContacts":
            return contacts or []
        if method == "send":
            if reject_mentions and "mention" in params or reject_quote and "quoteTimestamp" in params:
                return None
            return {"timestamp": 1700000000000 + len(calls)}
        return None

    adapter._rpc = rpc
    return calls


def _sends(calls):
    return [c["params"] for c in calls if c["method"] == "send"]


def _send(adapter, text, chat=GROUP, reply_to=None):
    return asyncio.run(adapter.send(chat, text, reply_to=reply_to))


class TestConfig:
    def test_default_off_and_keys(self, monkeypatch):
        from gateway.platforms.signal import SignalAdapter
        a = _adapter(monkeypatch, real_mentions=None)
        assert a.real_mentions is False
        assert _adapter(monkeypatch).real_mentions is True
        b = _adapter(monkeypatch, mention_aliases={"@Charlie": CHARLIE, "": "x", "Bad": None})
        assert b._mention_aliases == {"charlie": CHARLIE}
        assert isinstance(a, SignalAdapter)

    def test_env_fallback(self, monkeypatch):
        a = _adapter(monkeypatch, real_mentions=None)
        monkeypatch.setenv("SIGNAL_REAL_MENTIONS", "true")
        from gateway.platforms.signal import SignalAdapter
        cfg = PlatformConfig()
        cfg.extra = {"http_url": "http://x", "account": BOT}
        assert SignalAdapter(cfg).real_mentions is True


class TestPayload:
    def test_alias_becomes_mention_param(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(a)
        res = _send(a, "Hello @Charlie, done.")
        assert res.success
        (params,) = _sends(calls)
        # text untouched; mention covers "@Charlie" (start 6, length 8) and uses the member's UUID
        assert params["message"] == "Hello @Charlie, done."
        assert params["mention"] == [f"6:8:{CHARLIE_UUID}"]
        assert params["groupId"] == GID

    def test_no_mentions_in_dm_or_when_disabled(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(a)
        _send(a, "Hi @Charlie", chat=DM)
        assert "mention" not in _sends(calls)[0]
        assert not [c for c in calls if c["method"] == "listGroups"]
        off = _adapter(monkeypatch, real_mentions=False, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(off)
        _send(off, "Hi @Charlie")
        assert "mention" not in _sends(calls)[0]

    def test_utf16_offsets_with_emoji_and_two_mentions(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE, "alice": ALICE_UUID})
        calls = _rpc_stub(a)
        _send(a, "😀 @Charlie and @Alice!")
        (params,) = _sends(calls)
        # the emoji is 2 UTF-16 units: "@Charlie" starts at 3, "@Alice" at 3+8+5=16
        assert params["mention"] == [f"3:8:{CHARLIE_UUID}", f"16:6:{ALICE_UUID}"]

    def test_unknown_name_email_and_code_are_left_alone(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(a)
        _send(a, "mail me@example.com, @Nobody and `@Charlie` stay plain")
        assert "mention" not in _sends(calls)[0]
        calls = _rpc_stub(a)
        a._group_members.clear()
        _send(a, "```\n@Charlie\n```")
        assert "mention" not in _sends(calls)[0]

    def test_non_member_is_not_mentioned(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"mallory": OUTSIDER_UUID, "charlie": CHARLIE})
        calls = _rpc_stub(a)
        _send(a, "@Mallory and @Charlie")
        assert _sends(calls)[0]["mention"] == [f"13:8:{CHARLIE_UUID}"]

    def test_bot_itself_is_never_mentioned(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"chatbot": BOT})
        calls = _rpc_stub(a)
        _send(a, "I am @Chatbot")
        assert "mention" not in _sends(calls)[0]

    def test_member_list_unavailable_still_sends_alias_mention(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE_UUID})
        calls = _rpc_stub(a, members=False)
        _send(a, "@Charlie")
        assert _sends(calls)[0]["mention"] == [f"0:8:{CHARLIE_UUID}"]


class TestNameResolution:
    def test_contact_and_seen_sender_names(self, monkeypatch):
        a = _adapter(monkeypatch)
        contacts = [{"number": ALICE, "uuid": ALICE_UUID, "givenName": "Alice", "name": "Alice Example",
                     "profile": {"givenName": "Alice"}}]
        calls = _rpc_stub(a, contacts=contacts)
        a._learn_mention_name("Charlie M.", CHARLIE, CHARLIE_UUID)  # as inbound envelopes do
        _send(a, "@alice, @charlie")
        assert _sends(calls)[0]["mention"] == [f"0:6:{ALICE_UUID}", f"8:8:{CHARLIE_UUID}"]

    def test_alias_beats_learned_name(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        a._learn_mention_name("Charlie", ALICE, ALICE_UUID)
        calls = _rpc_stub(a)
        _send(a, "@Charlie")
        assert _sends(calls)[0]["mention"] == [f"0:8:{CHARLIE_UUID}"]

    def test_ambiguous_name_is_skipped_unless_reply_target_decides(self, monkeypatch):
        a = _adapter(monkeypatch)
        a._learn_mention_name("Alex", CHARLIE, CHARLIE_UUID)
        a._learn_mention_name("Alex", ALICE, ALICE_UUID)
        calls = _rpc_stub(a)
        _send(a, "@Alex")
        assert "mention" not in _sends(calls)[0]
        a._remember_inbound(GROUP, 1000, ALICE, "hi")
        _send(a, "@Alex", reply_to="1000")
        assert _sends(calls)[1]["mention"] == [f"0:5:{ALICE_UUID}"]

    def test_sender_of_inbound_envelope_is_learned(self, monkeypatch):
        a = _adapter(monkeypatch)
        asyncio.run(a._handle_envelope({"envelope": {
            "sourceNumber": ALICE, "sourceUuid": ALICE_UUID, "sourceName": "Alice K", "timestamp": 1,
            "dataMessage": {"message": "x", "groupInfo": {"groupId": "other=="}}}}))
        assert a._mention_names["alice"] == {ALICE, ALICE_UUID}


class TestChunksAndFallbacks:
    def test_mentions_are_chunk_local(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        a.MAX_MESSAGE_LENGTH = 40
        calls = _rpc_stub(a)
        _send(a, "x" * 45 + " @Charlie ok")
        sends = _sends(calls)
        assert len(sends) == 2
        assert "mention" not in sends[0]
        start = sends[1]["message"].index("@Charlie")
        assert sends[1]["mention"] == [f"{start}:8:{CHARLIE_UUID}"]

    def test_rejected_mention_falls_back_to_plain_text(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(a, reject_mentions=True)
        res = _send(a, "@Charlie hi")
        assert res.success
        first, second = _sends(calls)
        assert "mention" in first and "mention" not in second and second["message"] == "@Charlie hi"

    def test_quote_failure_keeps_mention(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        a._remember_inbound(GROUP, 1000, ALICE, "hi")
        calls = _rpc_stub(a, reject_quote=True)
        res = _send(a, "@Charlie hi", reply_to="1000")
        assert res.success
        first, second = _sends(calls)
        assert "quoteTimestamp" in first and "quoteTimestamp" not in second
        assert second["mention"] == [f"0:8:{CHARLIE_UUID}"]

    def test_quote_and_text_style_coexist_with_mention(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        a._remember_inbound(GROUP, 1000, ALICE, "hi")
        calls = _rpc_stub(a)
        _send(a, "**@Charlie** done", reply_to="1000")
        (params,) = _sends(calls)
        assert params["quoteTimestamp"] == 1000
        assert params["textStyle"] == "0:8:BOLD" and params["mention"] == [f"0:8:{CHARLIE_UUID}"]

    def test_member_list_is_cached(self, monkeypatch):
        a = _adapter(monkeypatch, mention_aliases={"charlie": CHARLIE})
        calls = _rpc_stub(a)
        _send(a, "@Charlie")
        _send(a, "@Charlie again")
        assert len([c for c in calls if c["method"] == "listGroups"]) == 1
        assert len([c for c in calls if c["method"] == "listContacts"]) == 1
