"""EmailSender tests - the Gmail threading logic.

No real SMTP connection is made anywhere here: _build_message is pure (no
I/O), and send_daily's persistence is exercised through a real StateStore
pointed at tmp_path, exactly like the rest of the state-store-backed tests.
"""

from __future__ import annotations

from echoes.deliver.email_sender import EmailSender
from echoes.models import EmailThread
from echoes.playlist import StateStore


def test_first_email_has_no_reply_headers():
    msg = EmailSender._build_message(
        from_address="a@example.com",
        to="b@example.com",
        subject="Echoes — Daily Quotes",
        body="1. Quote one.",
        message_id="<first@example.com>",
        thread=EmailThread(),
    )

    assert "In-Reply-To" not in msg
    assert "References" not in msg
    assert msg["Message-ID"] == "<first@example.com>"
    assert msg.get_content().strip() == "1. Quote one."


def test_later_email_replies_to_the_most_recent_and_references_the_whole_chain():
    thread = EmailThread(message_ids=["<first@example.com>", "<second@example.com>"])

    msg = EmailSender._build_message(
        from_address="a@example.com",
        to="b@example.com",
        subject="Echoes — Daily Quotes",
        body="1. Quote three.",
        message_id="<third@example.com>",
        thread=thread,
    )

    assert msg["In-Reply-To"] == "<second@example.com>"
    assert msg["References"] == "<first@example.com> <second@example.com>"


def test_subject_never_changes_between_sends():
    """A changing subject is a common way Gmail threading silently breaks."""
    first = EmailSender._build_message(
        from_address="a@example.com", to="b@example.com", subject="Echoes — Daily Quotes",
        body="day one", message_id="<1@example.com>", thread=EmailThread(),
    )
    second = EmailSender._build_message(
        from_address="a@example.com", to="b@example.com", subject="Echoes — Daily Quotes",
        body="day two", message_id="<2@example.com>",
        thread=EmailThread(message_ids=["<1@example.com>"]),
    )
    assert first["Subject"] == second["Subject"]


def test_send_daily_persists_the_message_id_for_the_next_run(settings, today):
    settings = settings  # from conftest - dry_run=False, tmp_path-backed state_dir
    store = StateStore(settings.state_dir)
    sender = EmailSender(settings.email, state_store=store, book_separator=settings.book_separator)

    from echoes.models import DailyBundle, PoolName, Quote

    bundle = DailyBundle(day=today, quotes=[Quote("b1", "A quote.", PoolName.BOOKS, "A Book")])

    class _StubDeliver:
        """Swap out real SMTP for a no-op that just records the message."""

        def __init__(self):
            self.sent = []

        def __call__(self, msg):
            self.sent.append(msg)

    stub = _StubDeliver()
    sender._deliver = stub  # type: ignore[method-assign]

    sender.send_daily(bundle)
    thread_after_first = store.load_email_thread()
    assert len(thread_after_first.message_ids) == 1
    assert "In-Reply-To" not in stub.sent[0]

    sender.send_daily(bundle)
    thread_after_second = store.load_email_thread()
    assert len(thread_after_second.message_ids) == 2
    assert stub.sent[1]["In-Reply-To"] == thread_after_first.message_ids[0]


def test_body_has_the_greeting_spaced_quotes_and_signoff(today):
    from echoes.deliver.email_sender import GREETING, INTRO, SIGNOFF, _build_body
    from echoes.models import DailyBundle, PoolName, Quote

    bundle = DailyBundle(
        day=today,
        quotes=[
            Quote("b1", "First quote.", PoolName.BOOKS, "Book One"),
            Quote("b2", "Second quote.", PoolName.BOOKS, "Book Two"),
        ],
    )

    body = _build_body(bundle, book_separator=" - ")

    assert body == (
        f"{GREETING}\n\n{INTRO}\n"
        "1. First quote. - Book One\n\n2. Second quote. - Book Two"
        f"\n\n{SIGNOFF}"
    )


def test_send_daily_uses_the_built_body(settings, today):
    """The wired-up sender actually sends _build_body's output, not something else."""
    from echoes.deliver.email_sender import _build_body
    from echoes.models import DailyBundle, PoolName, Quote

    store = StateStore(settings.state_dir)
    sender = EmailSender(settings.email, state_store=store, book_separator=settings.book_separator)
    bundle = DailyBundle(day=today, quotes=[Quote("b1", "A quote.", PoolName.BOOKS, "A Book")])

    sent: list = []
    sender._deliver = sent.append  # type: ignore[method-assign]
    sender.send_daily(bundle)

    expected = _build_body(bundle, book_separator=settings.book_separator)
    assert sent[0].get_content().strip() == expected
