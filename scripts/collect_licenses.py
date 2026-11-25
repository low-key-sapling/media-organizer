from __future__ import annotations

import importlib.metadata
import shutil
from pathlib import Path


def collect(destination: Path, root: Path):
    directory = destination / "third-party-licenses"
    for distribution in importlib.metadata.distributions():
        package = distribution.metadata.get("Name", "unknown")
        for item in distribution.files or []:
            if "licenses" not in item.parts and not item.name.lower().startswith(("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file():
                target = directory / "python" / package / str(item).replace("..", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    modules = root / "frontend/node_modules"
    for source in modules.rglob("*"):
        if source.is_file() and source.name.lower().startswith(("license", "copying", "notice")) and source.suffix.lower() not in (".js", ".ts", ".map"):
            target = directory / "frontend" / source.relative_to(modules)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
