"""Tests for the curses-free logic in tui.py: name validation, the
edit-window size guard and Textbox output normalization.
"""
import client.tui as tui


def test_default_user_falls_back_to_os_username(monkeypatch):
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "marzia")
    assert tui._default_user() == "marzia"


def test_default_user_falls_back_to_a_placeholder_when_unavailable(monkeypatch):
    def boom():
        raise OSError("no username available")

    monkeypatch.setattr(tui.getpass, "getuser", boom)
    assert tui._default_user() == "you"


def test_valid_note_names_are_accepted():
    assert tui._is_valid_note_name("notes.md")
    assert tui._is_valid_note_name("my-todo-list.txt")


def test_note_names_with_path_separators_are_rejected():
    assert not tui._is_valid_note_name("../secrets.md")
    assert not tui._is_valid_note_name("a/b.md")
    assert not tui._is_valid_note_name("a\\b.md")


def test_note_names_starting_with_a_dot_are_rejected():
    assert not tui._is_valid_note_name(".hidden.md")


def test_text_that_fits_the_window_is_not_too_large():
    assert not tui._too_large_for_window("line1\nline2", height=10, width=40)


def test_text_with_too_many_lines_is_too_large():
    text = "\n".join(f"line{i}" for i in range(20))
    assert tui._too_large_for_window(text, height=10, width=40)


def test_text_with_a_line_too_wide_is_too_large():
    assert tui._too_large_for_window("x" * 100, height=10, width=40)


def test_normalize_strips_trailing_spaces_textbox_adds():
    assert tui._normalize_edited_text("hello world \n") == "hello world\n"


def test_normalize_appends_a_single_trailing_newline():
    assert tui._normalize_edited_text("line1\nline2") == "line1\nline2\n"


def test_normalize_of_empty_text_stays_empty():
    assert tui._normalize_edited_text("") == ""


def test_normalize_is_idempotent_on_a_no_op_round_trip():
    once = tui._normalize_edited_text("hello \nworld  \n")
    twice = tui._normalize_edited_text(once)
    assert once == twice == "hello\nworld\n"
