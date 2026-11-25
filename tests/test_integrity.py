import csv
import json
import os
import threading
from pathlib import Path

import pytest

from media_organizer.filedata import Interrupted
from media_organizer.paths import contained, validate_roots


def run(organizer, source, target, level="full"):
    job = organizer.create(str(source), str(target), level, background=False)
    assert job["status"] == "ready", job
    return organizer.execute(job["id"], background=False)


def test_full_copy_is_lossless_and_keeps_source(organizer, directories):
    source, target = directories
    original = {"nested/IMG_20261125_100000.jpg": b"original photo", "zero.bin": b"", ".hidden": b"hidden", "paired.xmp": b"sidecar"}
    for name, body in original.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    job = run(organizer, source, target)
    assert job["safe_to_delete"] is True, job
    assert job["verified_count"] == len(original)
    for entry in organizer.store.iter_entries(job["id"]):
        assert (source / entry["source_rel"]).read_bytes() == original[entry["source_rel"]]
        assert (target / entry["target_rel"]).read_bytes() == original[entry["source_rel"]]
        assert entry["source_hash"] == entry["target_hash"]
    report = json.loads((Path(job["report_path"]) / "report.json").read_text(encoding="utf-8"))
    assert report["safe_to_delete"] is True
    with (Path(job["report_path"]) / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == len(original)


@pytest.mark.parametrize("level", ["quick", "sampled"])
def test_lower_levels_never_suggest_deletion(organizer, directories, level):
    source, target = directories
    for i in range(11):
        (source / f"{i}.txt").write_bytes(bytes([i]))
    job = run(organizer, source, target, level)
    assert job["status"] == "completed", job
    assert not job["safe_to_delete"]
    assert job["verified_count"] == (2 if level == "sampled" else 0)
    upgraded = organizer.execute(job["id"], "full", background=False, verify_only=True)
    assert upgraded["safe_to_delete"] is True


def test_same_name_same_content_reuses_and_different_content_survives(organizer, directories):
    source, target = directories
    for directory, content in (("a", b"AAAA"), ("b", b"BBBB"), ("c", b"AAAA")):
        (source / directory).mkdir()
        (source / directory / "20261125.txt").write_bytes(content)
    job = run(organizer, source, target, "quick")
    entries = list(organizer.store.iter_entries(job["id"]))
    assert len({e["target_rel"] for e in entries}) == 2
    assert {p.read_bytes() for p in (target / "documents/202611/others").glob("*.txt")} == {b"AAAA", b"BBBB"}
    assert any(e["status"] == "reused" for e in entries)
    repeated = run(organizer, source, target)
    assert repeated["safe_to_delete"]
    assert len(list((target / "documents/202611/others").glob("*.txt"))) == 2


def test_corruption_with_same_size_is_detected(organizer, directories):
    source, target = directories
    (source / "sample.txt").write_bytes(b"AAAA")
    job = run(organizer, source, target)
    entry = next(organizer.store.iter_entries(job["id"]))
    (target / entry["target_rel"]).write_bytes(b"BBBB")
    checked = organizer.execute(job["id"], "full", background=False, verify_only=True)
    assert checked["status"] == "completed_issues"
    assert not checked["safe_to_delete"]
    repaired = organizer.execute(job["id"], "full", background=False)
    assert repaired["safe_to_delete"], repaired
    assert (source / "sample.txt").read_bytes() == b"AAAA"


@pytest.mark.parametrize("change", ["new", "modified", "missing"])
def test_source_changes_block_deletion(organizer, directories, change):
    source, target = directories
    file = source / "sample.txt"
    file.write_bytes(b"original")
    job = run(organizer, source, target)
    if change == "new":
        (source / "new.txt").write_bytes(b"new")
    elif change == "modified":
        file.write_bytes(b"changed!")
    else:
        file.unlink()
    checked = organizer.execute(job["id"], background=False, verify_only=True)
    assert checked["status"] == "completed_issues"
    assert checked["changes_count"] >= 1
    assert not checked["safe_to_delete"]


def test_changes_between_scan_and_copy_are_not_silently_accepted(organizer, directories):
    source, target = directories
    file = source / "a.txt"
    file.write_bytes(b"before")
    job = organizer.create(str(source), str(target), background=False)
    file.write_bytes(b"after")
    result = organizer.execute(job["id"], background=False)
    assert result["status"] == "completed_issues"
    assert not result["safe_to_delete"]


def test_preview_correction_does_not_modify_source(organizer, directories):
    source, target = directories
    file = source / "foo.bin"
    file.write_bytes(b"bytes")
    job = organizer.create(str(source), str(target), background=False)
    entry = next(organizer.store.iter_entries(job["id"]))
    organizer.edit(job["id"], [entry["id"]], {"category": "photos", "month": "202001", "location": "CN_Zhejiang_Hangzhou"})
    result = organizer.execute(job["id"], background=False)
    assert result["safe_to_delete"]
    assert (target / "photos/202001/CN_Zhejiang_Hangzhou/foo.bin").read_bytes() == file.read_bytes()


def test_invalid_override_and_path_escape_rejected(organizer, directories, tmp_path):
    source, target = directories
    (source / "a.txt").write_text("hello")
    job = organizer.create(str(source), str(target), background=False)
    entry = next(organizer.store.iter_entries(job["id"]))
    for change in ({"month": "202613"}, {"location": "../../outside"}, {"category": "invalid"}):
        with pytest.raises(ValueError):
            organizer.edit(job["id"], [entry["id"]], change)
    with pytest.raises(ValueError):
        contained(target, "../outside.txt")
    for dst in (source, source / "inside", source.parent):
        with pytest.raises(ValueError):
            validate_roots(str(source), str(dst))


def test_symlink_is_reported_not_followed(organizer, directories, tmp_path):
    source, target = directories
    (source / "ordinary.txt").write_text("hello")
    outside = tmp_path / "outside.txt"
    outside.write_text("external")
    try:
        (source / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or permission")
    job = run(organizer, source, target)
    assert not job["safe_to_delete"]
    assert job["unhandled_count"] == 1
    assert outside.read_text() == "external"


def test_pause_keeps_progress_and_resume_is_idempotent(organizer, directories, monkeypatch):
    source, target = directories
    for i in range(3):
        (source / f"{i}.txt").write_bytes(str(i).encode())
    job = organizer.create(str(source), str(target), background=False)
    original = organizer._copy
    counter = 0
    def pause_after_one(job, entry, event):
        nonlocal counter
        counter += 1
        if counter == 2:
            event.set()
            raise Interrupted()
        return original(job, entry, event)
    monkeypatch.setattr(organizer, "_copy", pause_after_one)
    paused = organizer.execute(job["id"], background=False)
    assert paused["status"] == "paused"
    assert not paused["safe_to_delete"]
    monkeypatch.setattr(organizer, "_copy", original)
    finished = organizer.execute(job["id"], background=False)
    assert finished["safe_to_delete"], finished
    assert len(list(target.rglob("*.txt"))) == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data streams")
def test_windows_ads_is_copied_and_checked(organizer, directories):
    source, target = directories
    file = source / "download.txt"
    file.write_bytes(b"main")
    try:
        Path(str(file) + ":Zone.Identifier").write_bytes(b"[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pytest.skip("Filesystem does not support ADS")
    job = run(organizer, source, target)
    assert job["safe_to_delete"], job
    entry = next(organizer.store.iter_entries(job["id"]))
    dest = target / entry["target_rel"]
    assert Path(str(dest) + ":Zone.Identifier").read_bytes() == Path(str(file) + ":Zone.Identifier").read_bytes()
    Path(str(dest) + ":Zone.Identifier").write_bytes(b"altered")
    checked = organizer.execute(job["id"], background=False, verify_only=True)
    assert not checked["safe_to_delete"]


def test_report_failure_never_exposes_delete_recommendation(organizer, directories, monkeypatch):
    source, target = directories
    (source / "a.txt").write_bytes(b"A")
    def cannot_report(*args):
        raise OSError("disk full")
    monkeypatch.setattr(organizer, "report", cannot_report)
    job = run(organizer, source, target)
    assert job["status"] == "failed"
    assert not job["safe_to_delete"]


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_junction_is_reported_without_following(organizer, directories, tmp_path):
    import _winapi
    source, target = directories
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (outside / "external.txt").write_text("outside")
    (source / "ordinary.txt").write_text("ordinary")
    _winapi.CreateJunction(str(outside), str(source / "junction"))
    job = run(organizer, source, target)
    assert job["file_count"] == 1
    assert job["unhandled_count"] == 1
    assert not job["safe_to_delete"]
    assert (outside / "external.txt").read_text() == "outside"


def test_original_and_target_properties_are_auditable(organizer, directories):
    source, target = directories
    file = source / "sample.txt"
    file.write_bytes(b"original")
    os.utime(file, ns=(1700000000000000000, 1700000000000000000))
    before = file.stat()
    job = run(organizer, source, target)
    assert job["safe_to_delete"], job
    entry = next(organizer.store.iter_entries(job["id"]))
    assert entry["target_mtime_ns"] == before.st_mtime_ns
    assert "attribute_differences" in entry
    if os.name == "nt":
        assert entry["target_birthtime_ns"] == before.st_birthtime_ns


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "setxattr"), reason="Linux user attributes")
def test_linux_user_attributes_are_preserved_and_verified(organizer, directories):
    source, target = directories
    file = source / "tagged.txt"
    file.write_bytes(b"content")
    try:
        os.setxattr(file, "user.media-tag", b"favorite")
    except OSError:
        pytest.skip("Filesystem does not support user attributes")
    job = run(organizer, source, target)
    assert job["safe_to_delete"], job
    entry = next(organizer.store.iter_entries(job["id"]))
    destination = target / entry["target_rel"]
    assert os.getxattr(destination, "user.media-tag") == b"favorite"
    os.setxattr(destination, "user.media-tag", b"changed")
    checked = organizer.execute(job["id"], background=False, verify_only=True)
    assert not checked["safe_to_delete"]


def test_target_directory_link_cannot_escape_root(organizer, directories, tmp_path):
    source, target = directories
    (source / "sample.txt").write_bytes(b"source")
    outside = tmp_path / "outside-target"
    outside.mkdir()
    target.mkdir()
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(target / "documents"))
    else:
        (target / "documents").symlink_to(outside, target_is_directory=True)
    job = run(organizer, source, target)
    assert not job["safe_to_delete"]
    assert list(outside.iterdir()) == []
    assert (source / "sample.txt").read_bytes() == b"source"


def test_changes_to_an_earlier_verified_target_are_detected(organizer, directories, monkeypatch):
    import media_organizer.engine as engine
    source, target = directories
    for name in ("one.txt", "two.txt"):
        (source / name).write_bytes(b"original")
    job = run(organizer, source, target)
    entries = list(organizer.store.iter_entries(job["id"]))
    first_target = target / entries[0]["target_rel"]
    second_source = source / entries[1]["source_rel"]
    digest = engine.sha256
    modified = False
    def change_during_later_hash(path, stop=None, progress=None):
        nonlocal modified
        if path == second_source and not modified:
            first_target.write_bytes(b"tampered")
            modified = True
        return digest(path, stop, progress)
    monkeypatch.setattr(engine, "sha256", change_during_later_hash)
    checked = organizer.execute(job["id"], background=False, verify_only=True)
    assert modified
    assert not checked["safe_to_delete"]
    assert checked["status"] == "completed_issues"
