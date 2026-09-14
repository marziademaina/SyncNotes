# Full-screen terminal UI for SyncNotes

import curses
import curses.ascii
import curses.textpad
import getpass
import threading
from pathlib import Path

import requests

from client.api import download_file
from client.notes import (
    SyncNotesError,
    create_note,
    delete_note,
    describe_create_outcome,
    describe_delete_outcome,
    describe_upload_outcome,
    fetch_notes,
    format_updated_at,
    open_note,
    save_note,
    watch_notes,
)
from client.sync_state import clear_state, read_base_version, write_state

DEFAULT_GATEWAY = "http://localhost:8080"
DEFAULT_SYNC_DIR = Path.home() / "SyncNotes"

MIN_ROWS = 10
MIN_COLS = 40

REFRESH_TICK_MS = 300

_WATCH_MIN_BACKOFF_SECONDS = 2.0
_WATCH_MAX_BACKOFF_SECONDS = 30.0


def _default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "you"


def main(gateway: str = DEFAULT_GATEWAY, sync_dir: Path | str = DEFAULT_SYNC_DIR, user: str | None = None) -> None:
    sync_dir = Path(sync_dir)
    sync_dir.mkdir(parents=True, exist_ok=True)
    user = user or _default_user()
    curses.wrapper(_run, gateway, sync_dir, user)


def _run(stdscr, gateway: str, sync_dir: Path, user: str) -> None:
    curses.curs_set(0)
    selected = 0
    offset = 0
    notes, status = _reload(gateway)
    live = _LiveNotes(notes)

    stop = threading.Event()
    watcher = threading.Thread(target=_watch_loop, args=(gateway, sync_dir, live, stop), daemon=True)
    watcher.start()

    stdscr.timeout(REFRESH_TICK_MS)

    while True:
        max_y, max_x = stdscr.getmaxyx()
        if max_y < MIN_ROWS or max_x < MIN_COLS:
            try:
                stdscr.erase()
                _safe_addstr(stdscr, 0, 0, "Terminal window is too small for SyncNotes.")
                _safe_addstr(stdscr, 1, 0, "Resize it, or press 'q' to quit.")
                stdscr.refresh()
                if stdscr.getch() == ord("q"):
                    stop.set()
                    return
            except curses.error:
                pass
            continue

        notes = live.notes()

        if selected >= len(notes):
            selected = max(len(notes) - 1, 0)
        bg_status = live.take_status()
        if bg_status:
            status = bg_status

        visible_rows = max(max_y - 7, 0)
        offset = _scroll_offset(selected, offset, visible_rows, len(notes))

        try:
            _draw_menu(stdscr, gateway, user, notes, selected, offset, status)
            key = stdscr.getch()
        except curses.error:
            continue
        if key == -1:
            continue 
        status = ""

        if key in (curses.KEY_UP, ord("k")) and notes:
            selected = (selected - 1) % len(notes)
        elif key in (curses.KEY_DOWN, ord("j")) and notes:
            selected = (selected + 1) % len(notes)
        elif key in (curses.KEY_ENTER, 10, 13) and notes:
            try:
                status = _edit_note_flow(stdscr, gateway, sync_dir, user, notes[selected]["name"], live)
            except curses.error:
                status = "screen glitch while editing - your last edit may not have saved, please retry"
            notes, reload_status = _reload(gateway)
            live.set_notes(notes)
            status = reload_status or status
            selected = min(selected, max(len(notes) - 1, 0))
        elif key == ord("n"):
            try:
                name = _prompt_name(stdscr)
            except curses.error:
                name, status = None, "screen glitch - try 'n' again"
            if name is not None:
                reason = _invalid_note_name_reason(name)
                if reason:
                    status = f"not a valid note name: {reason}"
                elif any(note["name"] == name for note in notes):
                    status = f"'{name}' already exists - open it instead"
                else:
                    try:
                        result = create_note(gateway, sync_dir, name)
                        status = describe_create_outcome(name, result)
                    except SyncNotesError as exc:
                        status = str(exc)
            notes, reload_status = _reload(gateway)
            live.set_notes(notes)
            status = reload_status or status
        elif key == ord("d") and notes:
            name = notes[selected]["name"]
            try:
                confirmed = _prompt_confirm(stdscr, f"Delete '{name}'? This removes it for everyone (y/n): ")
            except curses.error:
                confirmed, status = False, "screen glitch - try 'd' again"
            if confirmed:
                try:
                    delete_note(gateway, sync_dir, name)
                    status = describe_delete_outcome(name)
                except SyncNotesError as exc:
                    status = str(exc)
                notes, reload_status = _reload(gateway)
                live.set_notes(notes)
                status = reload_status or status
                selected = min(selected, max(len(notes) - 1, 0))
            else:
                status = status or "delete cancelled"
        elif key == ord("r"):
            notes, status = _reload(gateway)
            live.set_notes(notes)
        elif key in (ord("q"), curses.ascii.ESC):
            stop.set()
            return


