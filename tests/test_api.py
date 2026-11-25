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
    (source / "20261125.txt").write_text("hello", encoding="utf-8")
    with TestClient(create_app(organizer=organizer), base_url="http://127.0.0.1") as client:
        assert client.get("/api/meta").json()["offline"]
        response = client.post("/api/jobs", json={"source": str(source), "target": str(target)})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert settled(client, job_id)["status"] == "ready"
        listing = client.get(f"/api/jobs/{job_id}/files").json()
        entry_id = listing["items"][0]["id"]
        edited = client.patch(f"/api/jobs/{job_id}/files", json={"ids": [entry_id], "month": "202001"})
        assert edited.status_code == 200
        assert client.post(f"/api/jobs/{job_id}/execute", json={"verification": "full"}).status_code == 202
        assert settled(client, job_id)["safe_to_delete"] is True
        export = client.get(f"/api/jobs/{job_id}/report.csv")
        assert export.status_code == 200
        assert "202001" in export.text
        assert (source / "20261125.txt").read_text() == "hello"


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
