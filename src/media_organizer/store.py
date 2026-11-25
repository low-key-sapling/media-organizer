from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "organizer.sqlite3"
        self.anchor = sqlite3.connect(self.path, check_same_thread=False)
        self.anchor.execute("PRAGMA journal_mode=WAL")
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                    source_rel TEXT NOT NULL, target_rel TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'others',
                    month TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT 'others', body TEXT NOT NULL,
                    UNIQUE(job_id, source_rel)
                );
                CREATE INDEX IF NOT EXISTS entries_job ON entries(job_id, id);
                CREATE INDEX IF NOT EXISTS entries_status ON entries(job_id, status);
                CREATE INDEX IF NOT EXISTS entries_category ON entries(job_id, category);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_job(self, body: dict) -> dict:
        body = {**body, "created_at": now(), "updated_at": now()}
        with self.connection() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (body["id"], body["status"], body["created_at"], json.dumps(body, ensure_ascii=False)))
        return body

    def job(self, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在")
        return json.loads(row[0])

    def jobs(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def update_job(self, job_id: str, **changes) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在")
            body = {**json.loads(row[0]), **changes, "updated_at": now()}
            db.execute("UPDATE jobs SET status=?,body=? WHERE id=?", (body["status"], json.dumps(body, ensure_ascii=False), job_id))
        return body

    def insert_entries(self, job_id: str, entries: list[dict]) -> None:
        with self.connection() as db:
            db.executemany("""INSERT INTO entries(job_id,source_rel,target_rel,kind,status,category,month,location,body)
                VALUES(?,?,?,?,?,?,?,?,?)""", [(job_id, e["source_rel"], e.get("target_rel", ""), e["kind"], e["status"], e.get("category", "others"), e.get("month", ""), e.get("location", "others"), json.dumps(e, ensure_ascii=False)) for e in entries])

    @staticmethod
    def decode(row) -> dict:
        return {**json.loads(row["body"]), "id": row["id"], "job_id": row["job_id"]}

    def entry(self, job_id: str, entry_id: int) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM entries WHERE job_id=? AND id=?", (job_id, entry_id)).fetchone()
        if not row:
            raise ValueError("文件记录不存在")
        return self.decode(row)

    def update_entry(self, job_id: str, entry_id: int, **changes) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM entries WHERE job_id=? AND id=?", (job_id, entry_id)).fetchone()
            if not row:
                raise ValueError("文件记录不存在")
            body = {**self.decode(row), **changes}
            db.execute("UPDATE entries SET target_rel=?,status=?,category=?,month=?,location=?,body=? WHERE id=?", (body.get("target_rel", ""), body["status"], body["category"], body.get("month", ""), body.get("location", "others"), json.dumps(body, ensure_ascii=False), entry_id))
        return body

    def entries(self, job_id: str, page: int = 1, page_size: int = 50, search: str = "", category: str = "", status: str = "") -> dict:
        where, args = ["job_id=?"], [job_id]
        if search:
            where.append("(source_rel LIKE ? ESCAPE '\\' OR target_rel LIKE ? ESCAPE '\\')")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([f"%{escaped}%"] * 2)
        for column, value in (("category", category), ("status", status)):
            if value:
                if column == "status" and value == "issues":
                    where.append("status IN ('failed','unhandled')")
                    continue
                where.append(f"{column}=?")
                args.append(value)
        clause = " AND ".join(where)
        with self.connection() as db:
            count = db.execute(f"SELECT COUNT(*) FROM entries WHERE {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM entries WHERE {clause} ORDER BY id LIMIT ? OFFSET ?", (*args, page_size, (page - 1) * page_size)).fetchall()
        return {"items": [self.decode(row) for row in rows], "total": count, "page": page, "page_size": page_size}

    def iter_entries(self, job_id: str, kind: str | None = None):
        cursor = 0
        while True:
            with self.connection() as db:
                sql = "SELECT * FROM entries WHERE job_id=? AND id>?"
                args: list = [job_id, cursor]
                if kind:
                    sql += " AND kind=?"
                    args.append(kind)
                rows = db.execute(sql + " ORDER BY id LIMIT 200", args).fetchall()
            if not rows:
                break
            for row in rows:
                cursor = row["id"]
                yield self.decode(row)

    def stats(self, job_id: str) -> dict:
        with self.connection() as db:
            categories = dict(db.execute("SELECT category,COUNT(*) FROM entries WHERE job_id=? AND kind='file' GROUP BY category", (job_id,)))
            statuses = dict(db.execute("SELECT status,COUNT(*) FROM entries WHERE job_id=? GROUP BY status", (job_id,)))
            total = db.execute("SELECT COUNT(*) FROM entries WHERE job_id=? AND kind='file'", (job_id,)).fetchone()[0]
        return {"categories": categories, "statuses": statuses, "file_count": total}

    def settings(self) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT body FROM settings WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def save_settings(self, body: dict) -> dict:
        with self.connection() as db:
            db.execute("INSERT INTO settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (json.dumps(body, ensure_ascii=False),))
        return body

    def recover(self):
        for job in self.jobs(limit=10000):
            if job["status"] in ("scanning", "queued_scan"):
                self.update_job(job["id"], status="scan_interrupted", phase="扫描已中断，请重新创建任务", safe_to_delete=False)
            elif job["status"] in ("copying", "verifying", "queued"):
                self.update_job(job["id"], status="paused", phase="任务已保存，可以继续", safe_to_delete=False)

    def close(self):
        self.anchor.close()