def _reload(gateway: str) -> tuple[list[dict], str]:
    try:
        return fetch_notes(gateway), ""
    except SyncNotesError as exc:
        return [], str(exc)


class _LiveNotes:

    def __init__(self, notes: list[dict]) -> None:
        self._lock = threading.Lock()
        self._notes = notes
        self._status = ""
        self._editing: str | None = None

    def notes(self) -> list[dict]:
        with self._lock:
            return self._notes

    def set_notes(self, notes: list[dict]) -> None:
        with self._lock:
            self._notes = notes

    def take_status(self) -> str:
        with self._lock:
            status, self._status = self._status, ""
            return status

    def set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def begin_editing(self, name: str) -> None:
        with self._lock:
            self._editing = name

    def end_editing(self) -> None:
        with self._lock:
            self._editing = None

    def is_editing(self, name: str) -> bool:
        with self._lock:
            return self._editing == name

    def write_if_not_editing(self, name: str, write) -> bool:
        with self._lock:
            if self._editing == name:
                return False
            write()
            return True


def _sync_local_copies(gateway: str, sync_dir: Path, notes: list[dict], live: _LiveNotes) -> None:
    for note in notes:
        name = note["name"]
        local_path = sync_dir / name
        if not local_path.exists() or live.is_editing(name):
            continue  
        if read_base_version(str(local_path), name) == note["version"]:
            continue
        try:
            result = download_file(gateway, name)

            def write(result=result, local_path=local_path, name=name) -> None:
                local_path.write_text(result["content"])
                write_state(str(local_path), name, result["version"])

            live.write_if_not_editing(name, write)
        except (requests.RequestException, OSError):
            pass 
    if notes:
        _remove_deleted_local_copies(sync_dir, {note["name"] for note in notes}, live)


def _remove_deleted_local_copies(sync_dir: Path, live_names: set[str], live: _LiveNotes) -> None:
    try:
        entries = list(sync_dir.iterdir())
    except OSError:
        return
    for path in entries:
        name = path.name
        if name.startswith(".") or not path.is_file() or name in live_names:
            continue
        if read_base_version(str(path), name) is None or live.is_editing(name):
            continue

        def remove(path=path, name=name) -> None:
            path.unlink(missing_ok=True)
            clear_state(str(path))

        try:
            live.write_if_not_editing(name, remove)
        except OSError:
            pass 


def _watch_loop(gateway: str, sync_dir: Path, live: _LiveNotes, stop: threading.Event) -> None:
    backoff = _WATCH_MIN_BACKOFF_SECONDS
    while not stop.is_set():
        try:
            for notes in watch_notes(gateway):
                if stop.is_set():
                    return
                backoff = _WATCH_MIN_BACKOFF_SECONDS
                _sync_local_copies(gateway, sync_dir, notes, live)
                live.set_notes(notes)
        except SyncNotesError as exc:
            live.set_status(f"live updates paused: {exc}")
        if stop.is_set():
            return
        stop.wait(backoff)
        backoff = min(backoff * 2, _WATCH_MAX_BACKOFF_SECONDS)


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    max_y, max_x = win.getmaxyx()
    if not (0 <= y < max_y and 0 <= x < max_x):
        return
    try:
        win.addstr(y, x, text[: max_x - x], attr)
    except curses.error:
        pass


