import difflib

Hunk = tuple[int, int, list[str]]


def resolve_conflict(base_content: str | None, authoritative_content: str | None, uploaded_content: str) -> str:
    if authoritative_content is None:
        return uploaded_content

    if base_content is None:
        return authoritative_content

    if base_content == authoritative_content:
        return uploaded_content

    if base_content == uploaded_content:
        return authoritative_content

    base_lines = base_content.splitlines(keepends=True)
    server_lines = authoritative_content.splitlines(keepends=True)
    client_lines = uploaded_content.splitlines(keepends=True)

    server_hunks = _hunks(base_lines, server_lines)
    client_hunks = _hunks(base_lines, client_lines)

    non_conflicting_client_hunks = [h for h in client_hunks if not any(_overlaps(h, s) for s in server_hunks)]

    # Server hunks always win; where a client hunk survives (untouched by the
    # server), it stands. At a shared boundary, the server's hunk sorts first.
    tagged = [(i1, i2, lines, 0) for i1, i2, lines in server_hunks]
    tagged += [(i1, i2, lines, 1) for i1, i2, lines in non_conflicting_client_hunks]
    combined = sorted(tagged, key=lambda h: (h[0], h[3]))

    merged: list[str] = []
    pos = 0
    for i1, i2, lines, _source in combined:
        merged.extend(base_lines[pos:i1])
        merged.extend(lines)
        pos = max(pos, i2)
    merged.extend(base_lines[pos:])

    return "".join(merged)


def _hunks(base_lines: list[str], other_lines: list[str]) -> list[Hunk]:
    matcher = difflib.SequenceMatcher(None, base_lines, other_lines, autojunk=False)
    return [(i1, i2, other_lines[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _overlaps(a: Hunk, b: Hunk) -> bool:
    a1, a2, _ = a
    b1, b2, _ = b
    return a1 < b2 and b1 < a2
