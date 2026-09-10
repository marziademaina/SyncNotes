import json

import requests

import client.tui as tui


class _FakePromptWindow:

    def __init__(self, getstr_return: bytes = b"my-note.md"):
        self.timeouts: list[int] = []
        self._getstr_return = getstr_return

    def getmaxyx(self) -> tuple[int, int]:
        return 24, 80

    def addstr(self, *args, **kwargs) -> None:
        pass

    def refresh(self) -> None:
        pass

    def timeout(self, ms: int) -> None:
        self.timeouts.append(ms)

    def getstr(self, row: int, col: int, n: int) -> bytes:
        assert self.timeouts and self.timeouts[-1] == -1, "getstr() must run in blocking mode"
        return self._getstr_return


def test_prompt_name_restores_blocking_mode_around_getstr(monkeypatch):
    monkeypatch.setattr(tui.curses, "echo", lambda: None)
    monkeypatch.setattr(tui.curses, "noecho", lambda: None)
    monkeypatch.setattr(tui.curses, "curs_set", lambda visibility: None)
    win = _FakePromptWindow()

    name = tui._prompt_name(win)

    assert name == "my-note.md"
    assert win.timeouts == [-1, tui.REFRESH_TICK_MS]


def test_prompt_name_restores_the_tick_timeout_even_if_getstr_raises(monkeypatch):
    monkeypatch.setattr(tui.curses, "echo", lambda: None)
    monkeypatch.setattr(tui.curses, "noecho", lambda: None)
    monkeypatch.setattr(tui.curses, "curs_set", lambda visibility: None)

    class _BoomWindow(_FakePromptWindow):
        def getstr(self, row, col, n):
            super().getstr(row, col, n)
            raise tui.curses.error("screen glitch")

    win = _BoomWindow()

    try:
        tui._prompt_name(win)
    except tui.curses.error:
        pass

    assert win.timeouts == [-1, tui.REFRESH_TICK_MS]


def test_prompt_confirm_only_yes_confirms(monkeypatch):
    monkeypatch.setattr(tui.curses, "echo", lambda: None)
    monkeypatch.setattr(tui.curses, "noecho", lambda: None)
    monkeypatch.setattr(tui.curses, "curs_set", lambda visibility: None)

    assert tui._prompt_confirm(_FakePromptWindow(getstr_return=b"y"), "delete? ") is True
    assert tui._prompt_confirm(_FakePromptWindow(getstr_return=b"YES"), "delete? ") is True
    assert tui._prompt_confirm(_FakePromptWindow(getstr_return=b"n"), "delete? ") is False
    assert tui._prompt_confirm(_FakePromptWindow(getstr_return=b""), "delete? ") is False


def test_prompt_name_treats_a_truly_empty_input_as_cancel(monkeypatch):
    monkeypatch.setattr(tui.curses, "echo", lambda: None)
    monkeypatch.setattr(tui.curses, "noecho", lambda: None)
    monkeypatch.setattr(tui.curses, "curs_set", lambda visibility: None)
    win = _FakePromptWindow(getstr_return=b"")

    assert tui._prompt_name(win) is None


def test_prompt_name_returns_whitespace_only_input_as_is_rather_than_cancelling(monkeypatch):
    monkeypatch.setattr(tui.curses, "echo", lambda: None)
    monkeypatch.setattr(tui.curses, "noecho", lambda: None)
    monkeypatch.setattr(tui.curses, "curs_set", lambda visibility: None)
    win = _FakePromptWindow(getstr_return=b" ")

    name = tui._prompt_name(win)

    assert name == ""
    assert name is not None
    assert not tui._is_valid_note_name(name)


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


def test_empty_or_whitespace_only_note_names_are_rejected():
    assert not tui._is_valid_note_name("")
    assert not tui._is_valid_note_name(" ")
    assert not tui._is_valid_note_name("   ")
    assert not tui._is_valid_note_name("\t")


def test_note_names_with_leading_or_trailing_spaces_are_rejected():
    assert not tui._is_valid_note_name(" notes.md")
    assert not tui._is_valid_note_name("notes.md ")


def test_note_names_with_windows_reserved_characters_are_rejected():
    for bad in '<>:"|?*':
        assert not tui._is_valid_note_name(f"notes{bad}.md")


def test_note_names_with_control_characters_are_rejected():
    assert not tui._is_valid_note_name("notes\x00.md")
    assert not tui._is_valid_note_name("notes\n.md")


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


