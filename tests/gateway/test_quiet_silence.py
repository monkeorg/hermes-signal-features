"""gateway.warn_on_unexpected_silence: default keeps upstream behaviour, ``false`` makes bare silence markers quiet."""
import gateway.response_filters as rf


def _cfg(monkeypatch, cfg):
    import gateway.run as run
    monkeypatch.setattr(run, "_load_gateway_config", lambda *a, **k: cfg)


def test_default_keeps_upstream_behaviour(monkeypatch):
    _cfg(monkeypatch, {})
    assert rf.silence_allowed(None, None) is False
    assert rf.silence_allowed(None, True) is False
    assert rf.silence_allowed(None, False) is True
    assert rf.silence_allowed("internal_notification", None) is True


def test_explicit_true_keeps_upstream_behaviour(monkeypatch):
    _cfg(monkeypatch, {"gateway": {"warn_on_unexpected_silence": True}})
    assert rf.silence_allowed(None, None) is False
    assert rf.silence_allowed(None, True) is False


def test_false_makes_silence_quiet(monkeypatch):
    _cfg(monkeypatch, {"gateway": {"warn_on_unexpected_silence": False}})
    assert rf.silence_allowed(None, None) is True
    assert rf.silence_allowed(None, True) is True


def test_config_error_keeps_upstream_behaviour(monkeypatch):
    import gateway.run as run

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(run, "_load_gateway_config", boom)
    assert rf.silence_allowed(None, None) is False
