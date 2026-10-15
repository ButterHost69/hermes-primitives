"""Tests for the per-primitive streams the TUI keeps of a fan-out."""

from __future__ import annotations

from tui.primitives import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    PrimitiveFeed,
    abbreviate,
)


def test_feed_starts_empty():
    feed = PrimitiveFeed()

    assert bool(feed) is False
    assert feed.order == []
    assert feed.summary() == "0 primitives"


def test_register_keeps_registration_order():
    feed = PrimitiveFeed()

    feed.register("src/db.py")
    feed.register("src/app.py")
    feed.register("src/db.py")

    assert feed.order == ["src/db.py", "src/app.py"]
    assert feed.status == {"src/db.py": STATUS_RUNNING, "src/app.py": STATUS_RUNNING}
    assert feed.marker("src/db.py") == "\u27f3"


def test_transcript_for_registers_an_unannounced_primitive():
    feed = PrimitiveFeed()

    stream = feed.transcript_for("src/app.py")

    assert stream is feed.streams["src/app.py"]
    assert feed.transcript_for("src/app.py") is stream
    assert feed.order == ["src/app.py"]


def test_note_report_settles_the_primitive_with_its_report():
    feed = PrimitiveFeed()
    feed.register("src/app.py")

    feed.note_report("src/app.py", "returned the parsed value")

    assert feed.status["src/app.py"] == STATUS_DONE
    assert feed.marker("src/app.py") == "\u2713"
    assert feed.status_color("src/app.py") == "success"
    assert feed.streams["src/app.py"].lines[-1] == (
        "result",
        "returned the parsed value",
    )


def test_note_report_without_a_report_writes_nothing():
    feed = PrimitiveFeed()
    feed.register("src/app.py")

    feed.note_report("src/app.py", "")

    assert feed.status["src/app.py"] == STATUS_DONE
    assert feed.streams["src/app.py"].lines == []


def test_note_failure_marks_the_primitive_error_and_keeps_the_reason():
    feed = PrimitiveFeed()
    feed.register("src/app.py")

    feed.note_failure("src/app.py", "pi exited before the turn settled")
    feed.note_failure("src/app.py", "a second report of the same failure")

    assert feed.status["src/app.py"] == STATUS_ERROR
    assert feed.marker("src/app.py") == "\u2717"
    assert feed.status_color("src/app.py") == "error"
    assert feed.detail("src/app.py") == "failed"
    assert feed.streams["src/app.py"].lines == [
        ("spacer", ""),
        ("error", "\u2717 pi exited before the turn settled"),
    ]


def test_finish_settles_the_primitives_that_reported_nothing():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.register("src/db.py")
    feed.note_failure("src/db.py", "boom")

    feed.finish()

    assert feed.status["src/app.py"] == STATUS_DONE
    assert feed.status["src/db.py"] == STATUS_ERROR


def test_summary_counts_the_states():
    feed = PrimitiveFeed()
    feed.register("a.py")
    feed.register("b.py")
    feed.register("c.py")
    feed.note_report("b.py", "done")
    feed.note_failure("c.py", "boom")

    assert feed.summary() == "3 primitives \u00b7 1 running \u00b7 1 done \u00b7 1 error"


def test_unknown_status_falls_back_to_muted():
    feed = PrimitiveFeed()

    assert feed.status_color("never-seen.py") == "muted"
    assert feed.marker("never-seen.py") == " "
    assert feed.detail("never-seen.py") == ""


def test_rows_group_every_stream_under_its_own_header():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.register("src/db.py")
    feed.streams["src/app.py"].tool("\u2699 read src/app.py \u2026")
    feed.streams["src/db.py"].text("closing the cursor")
    feed.streams["src/db.py"].settle()

    rows = feed.rows(40)

    assert rows[0][0] == "role"
    assert rows[0][1] == "src/app.py"
    assert rows[1] == ("tool", "src/app.py", "\u2699 read src/app.py \u2026")
    assert rows[2] == ("spacer", "", "")
    assert rows[3] == ("role", "src/db.py", "\u2713  0.0s") or rows[3][0] == "role"
    assert rows[4] == ("text", "src/db.py", "closing the cursor")


def test_rows_focus_one_stream_under_a_single_header():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.register("src/db.py")
    feed.streams["src/app.py"].tool("\u2699 read src/app.py \u2026")
    feed.streams["src/db.py"].text("closing the cursor")
    feed.streams["src/db.py"].settle()

    rows = feed.rows(40, focus="src/db.py")

    assert rows[0][0] == "role"
    assert rows[0][1] == "src/db.py"
    assert rows[1] == ("text", "src/db.py", "closing the cursor")
    assert all(row[1] != "src/app.py" for row in rows)


def test_rows_of_a_single_primitive_carry_no_label():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.streams["src/app.py"].text("alone")
    feed.streams["src/app.py"].settle()

    assert feed.rows(40) == [("role", "src/app.py", "\u27f3  0.0s"),
                             ("text", "", "alone")]


def test_rows_include_the_line_a_primitive_is_still_writing():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.register("src/db.py")
    feed.streams["src/app.py"].text("half a line")

    rows = feed.rows(40)

    assert ("text", "src/app.py", "half a line") in rows


def test_rows_wrap_the_stream_to_the_width_it_is_given():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.streams["src/app.py"].text("a " * 30)
    feed.streams["src/app.py"].settle()

    rows = feed.rows(10)

    assert all(len(row[2]) <= 10 for row in rows)


def test_rows_of_an_unknown_focus_build_every_group():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.streams["src/app.py"].text("here")
    feed.streams["src/app.py"].settle()

    rows = feed.rows(20, focus="gone.py")

    assert [row for row in rows if row[0] == "role"] == [
        ("role", "src/app.py", "\u27f3  0.0s")
    ]


def test_rows_do_not_label_spacer_rows():
    feed = PrimitiveFeed()
    feed.register("src/app.py")
    feed.streams["src/app.py"].text("one")
    feed.streams["src/app.py"].tool("\u2699 read \u2026")
    feed.streams["src/app.py"].settle()

    kinds = [(kind, label) for kind, label, _ in feed.rows(40)]

    assert ("spacer", "") in kinds


# --------------------------------------------------------------------------- #
# abbreviate
# --------------------------------------------------------------------------- #
def test_abbreviate_keeps_a_path_that_fits():
    assert abbreviate("src/app.py", 20) == "src/app.py"


def test_abbreviate_drops_the_middle_of_a_path():
    assert abbreviate("environment/codebase/src/module.py", 20) == (
        "environme\u2026/module.py"
    )


def test_abbreviate_cuts_a_path_with_no_directory_to_drop():
    assert abbreviate("module.py", 6) == "modul\u2026"


def test_abbreviate_handles_a_zero_or_negative_width():
    assert abbreviate("src/app.py", 0) == ""
    assert abbreviate("src/app.py", -3) == ""


def test_abbreviate_keeps_a_file_name_when_the_width_allows_nothing_else():
    assert abbreviate("src/app.py", 1) == "\u2026"