def test_live_notes_starts_with_the_given_list_and_no_pending_status():
    live = tui._LiveNotes([{"name": "a.md", "version": 1}])
    assert live.notes() == [{"name": "a.md", "version": 1}]
    assert live.take_status() == ""


def test_live_notes_take_status_returns_it_once_then_clears_it():
    live = tui._LiveNotes([])
    live.set_status("live updates paused: offline")
    assert live.take_status() == "live updates paused: offline"
    assert live.take_status() == ""


def test_live_notes_tracks_which_note_is_being_edited():
    live = tui._LiveNotes([])
    assert not live.is_editing("a.md")
    live.begin_editing("a.md")
    assert live.is_editing("a.md")
    assert not live.is_editing("b.md")
    live.end_editing()
    assert not live.is_editing("a.md")


def _note(version: int) -> dict:
    return {"name": "notes.md", "version": version, "updated_at": "2026-01-01T00:00:00Z"}


def test_sync_local_copies_skips_a_note_never_downloaded_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "download_file", lambda gateway, name: (_ for _ in ()).throw(AssertionError("should not download")))

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], tui._LiveNotes([]))


def test_sync_local_copies_skips_the_note_currently_being_edited(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("stale")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))
    monkeypatch.setattr(tui, "download_file", lambda gateway, name: (_ for _ in ()).throw(AssertionError("should not download")))

    live = tui._LiveNotes([])
    live.begin_editing("notes.md")

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], live)


def test_sync_local_copies_skips_a_note_already_at_the_latest_version(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("up to date")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 2}))
    monkeypatch.setattr(tui, "download_file", lambda gateway, name: (_ for _ in ()).throw(AssertionError("should not download")))

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], tui._LiveNotes([]))


def test_sync_local_copies_redownloads_a_stale_note_that_is_not_being_edited(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("stale")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))

    monkeypatch.setattr(
        tui, "download_file", lambda gateway, name: {"content": "fresh from server", "version": 2}
    )

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], tui._LiveNotes([]))

    assert local_path.read_text() == "fresh from server"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 2}


def test_sync_local_copies_is_best_effort_on_download_failure(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("stale")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))

    def boom(gateway, name):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(tui, "download_file", boom)

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], tui._LiveNotes([]))  # must not raise
    assert local_path.read_text() == "stale"


def _other_note(version: int) -> dict:
    return {"name": "keep.md", "version": version, "updated_at": "2026-01-01T00:00:00Z"}


def test_sync_local_copies_removes_the_local_copy_of_a_deleted_note(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("was synced")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))
    (tmp_path / "keep.md").write_text("still here")
    (tmp_path / ".keep.md.syncnotes.json").write_text(json.dumps({"name": "keep.md", "version": 2}))
    monkeypatch.setattr(tui, "download_file", lambda gateway, name: (_ for _ in ()).throw(AssertionError()))

    tui._sync_local_copies("http://gateway", tmp_path, [_other_note(2)], tui._LiveNotes([]))

    assert not local_path.exists()
    assert not (tmp_path / ".notes.md.syncnotes.json").exists()
    assert (tmp_path / "keep.md").exists()


def test_sync_local_copies_does_not_wipe_local_copies_on_an_empty_list(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("do not lose me")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))

    tui._sync_local_copies("http://gateway", tmp_path, [], tui._LiveNotes([]))

    assert local_path.exists()


def test_sync_local_copies_keeps_a_deleted_note_that_is_being_edited(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("open in the editor")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))

    live = tui._LiveNotes([])
    live.begin_editing("notes.md")

    tui._sync_local_copies("http://gateway", tmp_path, [_other_note(2)], live)

    assert local_path.exists()


def test_sync_local_copies_leaves_untracked_files_alone(tmp_path, monkeypatch):
    stray = tmp_path / "my-own-scratch.txt"  # no sidecar -> not a synced note
    stray.write_text("the user's own file")

    tui._sync_local_copies("http://gateway", tmp_path, [_other_note(2)], tui._LiveNotes([]))

    assert stray.exists()


def test_sync_local_copies_does_not_write_if_editing_starts_during_the_download(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("stale")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 1}))

    live = tui._LiveNotes([])

    def fake_download_file(gateway, name):
        live.begin_editing(name)
        return {"content": "fresh from server", "version": 2}

    monkeypatch.setattr(tui, "download_file", fake_download_file)

    tui._sync_local_copies("http://gateway", tmp_path, [_note(2)], live)

    assert local_path.read_text() == "stale"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 1}
