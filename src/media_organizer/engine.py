from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import stat as stat_module
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .filedata import BLOCK, Interrupted, ProcessLock, copy_extras, extra_signature, sha256, preserve_windows_properties, property_report
from .geography import Places
from .metadata import DEFAULT_MAPPING, MetadataReader, classify
from .paths import CATEGORIES, LEVELS, contained, is_link, relative_target, validate_override, validate_roots
from .store import Store, now

BUSY = {"queued_scan", "scanning", "queued", "copying", "verifying"}
DEFAULT_SETTINGS = {"source": "", "target": "", "verification": "full", "max_city_distance_km": 100, "extension_mapping": {}}


def walk(source: Path):
    stack = [source]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(source).as_posix()
                    try:
                        if is_link(path):
                            yield path, relative, "special", None, "符号链接或目录联接未跟随"
                            continue
                        st = entry.stat(follow_symlinks=False)
                        if stat_module.S_ISDIR(st.st_mode):
                            stack.append(path)
                        elif stat_module.S_ISREG(st.st_mode):
                            yield path, relative, "file", st, ""
                        else:
                            yield path, relative, "special", None, "特殊文件未处理"
                    except OSError as error:
                        yield path, relative, "error", None, str(error)
        except OSError as error:
            relative = directory.relative_to(source).as_posix()
            yield directory, relative, "error", None, str(error)


def same_snapshot(path: Path, entry: dict) -> bool:
    if is_link(path):
        return False
    try:
        st = path.stat(follow_symlinks=False)
        return stat_module.S_ISREG(st.st_mode) and st.st_size == entry["size"] and st.st_mtime_ns == entry["mtime_ns"] and st.st_ctime_ns == entry["ctime_ns"]
    except OSError:
        return False


