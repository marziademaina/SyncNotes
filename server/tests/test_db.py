def test_apply_creates_new_file(local_db):
    status, version = local_db.apply_if_newer("notes.md", 1, "hello", "h1")
    assert (status, version) == ("applied", 1)
    assert local_db.list_files() == {"notes.md": {"version": 1, "content_hash": "h1"}}


def test_apply_accepts_newer_version(local_db):
    local_db.apply_if_newer("notes.md", 1, "v1", "h1")
    status, version = local_db.apply_if_newer("notes.md", 2, "v2", "h2")
    assert (status, version) == ("applied", 2)
    assert local_db.list_files()["notes.md"]["content_hash"] == "h2"


def test_apply_ignores_older_version(local_db):
    local_db.apply_if_newer("notes.md", 2, "v2", "h2")
    status, version = local_db.apply_if_newer("notes.md", 1, "v1", "h1")
    assert (status, version) == ("ignored", 2)
    assert local_db.list_files()["notes.md"]["content_hash"] == "h2"


def test_apply_ignores_same_version_same_hash(local_db):
    local_db.apply_if_newer("notes.md", 1, "v1", "h1")
    status, version = local_db.apply_if_newer("notes.md", 1, "v1", "h1")
    assert (status, version) == ("ignored", 1)


def test_apply_repairs_same_version_different_hash(local_db):
    local_db.apply_if_newer("notes.md", 1, "corrupted", "bad-hash")
    status, version = local_db.apply_if_newer("notes.md", 1, "correct", "good-hash")
    assert (status, version) == ("applied", 1)
    assert local_db.list_files()["notes.md"]["content_hash"] == "good-hash"


def test_apply_without_force_ignores_write_when_version_and_hash_already_match(local_db):
    local_db.apply_if_newer("notes.md", 1, "hello", "h1")
    status, version = local_db.apply_if_newer("notes.md", 1, "hello-but-different", "h1")
    assert (status, version) == ("ignored", 1)


def test_apply_with_force_overwrites_even_when_version_and_hash_match(local_db):
    local_db.apply_if_newer("notes.md", 1, "hello", "h1")
    status, version = local_db.apply_if_newer("notes.md", 1, "corrected", "h1", force=True)
    assert (status, version) == ("applied", 1)

    session = local_db.get_session()
    record = session.get(local_db.FileRecord, "notes.md")
    assert record.content == "corrected"
    session.close()


def test_list_files_summary_is_empty_with_no_files(local_db):
    assert local_db.list_files_summary() == []


def test_list_files_summary_reports_name_version_hash_and_updated_at(local_db):
    local_db.apply_if_newer("notes.md", 1, "hello", "h1")

    summary = local_db.list_files_summary()

    assert len(summary) == 1
    entry = summary[0]
    assert entry["name"] == "notes.md"
    assert entry["version"] == 1
    assert entry["content_hash"] == "h1"
    assert "updated_at" in entry


def test_list_files_summary_is_sorted_by_name(local_db):
    local_db.apply_if_newer("zzz.md", 1, "z", "hz")
    local_db.apply_if_newer("aaa.md", 1, "a", "ha")

    names = [entry["name"] for entry in local_db.list_files_summary()]

    assert names == ["aaa.md", "zzz.md"]


def test_find_locally_corrupted_files_detects_content_hash_mismatch(local_db):
    import hashlib

    correct_hash = hashlib.sha256(b"hello").hexdigest()
    local_db.apply_if_newer("notes.md", 1, "hello", correct_hash)
    assert local_db.find_locally_corrupted_files() == set()

    session = local_db.get_session()
    record = session.get(local_db.FileRecord, "notes.md")
    record.content = "corrupted in place"
    session.commit()
    session.close()

    assert local_db.find_locally_corrupted_files() == {"notes.md"}
