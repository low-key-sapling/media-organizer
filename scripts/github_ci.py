"""Developer-only CI reader. Credentials stay in memory and are never printed."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import ssl
import sys
from pathlib import Path

import httpx


def token_for(owner: str) -> str | None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    try:
        result = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost=github.com\nusername={owner}\n\n", capture_output=True, text=True, timeout=20, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            return fields.get("password")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="low-key-sapling/media-organizer")
    parser.add_argument("--run", type=int)
    parser.add_argument("--logs", type=int, help="Job ID")
    parser.add_argument("--artifact", type=int)
    parser.add_argument("--artifacts", type=int, help="List artifacts for a workflow run")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sha")
    args = parser.parse_args()
    owner, repository = args.repo.split("/")
    if not owner.replace("-", "").isalnum() or not repository.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise SystemExit("Invalid repository")
    token = token_for(owner)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "media-organizer-build"}
    if token:
        headers["Authorization"] = "Bearer " + token
    endpoint = f"https://api.github.com/repos/{owner}/{repository}"
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    with httpx.Client(headers=headers, follow_redirects=True, timeout=40, trust_env=False, proxy=proxy, verify=ssl.create_default_context()) as client:
        if args.logs:
            url = f"{endpoint}/actions/jobs/{args.logs}/logs"
        elif args.artifacts:
            url = f"{endpoint}/actions/runs/{args.artifacts}/artifacts"
        elif args.artifact:
            url = f"{endpoint}/actions/artifacts/{args.artifact}/zip"
        elif args.run:
            url = f"{endpoint}/actions/runs/{args.run}/jobs"
        else:
            url = f"{endpoint}/actions/runs?per_page=5" + (f"&head_sha={args.sha}" if args.sha else "")
        response = client.get(url)
        if response.status_code >= 400:
            print(json.dumps({"status": response.status_code, "authenticated": bool(token), "message": "GitHub API access was unavailable; credentials are not logged"}))
            return 1
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(response.content)
            print(json.dumps({"saved": str(args.output), "bytes": len(response.content)}))
        elif args.logs:
            print(response.text)
        else:
            body = response.json()
            if args.artifacts:
                data = [{"id": item["id"], "name": item["name"], "size_in_bytes": item["size_in_bytes"], "expired": item["expired"]} for item in body.get("artifacts", [])]
            elif args.run:
                data = [{"id": item["id"], "name": item["name"], "status": item["status"], "conclusion": item["conclusion"], "html_url": item["html_url"], "steps": [{"name": step["name"], "status": step["status"], "conclusion": step["conclusion"]} for step in item.get("steps", [])]} for item in body.get("jobs", [])]
            else:
                data = [{"id": item["id"], "status": item["status"], "conclusion": item["conclusion"], "html_url": item["html_url"], "head_sha": item["head_sha"], "name": item["name"]} for item in body.get("workflow_runs", [])]
            print(json.dumps({"authenticated": bool(token), "items": data}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