def _draw_user_label(stdscr, user: str) -> None:
    _, max_x = stdscr.getmaxyx()
    label = f"[{user}]"
    col = max(max_x - len(label) - 1, 0)
    _safe_addstr(stdscr, 0, col, label, curses.A_BOLD)


def _scroll_offset(selected: int, offset: int, visible_rows: int, total: int) -> int:
    if visible_rows <= 0 or total <= 0:
        return 0
    offset = min(offset, max(total - visible_rows, 0))
    if selected < offset:
        offset = selected
    elif selected >= offset + visible_rows:
        offset = selected - visible_rows + 1
    return max(offset, 0)


def _draw_menu(stdscr, gateway: str, user: str, notes: list[dict], selected: int, offset: int, status: str) -> None:
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 0, "SyncNotes", curses.A_BOLD)
    _safe_addstr(stdscr, 0, 10, f"({gateway})", curses.A_DIM)
    _draw_user_label(stdscr, user)
    _safe_addstr(stdscr, 1, 0, "Your team's shared notes. Up/Down to move, Enter to open.")

    if not notes:
        _safe_addstr(stdscr, 3, 2, "(no notes yet - press 'n' to create one)")
    else:
        if offset > 0:
            _safe_addstr(stdscr, 2, 2, f"^ {offset} more above", curses.A_DIM)
        shown = 0
        for i, note in enumerate(notes[offset:]):
            row = 3 + i
            if row >= max_y - 4:
                break
            idx = offset + i
            line = f"{note['name']:<30} v{note['version']:<4} updated {format_updated_at(note['updated_at'])}"
            attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
            _safe_addstr(stdscr, row, 2, line, attr)
            shown += 1
        below = len(notes) - offset - shown
        if below > 0:
            _safe_addstr(stdscr, max_y - 4, 2, f"v {below} more below", curses.A_DIM)

    footer_row = max_y - 3
    _safe_addstr(stdscr, footer_row, 0, "-" * max(max_x - 1, 0))
    _safe_addstr(stdscr, footer_row + 1, 0, "[Enter] open  [n] new  [d] delete  [r] refresh  [q] quit")
    if status:
        _safe_addstr(stdscr, footer_row + 2, 0, status)
    stdscr.refresh()


def _prompt_name(stdscr) -> str | None:
    max_y, max_x = stdscr.getmaxyx()
    hint = "no '/' '\\' or leading '.'"
    prompt = "New note name (empty to cancel): "
    row = max_y - 1
    _safe_addstr(stdscr, row - 1, 0, " " * max(max_x - 1, 0))
    _safe_addstr(stdscr, row - 1, 0, hint[: max(max_x - 1, 0)], curses.A_DIM)
    _safe_addstr(stdscr, row, 0, " " * max(max_x - 1, 0))
    _safe_addstr(stdscr, row, 0, prompt)
    stdscr.refresh()

    curses.echo()
    curses.curs_set(1)
    stdscr.timeout(-1)
    try:
        col = min(len(prompt), max(max_x - 1, 0))
        raw = stdscr.getstr(row, col, max(max_x - col - 1, 1))
    finally:
        stdscr.timeout(REFRESH_TICK_MS)
        curses.noecho()
        curses.curs_set(0)
    if not raw:
        return None
    return raw.decode("utf-8", errors="ignore").strip()


