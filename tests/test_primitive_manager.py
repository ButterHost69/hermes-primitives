"""Tests for the PrimitiveManager registry."""

from __future__ import annotations

from primitives import Primitive, PrimitiveManager


class StubSession:
    """Minimal stand-in for the PiSession a primitive attaches."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_create_registers_and_returns():
    manager = PrimitiveManager()

    primitive = manager.create("src/app.py", {"src/db.py": "reads its settings"})

    assert primitive == Primitive("src/app.py", {"src/db.py": "reads its settings"})
    assert manager.primitive_map["src/app.py"] is primitive


def test_create_defaults_dep_files():
    manager = PrimitiveManager()
    assert manager.create("src/app.py").dep_files == {}


def test_search_returns_registered_or_none():
    manager = PrimitiveManager()
    manager.create("a.py")

    assert manager.search("a.py") is not None
    assert manager.search("missing.py") is None


def test_remove_reports_whether_one_was_registered():
    manager = PrimitiveManager()
    manager.create("a.py")

    assert manager.remove("a.py") is True
    assert manager.remove("a.py") is False
    assert manager.search("a.py") is None


def test_close_closes_every_primitive_session():
    manager = PrimitiveManager()
    first = manager.create("a.py")
    second = manager.create("b.py")
    first_session = StubSession()
    second_session = StubSession()
    first.attach_session(first_session)
    second.attach_session(second_session)

    manager.close()

    assert first_session.closed is True
    assert second_session.closed is True
    assert first.session is None
    assert second.session is None


def test_close_without_sessions_is_noop():
    manager = PrimitiveManager()
    manager.create("a.py")
    manager.close()  # no sessions attached: nothing to close
