# Signal features for Hermes Agent

This repository is an **unofficial** fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
It is **not affiliated with or endorsed by Nous Research**. It adds six small, independent, opt-in improvements for the
Signal gateway (plus one general gateway option). Everything else is untouched upstream code, and the upstream MIT
`LICENSE` is unchanged.

> **No warranty.** This software is provided "as is", without warranty of any kind (see `LICENSE`). Test it on a
> throwaway Signal account/group before trusting it with anything important.

Developed and run against **Hermes v0.21.6+509** (upstream commit `f925b01791`). The branches in this repo are rebased
onto upstream `main` as of the time of publishing (`a62979dc36`); the Signal and gateway tests pass on both bases.
All features are **off or upstream-compatible by default**: with no new config keys set, behaviour is the same as
upstream, except where noted (`quote_replies` defaults to `groups`).

All keys below go under `platforms.signal.extra` in your Hermes `config.yaml` unless stated otherwise:

```yaml
platforms:
  signal:
    extra:
      require_mention: true            # existing upstream key
      mention_patterns: ["hey bot", "\\bbot\\b"]
      quote_replies: groups            # groups | always | never
      auto_react: true
      real_mentions: true
      mention_aliases:
        Alice: "+15551234567"
gateway:
  warn_on_unexpected_silence: true     # default; set to false to silence the warning
```

## The features

### 1. Group wake words (`mention_patterns`)  -  branch `feat/signal-mention-patterns`

With `require_mention: true`, Hermes normally only answers in a Signal group when it is @mentioned. With this feature a
group message also counts as addressed when

* it matches one of the case-insensitive regexes in `mention_patterns`, **or**
* it is a reply (quote) to one of the bot's own messages.

```yaml
platforms:
  signal:
    extra:
      require_mention: true
      mention_patterns:
        - "hey bot"
        - "\\bbot\\b"
```

Env fallback: `SIGNAL_MENTION_PATTERNS` (a single regex string). The config key wins when present.

### 2. Quote replies (`quote_replies`)  -  branch `feat/signal-quote-replies`

Replies are sent as real Signal quote-replies to the message that triggered them.

| value | behaviour |
| --- | --- |
| `groups` (default) | always quote in groups; in DMs only when answering an older message than the newest one in the chat |
| `always` | always quote |
| `never` | never quote (YAML `false` is accepted as `never`, `true` as `always`) |

Only the first chunk of a split reply carries the quote. If signal-cli rejects a quoted send (e.g. a stale target), the
message is resent without the quote, so a reply is never lost. Inbound messages now set `MessageEvent.message_id` to
the Signal timestamp, which is the anchor used for quoting. Env fallback: `SIGNAL_QUOTE_REPLIES`.

### 3. Reactions instead of text (`auto_react`)  -  branch `feat/signal-auto-react`

With `auto_react: true` the agent can answer with a reaction. A reply that starts with `[[react:<emoji>]]` is sent as an
emoji reaction on the triggering message; any text after the token is still sent as a normal message:

```text
[[react:👍]]                -> reaction only
[[react:✅]] Done, see PR.  -> reaction + message "Done, see PR."
```

The token is always stripped, so it can never leak into a chat. Tokens that are not an emoji (e.g. `[[react:thumbsup]]`)
are dropped. The change also adds `add_reaction()` so `send_message(action="react")` works on Signal, and the agent's
own reaction is not overwritten by Hermes' final ✅/❌ outcome reaction. To teach your model the syntax, mention it in
your system prompt / persona file. Env fallback: `SIGNAL_AUTO_REACT`.

### 4. Real @mentions (`real_mentions`, `mention_aliases`)  -  branch `feat/signal-real-mentions`

With `real_mentions: true`, `@Name` in an outgoing **group** message becomes a real Signal mention (a notification for
that person) via signal-cli's `mention` parameter. Names are resolved, in order, via