def _prompt_confirm(stdscr, prompt: str) -> bool:
    max_y, max_x = stdscr.getmaxyx()
    row = max_y - 1
    _safe_addstr(stdscr, row, 0, " " * max(max_x - 1, 0))
    _safe_addstr(stdscr, row, 0, prompt)
    stdscr.refresh()

    curses.echo()
    curses.curs_set(1)
    stdscr.timeout(-1)
    try:
        col = min(len(prompt), max(max_x - 1, 0))
        raw = stdscr.getstr(row, col, max(max_x - col - 1, 1))
    finally:
        stdscr.timeout(REFRESH_TICK_MS)
        curses.noecho()
        curses.curs_set(0)

    return bool(raw) and raw.decode("utf-8", errors="ignore").strip()[:1].lower() == "y"


_ILLEGAL_NAME_CHARS = set('<>:"|?*')


def _invalid_note_name_reason(name: str) -> str | None:
    if not name or not name.strip():
        return "can't be empty or just spaces"
    if name != name.strip():
        return "can't have leading/trailing spaces"
    if "/" in name or "\\" in name:
        return "can't contain '/' or '\\'"
    if name.startswith("."):
        return "can't start with '.'"
    if any(ch in _ILLEGAL_NAME_CHARS or ord(ch) < 32 for ch in name):
        return "has an unsupported character"
    return None


def _edit_note_flow(stdscr, gateway: str, sync_dir: Path, user: str, name: str, live: _LiveNotes) -> str:
    live.begin_editing(name)
    try:
        try:
            local_path = open_note(gateway, sync_dir, name)
        except SyncNotesError as exc:
            return str(exc)

        content = local_path.read_text()
        try:
            new_content, saved = _edit_text(stdscr, user, name, content)
        except _TooLargeToEdit:
            return f"'{name}' is too big for this window - resize your terminal and try again"
        if not saved:
            return f"'{name}' closed without saving"

        base_version = read_base_version(str(local_path), name)
        try:
            result = save_note(gateway, sync_dir, name, new_content)
        except SyncNotesError as exc:
            return str(exc)

        note = describe_upload_outcome(base_version, new_content, result)
        return f"saved '{name}' -> version {result['version']}{note}"
    finally:
        live.end_editing()


class _TooLargeToEdit(Exception):
    """The note doesn't fit the edit window, refuse to open it rather than
    silently truncating it on save (Textbox only gathers what's on screen)."""


def _too_large_for_window(text: str, height: int, width: int) -> bool:
    lines = text.splitlines()
    return len(lines) > height or any(len(line) > width - 1 for line in lines)


def _normalize_edited_text(raw_text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in raw_text.splitlines())
    if normalized:
        normalized += "\n"
    return normalized


def _edit_text(stdscr, user: str, name: str, initial_text: str) -> tuple[str, bool]:
    stdscr.erase()
    _safe_addstr(stdscr, 0, 0, f"Editing: {name}", curses.A_BOLD)
    _draw_user_label(stdscr, user)
    _safe_addstr(stdscr, 1, 0, "Ctrl+G to save & exit    Esc to discard changes")
    stdscr.refresh()

    max_y, max_x = stdscr.getmaxyx()
    height, width = max_y - 4, max_x - 2
    lines = initial_text.splitlines()
    if height <= 0 or width <= 0 or _too_large_for_window(initial_text, height, width):
        raise _TooLargeToEdit

    edit_win = curses.newwin(height, width, 3, 1)
    edit_win.keypad(True)
    for row, line in enumerate(lines):
        _safe_addstr(edit_win, row, 0, line)

    cancelled = {"flag": False}

    def validator(ch):
        if ch == curses.ascii.ESC:
            cancelled["flag"] = True
            return curses.ascii.BEL 
        return ch

    box = curses.textpad.Textbox(edit_win, insert_mode=True)
    curses.curs_set(1)
    raw_text = box.edit(validator)
    curses.curs_set(0)

    if cancelled["flag"]:
        return initial_text, False

    return _normalize_edited_text(raw_text), True


if __name__ == "__main__":
    main()
