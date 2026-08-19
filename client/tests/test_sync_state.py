import json

from client.sync_state import read_base_version, write_state


def test_write_state_creates_a_sidecar_next_to_the_file(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("hello")

    write_state(str(target), "notes.md", 3)

    sidecar = tmp_path / "notes.md.syncnotes.json"
    assert json.loads(sidecar.read_text()) == {"name": "notes.md", "version": 3}


def test_read_base_version_returns_none_when_sidecar_is_missing(tmp_path):
    target = tmp_path / "notes.md"
    assert read_base_version(str(target), "notes.md") is None


def test_read_base_version_returns_none_when_the_name_does_not_match(tmp_path):
    target = tmp_path / "notes.md"
    write_state(str(target), "other.md", 5)

    assert read_base_version(str(target), "notes.md") is None


def test_read_base_version_round_trips_through_write_state(tmp_path):
    target = tmp_path / "notes.md"
    write_state(str(target), "notes.md", 7)

    assert read_base_version(str(target), "notes.md") == 7
