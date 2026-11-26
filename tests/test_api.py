import csv
import io
import time

from fastapi.testclient import TestClient

from media_organizer.api import create_app


def settled(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued_scan", "scanning", "queued", "copying", "verifying"):
            return job
        time.sleep(0.02)
    raise AssertionError("Task did not finish")


def test_api_complete_flow_and_report(organizer, directories):
    source, target = directories
    destinations = {
        "20261125.txt": "documents/202611/txt",
        "合同_20261125.PDF": "documents/202611/pdf",
        "记录_20261125.docx": "documents/202611/docx",
        "资料_20261125.ZIP": "archives/202611/zip",
        "备份_20261125.7z": "archives/202611/7z",
        "备份_20261125.TAR.GZ": "archives/202611/tar.gz",
        "归档_20261125.tar.xz": "archives/202611/tar.xz",
        "日志_20261125.gz": "archives/202611/gz",
    }
    for name in destinations:
        (source / name).write_text(name, encoding="utf-8")
    with TestClient(create_app(organizer=organizer), base_url="http://127.0.0.1") as client:
        meta = client.get("/api/meta").json()
        assert meta["offline"] and set(meta["group_by_extension"]) == {"documents", "archives"}
        response = client.post("/api/jobs", json={"source": str(source), "target": str(target)})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert settled(client, job_id)["status"] == "ready"
        listing = client.get(f"/api/jobs/{job_id}/files").json()
        entries = {entry["source_rel"]: entry for entry in listing["items"]}
        for name, folder in destinations.items():
            entry = entries[name]
            assert entry["target_rel"] == f"{folder}/{name}"
            assert entry["location"] == ""
        edited_names = ("20261125.txt", "备份_20261125.TAR.GZ")
        edited_ids = [entries[name]["id"] for name in edited_names]
        location = client.patch(f"/api/jobs/{job_id}/files", json={"ids": edited_ids, "location": "CN_Zhejiang_Hangzhou"})
        assert location.status_code == 400
        edited = client.patch(f"/api/jobs/{job_id}/files", json={"ids": edited_ids, "month": "202001"})
        assert edited.status_code == 200
        destinations["20261125.txt"] = "documents/202001/txt"
        destinations["备份_20261125.TAR.GZ"] = "archives/202001/tar.gz"
        assert client.post(f"/api/jobs/{job_id}/execute", json={"verification": "full"}).status_code == 202
        assert settled(client, job_id)["safe_to_delete"] is True
        export = client.get(f"/api/jobs/{job_id}/report.csv")
        assert export.status_code == 200
        manifest = {row["source_rel"]: row for row in csv.DictReader(io.StringIO(export.content.decode("utf-8-sig")))}
        for name, folder in destinations.items():
            path = f"{folder}/{name}"
            assert manifest[name]["target_rel"] == path
            assert manifest[name]["location"] == ""
            assert (target / path).read_bytes() == (source / name).read_bytes()


def test_origin_host_and_path_validation(organizer, directories):
    source, target = directories
    with TestClient(create_app(organizer=organizer), base_url="http://127.0.0.1") as client:
        assert client.post("/api/jobs", headers={"Origin": "https://untrusted.example"}, json={"source": str(source), "target": str(target)}).status_code == 403
        assert client.get("/api/meta", headers={"Host": "untrusted.example"}).status_code == 400
        assert client.post("/api/jobs", json={"source": str(source), "target": str(source / "nested")}).status_code == 400
        assert client.put("/api/settings", json={"extension_mapping": {"weird": "../bad"}}).status_code == 400
        assert client.get("/api/jobs/no-such-job/files").status_code == 400


def test_process_lock_prevents_concurrent_recovery(organizer):
    import pytest
    from media_organizer.engine import Organizer
    with pytest.raises(ValueError, match="已有程序"):
        Organizer(organizer.store.path.parent)
