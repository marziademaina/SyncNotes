from server.merge import resolve_conflict


def test_new_file_accepts_upload_in_full():
    assert resolve_conflict(None, None, "hello") == "hello"


def test_unknown_base_treats_whole_upload_as_conflicting_with_existing_file():
    assert resolve_conflict(None, "server content", "client content") == "server content"


def test_server_untouched_since_base_accepts_the_whole_client_edit():
    base = "A\nB\nC\n"
    assert resolve_conflict(base, base, "A\nB-edited\nC\n") == "A\nB-edited\nC\n"


def test_client_unedited_since_base_keeps_the_server_content():
    base = "A\nB\nC\n"
    server = "A\nB-server\nC\n"
    assert resolve_conflict(base, server, base) == server


def test_disjoint_edits_are_both_kept():
    base = "A\nB\nC\nD\nE\n"
    server = "A\nB-server\nC\nD\nE\n"
    client = "A\nB\nC\nD-client\nE\n"
    assert resolve_conflict(base, server, client) == "A\nB-server\nC\nD-client\nE\n"


def test_conflicting_edit_to_the_same_line_keeps_the_server_version():
    base = "A\nB\nC\n"
    server = "A\nB-server\nC\n"
    client = "A\nB-client\nC\n"
    assert resolve_conflict(base, server, client) == server


def test_server_deletion_wins_over_a_client_edit_of_the_same_line():
    base = "A\nB\nC\n"
    server = "A\nC\n"
    client = "A\nB-client\nC\n"
    assert resolve_conflict(base, server, client) == server


def test_client_deletion_of_a_server_untouched_line_is_honored():
    base = "A\nB\nC\nD\n"
    server = "A\nB-server\nC\nD\n"
    client = "A\nB\nD\n"
    assert resolve_conflict(base, server, client) == "A\nB-server\nD\n"


def test_client_append_survives_alongside_an_unrelated_server_edit():
    base = "A\nB\nC\n"
    server = "A\nB-server\nC\n"
    client = "A\nB\nC\nD-new\n"
    assert resolve_conflict(base, server, client) == "A\nB-server\nC\nD-new\n"
