# Full-screen terminal UI for SyncNotes

import curses
import curses.ascii
import curses.textpad
import getpass
from pathlib import Path

from client.notes import SyncNotesError, create_note, describe_upload_outcome, fetch_notes, open_note, save_note
from client.sync_state import read_base_version

DEFAULT_GATEWAY = "http://localhost:8080"
DEFAULT_SYNC_DIR = Path.home() / "SyncNotes"

MIN_ROWS = 10
MIN_COLS = 40


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
    notes, status = _reload(gateway)

    while True:
        max_y, max_x = stdscr.getmaxyx()
        if max_y < MIN_ROWS or max_x < MIN_COLS:
            try:
                stdscr.erase()
                _safe_addstr(stdscr, 0, 0, "Terminal window is too small for SyncNotes.")
                _safe_addstr(stdscr, 1, 0, "Resize it, or press 'q' to quit.")
                stdscr.refresh()
                if stdscr.getch() == ord("q"):
                    return
            except curses.error:
                pass
            continue

        try:
            _draw_menu(stdscr, gateway, user, notes, selected, status)
            key = stdscr.getch()
        except curses.error:
            continue
        status = ""

        if key in (curses.KEY_UP, ord("k")) and notes:
            selected = (selected - 1) % len(notes)
        elif key in (curses.KEY_DOWN, ord("j")) and notes:
            selected = (selected + 1) % len(notes)
        elif key in (curses.KEY_ENTER, 10, 13) and notes:
            try:
                status = _edit_note_flow(stdscr, gateway, sync_dir, user, notes[selected]["name"])
            except curses.error:
                status = "screen glitch while editing - your last edit may not have saved, please retry"
            notes, reload_status = _reload(gateway)
            status = reload_status or status
            selected = min(selected, max(len(notes) - 1, 0))
        elif key == ord("n"):
            try:
                name = _prompt_name(stdscr)
            except curses.error:
                name, status = None, "screen glitch - try 'n' again"
            if name and not _is_valid_note_name(name):
                status = f"'{name}' is not a valid note name (no '/', '\\', or leading '.')"
            elif name:
                try:
                    create_note(gateway, sync_dir, name)
                    status = f"created '{name}'"
                except SyncNotesError as exc:
                    status = str(exc)
            notes, reload_status = _reload(gateway)
            status = reload_status or status
        elif key == ord("r"):
            notes, status = _reload(gateway)
        elif key in (ord("q"), curses.ascii.ESC):
            return


def _reload(gateway: str) -> tuple[list[dict], str]:
    try:
        return fetch_notes(gateway), ""
    except SyncNotesError as exc:
        return [], str(exc)


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


def _draw_menu(stdscr, gateway: str, user: str, notes: list[dict], selected: int, status: str) -> None:
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 0, "SyncNotes", curses.A_BOLD)
    _safe_addstr(stdscr, 0, 10, f"({gateway})", curses.A_DIM)
    _draw_user_label(stdscr, user)
    _safe_addstr(stdscr, 1, 0, "Your team's shared notes. Up/Down to move, Enter to open.")

    if not notes:
        _safe_addstr(stdscr, 3, 2, "(no notes yet - press 'n' to create one)")
    else:
        for i, note in enumerate(notes):
            row = 3 + i
            if row >= max_y - 4:
                break
            line = f"{note['name']:<30} v{note['version']:<4} updated {note['updated_at']}"
            attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
            _safe_addstr(stdscr, row, 2, line, attr)

    footer_row = max_y - 3
    _safe_addstr(stdscr, footer_row, 0, "-" * max(max_x - 1, 0))
    _safe_addstr(stdscr, footer_row + 1, 0, "[Enter] open   [n] new note   [r] refresh   [q] quit")
    if status:
        _safe_addstr(stdscr, footer_row + 2, 0, status)
    stdscr.refresh()


def _prompt_name(stdscr) -> str | None:
    max_y, max_x = stdscr.getmaxyx()
    prompt = "New note name (Enter to confirm, empty to cancel): "
    row = max_y - 1
    _safe_addstr(stdscr, row, 0, " " * max(max_x - 1, 0))
    _safe_addstr(stdscr, row, 0, prompt)
    stdscr.refresh()

    curses.echo()
    curses.curs_set(1)
    try:
        col = min(len(prompt), max(max_x - 1, 0))
        raw = stdscr.getstr(row, col, max(max_x - col - 1, 1))
    finally:
        curses.noecho()
        curses.curs_set(0)

    name = raw.decode("utf-8", errors="ignore").strip()
    return name or None


def _is_valid_note_name(name: str) -> bool:
    return "/" not in name and "\\" not in name and not name.startswith(".")


def _edit_note_flow(stdscr, gateway: str, sync_dir: Path, user: str, name: str) -> str:
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
            return curses.ascii.BEL  # tells Textbox to stop editing
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