class Organizer:
    def __init__(self, state: Path, places: Places | None = None, reader: MetadataReader | None = None):
        self.process_lock = ProcessLock(state)
        self.store = Store(state)
        self.places = places or Places()
        self.reader = reader or MetadataReader()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="organizer")
        self.events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.directory_cache: dict[Path, tuple[int, dict[str, str]]] = {}
        self.store.recover()

    def settings(self) -> dict:
        return {**DEFAULT_SETTINGS, **self.store.settings()}

    def save_settings(self, body: dict) -> dict:
        if set(body) - set(DEFAULT_SETTINGS):
            raise ValueError("配置包含未知字段")
        result = {**self.settings(), **body}
        if not all(isinstance(result[key], str) for key in ("source", "target")):
            raise ValueError("目录配置必须为字符串")
        if result["verification"] not in LEVELS:
            raise ValueError("无效的校验级别")
        if not 1 <= float(result["max_city_distance_km"]) <= 500:
            raise ValueError("城市匹配最大距离需在 1–500 公里之间")
        result["max_city_distance_km"] = float(result["max_city_distance_km"])
        mapping = result.get("extension_mapping", {})
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k.isalnum() or v not in CATEGORIES for k, v in mapping.items()):
            raise ValueError("扩展名映射需使用无点号的字母数字扩展名及有效类型")
        result["extension_mapping"] = {k.lower(): v for k, v in mapping.items()}
        return self.store.save_settings(result)

    def create(self, source: str, target: str, verification: str = "full", background: bool = True) -> dict:
        src, dst = validate_roots(source, target)
        if verification not in LEVELS:
            raise ValueError("无效的校验级别")
        with self.lock:
            if any(j["status"] in BUSY for j in self.store.jobs()):
                raise ValueError("已有任务正在运行，请等待完成或暂停后再创建任务")
            job = self.store.create_job({"id": uuid.uuid4().hex[:12], "status": "queued_scan", "source": str(src), "target": str(dst), "verification": verification, "settings": self.settings(), "phase": "等待扫描", "file_count": 0, "total_bytes": 0, "processed_count": 0, "copied_bytes": 0, "verified_count": 0, "safe_to_delete": False, "warnings": [], "changes": [], "changes_count": 0, "report_path": "", "error": ""})
            self.events[job["id"]] = threading.Event()
            if background:
                self.executor.submit(self._guard, job["id"], self.scan)
            else:
                self._guard(job["id"], self.scan)
        return self.store.job(job["id"])

    def _guard(self, job_id: str, operation, *args):
        try:
            operation(job_id, *args)
        except Interrupted:
            status = "scan_interrupted" if self.store.job(job_id)["status"] == "scanning" else "paused"
            self.store.update_job(job_id, status=status, phase="进度已保存", safe_to_delete=False)
        except Exception as error:
            self.store.update_job(job_id, status="failed", phase="任务异常，可查看错误或重试", error=str(error), safe_to_delete=False)

    def stop(self, job_id: str) -> dict:
        job = self.store.job(job_id)
        if job["status"] not in BUSY:
            raise ValueError("任务当前未在运行")
        self.events.setdefault(job_id, threading.Event()).set()
        return job

    def scan(self, job_id: str):
        job = self.store.update_job(job_id, status="scanning", phase="正在读取文件与元数据")
        source = Path(job["source"])
        pending, total, size = [], 0, 0
        event = self.events[job_id]

        def flush():
            nonlocal pending, total, size
            if not pending:
                return
            media_paths = [p for p, _, kind, _, _ in pending if kind == "file" and DEFAULT_MAPPING.get(p.suffix.lower().lstrip(".")) in ("photos", "images", "videos", "audio")]
            metadata = self.reader.read_batch(media_paths)
            records = []
            for path, relative, kind, st, error in pending:
                if event.is_set():
                    raise Interrupted()
                if kind == "file":
                    values = metadata.get(os.path.normcase(os.path.abspath(path)), {})
                    try:
                        record = classify(relative, st, values, self.places, job["settings"])
                        total += 1
                        size += st.st_size
                    except Exception as problem:
                        record = {"source_rel": relative, "target_rel": "", "kind": "error", "status": "unhandled", "category": "others", "month": "", "location": "others", "size": st.st_size, "warnings": [], "error": f"无法建立分类计划：{problem}"}
                else:
                    record = {"source_rel": relative, "target_rel": "", "kind": kind, "status": "unhandled", "category": "others", "month": "", "location": "others", "size": 0, "warnings": [], "error": error}
                records.append(record)
            self.store.insert_entries(job_id, records)
            self.store.update_job(job_id, file_count=total, total_bytes=size, phase=f"已扫描 {total:,} 个文件")
            pending = []

        argument_length = 0
        for item in walk(source):
            if event.is_set():
                raise Interrupted()
            pending.append(item)
            argument_length += len(str(item[0])) + 3
            if len(pending) >= 40 or argument_length >= 20000:
                flush()
                argument_length = 0
        flush()
        warnings = []
        if not self.reader.executable:
            warnings.append("ExifTool 不可用，当前使用图片元数据回退读取；正式发行包必须携带 ExifTool")
        if not self.places.available:
            warnings.append("离线城市库不可用，无法执行离线城市反查")
        self.store.update_job(job_id, status="ready", phase="扫描完成，请检查整理预览", stats=self.store.stats(job_id), warnings=warnings)

    def edit(self, job_id: str, ids: list[int], changes: dict) -> int:
        with self.lock:
            job = self.store.job(job_id)
            if job["status"] != "ready":
                raise ValueError("仅可在首次执行前修改整理预览")
            if not ids or len(ids) > 1000:
                raise ValueError("每次请选择 1–1000 个文件")
            allowed = {k: v for k, v in changes.items() if k in ("category", "month", "location") and v is not None and v != ""}
            if not allowed:
                raise ValueError("请填写需要修改的类型、月份或地点")
            records = []
            for entry_id in set(ids):
                entry = self.store.entry(job_id, entry_id)
                if entry["kind"] != "file":
                    raise ValueError("特殊项目不能通过修改分类转为普通文件")
                merged = {**entry, **allowed}
                validate_override(merged["category"], merged["month"], merged["location"])
                records.append((entry_id, merged))
            for entry_id, merged in records:
                self.store.update_entry(job_id, entry_id, **allowed, target_rel=relative_target(merged), manual=True)
            return len(records)

    def execute(self, job_id: str, verification: str | None = None, background: bool = True, verify_only: bool = False):
        with self.lock:
            job = self.store.job(job_id)
            if job["status"] not in ("ready", "paused", "failed", "completed", "completed_issues"):
                raise ValueError("任务尚未准备好或正在运行")
            if job["file_count"] == 0:
                raise ValueError("没有可处理的普通文件")
            if any(j["id"] != job_id and j["status"] in BUSY for j in self.store.jobs()):
                raise ValueError("已有其他任务正在运行")
            level = verification or job["verification"]
            if level not in LEVELS:
                raise ValueError("无效的校验级别")
            validate_roots(job["source"], job["target"])
            self.events[job_id] = threading.Event()
            self.store.update_job(job_id, status="queued", verification=level, safe_to_delete=False, error="", phase="等待校验" if verify_only else "等待复制")
            if background:
                self.executor.submit(self._guard, job_id, self._run, verify_only)
            else:
                self._guard(job_id, self._run, verify_only)
        return self.store.job(job_id)

    def _existing(self, parent: Path, name: str) -> Path:
        direct = parent / name
        if direct.exists() or is_link(direct):
            return direct
        stamp = parent.stat().st_mtime_ns
        cached = self.directory_cache.get(parent)
        if not cached or cached[0] != stamp:
            names = {candidate.name.casefold(): candidate.name for candidate in parent.iterdir()}
            if len(self.directory_cache) >= 16:
                self.directory_cache.pop(next(iter(self.directory_cache)))
            self.directory_cache[parent] = (stamp, names)
        return parent / self.directory_cache[parent][1].get(name.casefold(), name)

    def _remember_destination(self, path: Path):
        cached = self.directory_cache.get(path.parent)
        if cached:
            cached[1][path.name.casefold()] = path.name
            self.directory_cache[path.parent] = (path.parent.stat().st_mtime_ns, cached[1])

    def _copy(self, job: dict, entry: dict, event: threading.Event) -> dict:
        source_root, target_root = Path(job["source"]), Path(job["target"])
        source = contained(source_root, entry["source_rel"])
        if not same_snapshot(source, entry):
            raise ValueError("源文件在扫描后发生变化，请重新扫描")
        intended = contained(target_root, entry["target_rel"])
        intended.parent.mkdir(parents=True, exist_ok=True)
        contained(target_root, intended.relative_to(target_root).as_posix())
        stem, suffix = intended.stem, intended.suffix
        source_hash = None
        source_extras, extra_issues = extra_signature(source, event)
        for index in range(100000):
            if event.is_set():
                raise Interrupted()
            name = intended.name if index == 0 else f"{stem}_{index:03d}{suffix}"
            destination = self._existing(intended.parent, name)
            if destination.exists() or is_link(destination):
                if not is_link(destination) and destination.is_file() and destination.stat().st_size == entry["size"]:
                    source_hash = source_hash or sha256(source, event)
                    destination_extras, destination_issues = extra_signature(destination, event)
                    if not extra_issues and not destination_issues and source_extras == destination_extras and sha256(destination, event) == source_hash:
                        if not same_snapshot(source, entry):
                            raise ValueError("源文件在内容比较期间发生变化")
                        return {"status": "reused", "target_rel": destination.relative_to(target_root).as_posix(), "source_hash": source_hash, "extra_hashes": source_extras, "extra_issues": [], "content_verified": False, "error": "", **property_report(entry, destination)}
                continue
            temp = contained(target_root, f".media-organizer/tmp/{job['id']}-{entry['id']}.part")
            temp.parent.mkdir(parents=True, exist_ok=True)
            digest, written, last_progress = hashlib.sha256(), 0, time.monotonic()
            self.store.update_job(job["id"], phase="正在复制文件", active_file_bytes=0)
            with source.open("rb") as src, temp.open("wb") as dst:
                while chunk := src.read(BLOCK):
                    if event.is_set():
                        raise Interrupted()
                    dst.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if time.monotonic() - last_progress >= 0.4:
                        self.store.update_job(job["id"], active_file_bytes=written)
                        last_progress = time.monotonic()
                dst.flush()
                os.fsync(dst.fileno())
            if written != entry["size"] or not same_snapshot(source, entry):
                temp.unlink(missing_ok=True)
                raise ValueError("源文件在复制期间发生变化")
            try:
                # Hard-link publication is atomic and never overwrites an existing name.
                os.link(temp, destination)
            except FileExistsError:
                continue
            except OSError:
                # Filesystems without hard links use exclusive creation, still never overwrite.
                try:
                    with destination.open("xb") as dst, temp.open("rb") as src:
                        while chunk := src.read(BLOCK):
                            if event.is_set():
                                raise Interrupted()
                            dst.write(chunk)
                        dst.flush()
                        os.fsync(dst.fileno())
                except FileExistsError:
                    continue
                except BaseException:
                    # This file was exclusively created by this invocation.
                    destination.unlink(missing_ok=True)
                    raise
            temp.unlink(missing_ok=True)
            self._remember_destination(destination)
            copied_extras, issues = copy_extras(source, destination, event)
            warnings = list(entry.get("warnings", []))
            try:
                shutil.copystat(source, destination, follow_symlinks=False)
                if destination.stat().st_mtime_ns != entry["mtime_ns"]:
                    warnings.append("目标文件系统的修改时间精度不同，原始时间已记录")
            except OSError as error:
                warnings.append(f"部分文件属性无法保留：{error}")
            warnings.extend(preserve_windows_properties(source, destination))
            return {"status": "copied", "target_rel": destination.relative_to(target_root).as_posix(), "source_hash": digest.hexdigest(), "extra_hashes": copied_extras, "extra_issues": extra_issues + issues, "warnings": list(dict.fromkeys(warnings)), "content_verified": False, "error": "", **property_report(entry, destination)}
        raise ValueError("同名文件数量过多，无法分配目标名称")

    def _run(self, job_id: str, verify_only: bool = False):
        job = self.store.job(job_id)
        event = self.events[job_id]
        target = Path(job["target"])
        target.mkdir(parents=True, exist_ok=True)
        if not verify_only:
            self.store.update_job(job_id, status="copying", phase="正在复制文件", processed_count=0, copied_bytes=0, safe_to_delete=False)
            count, copied_bytes = 0, 0
            for entry in self.store.iter_entries(job_id, "file"):
                if event.is_set():
                    raise Interrupted()
                self.store.update_entry(job_id, entry["id"], status="copying", content_verified=False)
                self.store.update_job(job_id, current_file=entry["source_rel"], active_file_bytes=0)
                try:
                    result = self._copy(job, entry, event)
                    self.store.update_entry(job_id, entry["id"], **result)
                    copied_bytes += entry["size"]
                except Interrupted:
                    self.store.update_entry(job_id, entry["id"], status="planned")
                    raise
                except Exception as error:
                    self.store.update_entry(job_id, entry["id"], status="failed", error=str(error))
                count += 1
                self.store.update_job(job_id, processed_count=count, copied_bytes=copied_bytes, active_file_bytes=0, current_file=entry["source_rel"])
        self.verify(job_id)

    def _source_changes(self, job: dict) -> tuple[int, list[str]]:
        source = Path(job["source"])
        originals = {entry["source_rel"]: (entry["kind"], entry.get("size", 0), entry.get("mtime_ns", 0)) for entry in self.store.iter_entries(job["id"])}
        changes, count = [], 0
        def add(message):
            nonlocal count
            count += 1
            if len(changes) < 200:
                changes.append(message)
        for path, relative, kind, st, error in walk(source):
            if self.events[job["id"]].is_set():
                raise Interrupted()
            old = originals.pop(relative, None)
            if old is None:
                add(f"新增：{relative}")
            elif kind != old[0] or (kind == "file" and (st.st_size != old[1] or st.st_mtime_ns != old[2])):
                add(f"变化：{relative}")
            elif kind == "error":
                add(f"无法复查：{relative}：{error}")
        for relative in originals:
            add(f"消失：{relative}")
        return count, changes

    def verify(self, job_id: str):
        job = self.store.update_job(job_id, status="verifying", phase="正在校验文件", verified_count=0, safe_to_delete=False)
        level, event = job["verification"], self.events[job_id]
        sample_ids = set()
        if level == "sampled":
            amount = max(1, math.ceil(job["file_count"] * 0.1))
            with self.store.connection() as db:
                sample_ids = {row[0] for row in db.execute("SELECT id FROM entries WHERE job_id=? AND kind='file' ORDER BY RANDOM() LIMIT ?", (job_id, amount))}
        verified, checked, issues, checked_bytes = 0, 0, 0, 0
        for entry in self.store.iter_entries(job_id, "file"):
            if event.is_set():
                raise Interrupted()
            checked += 1
            self.store.update_job(job_id, current_file=entry["source_rel"], active_verify_bytes=0)
            try:
                if entry["status"] not in ("copied", "reused", "verified"):
                    raise ValueError(entry.get("error") or "文件尚未成功复制")
                source = contained(Path(job["source"]), entry["source_rel"])
                target = contained(Path(job["target"]), entry["target_rel"])
                if not same_snapshot(source, entry):
                    raise ValueError("源文件在扫描后发生变化或消失")
                if not target.is_file() or target.stat().st_size != entry["size"]:
                    raise ValueError("目标文件缺失或大小不一致")
                target_before = target.stat()
                hash_check = level == "full" or entry["id"] in sample_ids
                updates = {"content_verified": False, "target_hash": "", "error": "", "sampled": entry["id"] in sample_ids}
                if hash_check:
                    source_hash = sha256(source, event, lambda amount: self.store.update_job(job_id, active_verify_bytes=amount // 2))
                    target_hash = sha256(target, event, lambda amount: self.store.update_job(job_id, active_verify_bytes=(entry["size"] + amount) // 2))
                    if source_hash != target_hash or source_hash != entry.get("source_hash"):
                        raise ValueError("SHA-256 不一致，源文件或目标文件内容发生变化")
                    source_extra, source_issues = extra_signature(source, event)
                    target_extra, target_issues = extra_signature(target, event)
                    if source_issues or target_issues or source_extra != target_extra or source_extra != entry.get("extra_hashes", {}):
                        raise ValueError("附加数据不一致或无法完整检查")
                    if not same_snapshot(source, entry):
                        raise ValueError("源文件在校验过程中发生变化")
                    updates.update(content_verified=True, target_hash=target_hash, status="verified")
                if entry.get("extra_issues"):
                    raise ValueError("；".join(entry["extra_issues"]))
                target_after = target.stat()
                if (target_before.st_size, target_before.st_mtime_ns, target_before.st_ctime_ns) != (target_after.st_size, target_after.st_mtime_ns, target_after.st_ctime_ns):
                    raise ValueError("目标文件在校验过程中发生变化")
                updates["target_snapshot"] = {"size": target_after.st_size, "mtime_ns": target_after.st_mtime_ns, "ctime_ns": target_after.st_ctime_ns}
                self.store.update_entry(job_id, entry["id"], **updates)
                if hash_check:
                    verified += 1
            except Interrupted:
                raise
            except Exception as error:
                issues += 1
                self.store.update_entry(job_id, entry["id"], status="failed", content_verified=False, error=str(error))
            checked_bytes += entry["size"]
            self.store.update_job(job_id, verified_count=verified, checked_count=checked, checked_bytes=checked_bytes, active_verify_bytes=0, current_file=entry["source_rel"])
        changes_count, changes = self._source_changes(job)
        # Recheck earlier targets after later files have finished hashing.
        for entry in self.store.iter_entries(job_id, "file"):
            snapshot = entry.get("target_snapshot")
            if entry["status"] == "failed" or not snapshot:
                continue
            if event.is_set():
                raise Interrupted()
            try:
                target = contained(Path(job["target"]), entry["target_rel"])
                current = target.stat()
                if snapshot != {"size": current.st_size, "mtime_ns": current.st_mtime_ns, "ctime_ns": current.st_ctime_ns}:
                    raise ValueError("目标文件在校验后发生变化，请重新校验")
            except (OSError, ValueError) as error:
                issues += 1
                if entry.get("content_verified"):
                    verified -= 1
                self.store.update_entry(job_id, entry["id"], status="failed", content_verified=False, error=str(error))
        stats = self.store.stats(job_id)
        unhandled = stats["statuses"].get("unhandled", 0)
        safe = level == "full" and verified == job["file_count"] and verified > 0 and issues == 0 and unhandled == 0 and changes_count == 0
        status = "completed_issues" if issues or unhandled or changes_count else "completed"
        self.store.update_job(job_id, phase="正在保存校验报告", verified_count=verified, issue_count=issues, unhandled_count=unhandled, safe_to_delete=False, changes_count=changes_count, changes=changes, stats=stats, validated_at=now(), current_file="")
        # The report must be durably written before exposing a deletion recommendation.
        self.report(job_id, safe, status)
        self.store.update_job(job_id, status=status, phase="校验完成，可以查看报告", safe_to_delete=safe)

    def report(self, job_id: str, safe: bool | None = None, status: str | None = None) -> Path:
        job = self.store.job(job_id)
        directory = contained(Path(job["target"]), f".media-organizer/reports/{job_id}")
        directory.mkdir(parents=True, exist_ok=True)
        contained(Path(job["target"]), f".media-organizer/reports/{job_id}/manifest.csv")
        columns = ["source_rel", "target_rel", "category", "month", "location", "size", "status", "date_source", "location_source", "source_hash", "target_hash", "content_verified", "original_date", "mtime_ns", "ctime_ns", "birthtime_ns", "mode", "uid", "gid", "target_mtime_ns", "target_birthtime_ns", "target_mode", "attribute_differences", "permissions_note", "manual", "warnings", "extra_issues", "error"]
        csv_path = directory / "manifest.csv"
        temporary = directory / "manifest.csv.part"
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for entry in self.store.iter_entries(job_id):
                row = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in entry.items()}
                # Spreadsheet formulas in user-supplied filenames are plain text in CSV.
                for key in ("source_rel", "target_rel", "error"):
                    text = str(row.get(key, ""))
                    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
                        row[key] = "'" + text
                writer.writerow(row)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, csv_path)
        report_path = directory / "report.json"
        summary = {**job, "status": status or job["status"], "report_path": str(directory), "safe_to_delete": safe if safe is not None else job.get("safe_to_delete", False), "scope": "普通文件内容、内嵌元数据、可读取的 Windows ADS / Linux user.* 扩展属性；不等同于文件系统或权限镜像备份", "deletion_notice": "仅针对本次校验时点；程序不会删除源目录。删除前请确认分类结果并避免继续向源目录写入。", "attribute_note": "创建时间、所有者、ACL、系统属性无法跨平台完整保证；原始时间已记入清单。"}
        with (directory / "report.json.part").open("w", encoding="utf-8") as output:
            json.dump(summary, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(directory / "report.json.part", report_path)
        self.store.update_job(job_id, report_path=str(directory))
        return directory

    def close(self):
        for event in self.events.values():
            event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.store.close()
        self.process_lock.close()
