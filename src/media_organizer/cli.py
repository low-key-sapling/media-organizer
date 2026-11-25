from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__
from .engine import Organizer
from .paths import CATEGORIES, LEVELS, state_directory


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    root = argparse.ArgumentParser(prog="media-organizer", description="媒体整理器 · 本地离线复制、归档与校验")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--state-dir", type=Path, help="任务数据库和配置目录")
    commands = root.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="启动本地网页界面（默认操作）")
    serve.add_argument("--port", type=int, default=8731)
    serve.add_argument("--no-browser", action="store_true")
    for name in ("scan", "run"):
        command = commands.add_parser(name, help="扫描预览" if name == "scan" else "扫描、复制并校验")
        command.add_argument("source")
        command.add_argument("target")
        command.add_argument("--verification", choices=LEVELS, default="full")
        if name == "run":
            command.add_argument("--yes", action="store_true", help="确认开始复制")
    for name in ("execute", "verify"):
        command = commands.add_parser(name)
        command.add_argument("job_id")
        command.add_argument("--verification", choices=LEVELS, default=None if name == "execute" else "full")
    commands.add_parser("jobs", help="任务历史")
    commands.add_parser("doctor", help="检查离线资源状态")
    files = commands.add_parser("files", help="分页查看文件清单")
    files.add_argument("job_id")
    files.add_argument("--page", type=int, default=1)
    files.add_argument("--search", default="")
    files.add_argument("--category", default="", choices=("", *CATEGORIES))
    edit = commands.add_parser("edit", help="修改预览中的分类")
    edit.add_argument("job_id")
    edit.add_argument("--ids", type=int, nargs="+", required=True)
    edit.add_argument("--category", choices=CATEGORIES)
    edit.add_argument("--month")
    edit.add_argument("--location")
    config = commands.add_parser("config", help="查看或导入导出配置")
    config.add_argument("--import", dest="import_path", type=Path)
    config.add_argument("--export", dest="export_path", type=Path)
    return root


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    directory = args.state_dir or state_directory()
    if not args.command or args.command == "serve":
        import uvicorn
        from .api import create_app
        port = getattr(args, "port", 8731)
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        print(f"媒体整理器 {__version__} · http://127.0.0.1:{port}\n关闭浏览器不会停止任务；退出程序可在下次启动时恢复。", flush=True)
        try:
            app = create_app(directory)
        except ValueError as error:
            endpoint_path = directory / "server.json"
            try:
                existing_port = int(json.loads(endpoint_path.read_text(encoding="utf-8"))["port"])
                if not 1 <= existing_port <= 65535:
                    raise ValueError("Invalid port")
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f"http://127.0.0.1:{existing_port}/api/meta", timeout=2) as response:
                    if not json.load(response).get("offline"):
                        raise ValueError("Not a media-organizer instance")
                if not getattr(args, "no_browser", False):
                    webbrowser.open(f"http://127.0.0.1:{existing_port}")
                print(f"已有实例运行：http://127.0.0.1:{existing_port}")
                return 0
            except (OSError, ValueError, KeyError):
                output({"error": str(error)})
                return 1
        (directory / "server.json").write_text(json.dumps({"port": port}), encoding="utf-8")
        if not getattr(args, "no_browser", False):
            timer = threading.Timer(1.2, webbrowser.open, args=[f"http://127.0.0.1:{port}"])
            timer.daemon = True
            timer.start()
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        return 0
    try:
        organizer = Organizer(directory)
    except ValueError as error:
        output({"error": str(error)})
        return 1
    try:
        if args.command == "doctor":
            output({"version": __version__, "metadata": str(organizer.reader.executable or "unavailable"), "places": organizer.places.info(), "state": str(directory)})
        elif args.command in ("scan", "run"):
            job = organizer.create(args.source, args.target, args.verification, background=False)
            if args.command == "run" and job["status"] == "ready":
                if not args.yes:
                    output({"job": job, "notice": "请先检查清单，再使用 execute 命令；或显式传入 --yes 开始复制"})
                    return 2
                job = organizer.execute(job["id"], background=False)
            output(job)
            return 0 if job["status"] in ("ready", "completed") else 1
        elif args.command in ("execute", "verify"):
            job = organizer.execute(args.job_id, args.verification, background=False, verify_only=args.command == "verify")
            output(job)
            return 0 if job["status"] == "completed" else 1
        elif args.command == "jobs":
            output(organizer.store.jobs())
        elif args.command == "files":
            output(organizer.store.entries(args.job_id, max(1, args.page), search=args.search, category=args.category))
        elif args.command == "edit":
            output({"updated": organizer.edit(args.job_id, args.ids, {key: getattr(args, key) for key in ("category", "month", "location")})})
        elif args.command == "config":
            config = organizer.settings()
            if args.import_path:
                config = organizer.save_settings(json.loads(args.import_path.read_text(encoding="utf-8-sig")))
            if args.export_path:
                args.export_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            output(config)
    except (OSError, ValueError) as error:
        output({"error": str(error)})
        return 1
    finally:
        organizer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
