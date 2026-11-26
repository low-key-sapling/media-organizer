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
    documents = {"20261125.txt": "txt", "合同_20261125.PDF": "pdf", "记录_20261125.docx": "docx"}
    for name in documents:
        (source / name).write_text(name, encoding="utf-8")
    with TestClient(create_app(organizer=organizer), base_url="http://127.0.0.1") as client:
        assert client.get("/api/meta").json()["offline"]
        response = client.post("/api/jobs", json={"source": str(source), "target": str(target)})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert settled(client, job_id)["status"] == "ready"
        listing = client.get(f"/api/jobs/{job_id}/files").json()
        entries = {entry["source_rel"]: entry for entry in listing["items"]}
        for name, extension in documents.items():
            entry = entries[name]
            assert entry["target_rel"] == f"documents/202611/{extension}/{name}"
            assert entry["location"] == ""
        entry_id = entries["20261125.txt"]["id"]
        location = client.patch(f"/api/jobs/{job_id}/files", json={"ids": [entry_id], "location": "CN_Zhejiang_Hangzhou"})
        assert location.status_code == 400
        edited = client.patch(f"/api/jobs/{job_id}/files", json={"ids": [entry_id], "month": "202001"})
        assert edited.status_code == 200
        assert client.post(f"/api/jobs/{job_id}/execute", json={"verification": "full"}).status_code == 202
        assert settled(client, job_id)["safe_to_delete"] is True
        export = client.get(f"/api/jobs/{job_id}/report.csv")
        assert export.status_code == 200
        manifest = {row["source_rel"]: row for row in csv.DictReader(io.StringIO(export.content.decode("utf-8-sig")))}
        for name, extension in documents.items():
            month = "202001" if name == "20261125.txt" else "202611"
            path = f"documents/{month}/{extension}/{name}"
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
