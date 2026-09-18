"""DETERMINISTIC EVAL — a dead provider degrades honestly instead of crashing.

Live bug, 2026-09-18: a DeepSeek balance hit zero, the 402 travelled up from
`run_loop` through `respond()` and out of the CLI as a twenty-line traceback,
killing the session. The retrieval gate had already handled the SAME error
correctly one line earlier ("gate failed open"), which is what made the
asymmetry obvious: the graph path and the streaming path both fall open, and
only the plain model call escaped.

What's pinned here:
  1. `respond()` returns an honest reply instead of raising, whatever the
     provider throws.
  2. The reply names the brain and what it said — "which provider is this,
     and is it me or them?" are the two questions a 402 has to answer.
  3. A failed turn is NOT recorded: no chat_log row, no history entry, nothing
     for consolidation to turn into a fact.
  4. A good turn after a bad one still works, and the bad one left no trace in
     working memory.
  5. The CLI's quit words are slash commands only, so no bare word a user
     might genuinely want to say is swallowed by the gateway.

Hermetic: the "provider" is a client that raises. No model, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evals.helpers import make_waku
from waku.app import provider_error_reply
from waku.config import Settings
from waku.gateway.cli import QUIT_WORDS


class Insufficient(Exception):
    """Stands in for openai.APIStatusError — respond() must not know the type."""


class DeadClient:
    """Every call raises, exactly as an out-of-credit provider does."""

    def __init__(self, exc=None):
        self._exc = exc or Insufficient(
            "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}")
        self.messages = SimpleNamespace(create=self._raise, stream=self._raise)

    def _raise(self, **kwargs):
        raise self._exc


class FlakyClient(DeadClient):
    """Dead until `healthy` is set — a balance topped up mid-session.

    A flag, not a call count: the retrieval gate calls the model too, so
    "fail the first call" would fail the GATE (which fails open by design)
    and let the loop's call through, testing nothing."""

    def __init__(self, good):
        super().__init__()
        self._good = good
        self.healthy = False
        self.messages = SimpleNamespace(create=self._create, stream=self._raise)

    def _create(self, **kwargs):
        if not self.healthy:
            raise self._exc
        return self._good


def _reply(content: str):
    """The minimal shape run_loop reads off a response."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=content)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


def test_a_dead_provider_returns_text_instead_of_raising(tmp_path):
    app = make_waku(tmp_path / "home", client=DeadClient())
    result = app.respond("hello")          # must not raise
    assert result.reply
    assert "did not happen" in result.reply


def test_the_reply_names_the_brain_and_what_it_said(tmp_path):
    app = make_waku(tmp_path / "home", client=DeadClient(),
                    provider="deepseek", model="deepseek-v4-pro")
    reply = app.respond("hello").reply
    assert "deepseek/deepseek-v4-pro" in reply
    assert "Insufficient" in reply          # the provider's own words survive
    assert "Insufficient" in reply and "Traceback" not in reply


def test_a_failed_turn_is_not_recorded_anywhere(tmp_path):
    app = make_waku(tmp_path / "home", client=DeadClient())
    app.respond("remember that I like oat milk")
    rows = app.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0]
    assert rows == 0, "a turn that never reached the model must not enter the chat log"
    assert app.session.history == [], "nor working memory"


def test_recovery_leaves_no_trace_of_the_failure(tmp_path):
    client = FlakyClient(_reply("hi there"))
    app = make_waku(tmp_path / "home", client=client)
    first = app.respond("one")
    assert "did not happen" in first.reply
    client.healthy = True
    second = app.respond("two")
    assert second.reply == "hi there"
    # only the good turn is in working memory, and the error text is nowhere
    assert len(app.session.history) == 2
    assert all("did not happen" not in m["content"] for m in app.session.history)


def test_the_error_reply_is_bounded(tmp_path):
    """A provider that returns an HTML error page must not become the reply."""
    text = provider_error_reply(Settings(home=tmp_path, provider="x", model="y"),
                                Insufficient("z" * 5000))
    assert len(text) < 500 and text.endswith("…")


def test_quit_words_are_slash_commands_only():
    """`/memory` carries a slash, so leaving does too. A bare word in the prompt
    box is a message to the model, and the gateway may not swallow one."""
    assert QUIT_WORDS == {"/quit", "/exit", "/q"}


@pytest.mark.parametrize("typed", ["/quit", "/QUIT", "/Q", "/Exit"])
def test_quit_words_are_case_insensitive(typed):
    assert typed.lower() in QUIT_WORDS


@pytest.mark.parametrize("typed", ["exit", "quit", "q", "please quit"])
def test_bare_words_stay_messages(typed):
    assert typed.lower() not in QUIT_WORDS