1. `mention_aliases` (`{name: phone-number-or-uuid}`, case-insensitive),
2. the group's members matched against first names from your signal-cli contact book,
3. profile names of people the bot has seen writing.

```yaml
platforms:
  signal:
    extra:
      real_mentions: true
      mention_aliases:
        Alice: "+15551234567"
        Bob: "11111111-2222-4333-8444-555555555555"
```

Safety rules: unknown or ambiguous names are left as plain text (unless the person being replied to disambiguates),
people who are not in the group and the bot itself are never mentioned, code spans/blocks and e-mail addresses are
skipped, offsets are computed in UTF-16 units, and if signal-cli rejects a mention the message is resent as plain
`@Name` text. Env fallback: `SIGNAL_REAL_MENTIONS`. Depends on features 2 and 3 (shared helper code).

### 5. Emoji-only replies become reactions  -  branch `feat/signal-react-emoji-only`

A safety net for feature 3: when `auto_react` is on and the model replies with nothing but one short emoji (e.g. `👍`)
to a known message, it is sent as a reaction on that message instead of a chat message. If the reaction cannot be
delivered, it falls back to plain text. Depends on feature 3.

### 6. Optional quiet silence marker (`gateway.warn_on_unexpected_silence`)  -  branch `feat/quiet-silence`

Upstream warns "model returned only a silence marker" when the model answers a human turn with a bare `NO_REPLY` /
`[SILENT]` and the platform did not say that no reply was expected. In places where the platform cannot say so (for
example Signal groups) a deliberate silence therefore produces a spurious warning.

```yaml
gateway:
  warn_on_unexpected_silence: false   # bare silence markers stay silent on every turn
```

**The default is `true`, i.e. identical to upstream behaviour**, so no upstream test or behaviour changes unless you
opt in. Config errors fall back to the default. (Some downstream users run with `false`.) This is a general gateway
change, not specific to Signal; it touches only `gateway/response_filters.py`.

## Applying individual features

Each feature has its own branch, stacked in dependency order (each branch contains the previous ones):

```text
upstream main
 └─ feat/signal-mention-patterns
     └─ feat/signal-quote-replies
         └─ feat/signal-auto-react
             └─ feat/signal-real-mentions
                 └─ feat/signal-react-emoji-only
                     └─ feat/quiet-silence
```

`signal-features` (the default branch) contains all six commits plus this documentation.

Dependencies: 1 is standalone; 2 is standalone in behaviour but touches neighbouring lines of 1, so apply in order;
3 needs 2; 4 needs 2 and 3; 5 needs 3; 6 is fully standalone (it only touches `gateway/response_filters.py`).

### With git

```bash
git remote add signal-features https://github.com/monkeorg/hermes-signal-features.git
git fetch signal-features
git log --oneline upstream/main..signal-features/signal-features   # list the commits
git cherry-pick <commit>                                           # pick what you want, in order
```

### With patch files

The same commits are available as `git format-patch` files in [`docs/patches/`](patches/):

```bash
git am docs/patches/0001-*.patch            # apply one (or several, in order)
# or, without creating commits:
git apply --3way docs/patches/0006-*.patch
```

## Tests

Each Signal feature ships with tests under `tests/gateway/` (`test_signal_quote_replies.py`,
`test_signal_auto_react.py`, `test_signal_real_mentions.py`, `test_signal_react_emoji_only.py`; feature 1 has no dedicated test file;
feature 6 adds `test_quiet_silence.py`):

```bash
python -m pytest tests/gateway -k "signal or silence" -q
```

## Caveats

* Tested against signal-cli's JSON-RPC mode as used by Hermes at v0.21.6+509; behaviour with other signal-cli versions
  is unknown.
* Real mentions and quotes depend on signal-cli accepting the parameters; failures fall back to plain text/no quote.
* The wake-word heuristics are plain regexes: choose patterns that do not match ordinary conversation.
* Not an official Hermes release. For support with upstream Hermes, see the
  [upstream project](https://github.com/NousResearch/hermes-agent).
