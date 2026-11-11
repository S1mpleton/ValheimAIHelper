"""Build an offline-ready service using the locked runtime/build dependencies."""
from pathlib import Path
import importlib.metadata
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def collect_licenses():
    destination = ROOT / ".local/runtime-licenses"
    if destination.exists(): shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for package in ("fastapi", "httpx", "pydantic", "pydantic-core", "uvicorn", "starlette", "anyio", "certifi",
                    "h11", "httpcore", "idna", "click", "sniffio", "typing-extensions", "typing-inspection", "annotated-types", "pyinstaller"):
        try: distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError: continue
        for path in distribution.files or ():
            if any(term in path.name.lower() for term in ("license", "copying", "notice")) and path.suffix.lower() in ("", ".txt", ".md"):
                source = Path(distribution.locate_file(path))
                if source.is_file(): shutil.copyfile(source, destination / (package + "-" + path.name))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file(): shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    elif sys.platform == "win32": raise RuntimeError("Python redistribution license is missing from the build installation")
    return destination

if __name__ == "__main__":
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "ValheimAIBridge", "--paths", str(ROOT / "bridge/src"),
        "--add-data", str(ROOT / "bridge/src/valheim_ai/static") + ":valheim_ai/static",
        "--add-data", str(collect_licenses()) + ":licenses",
        "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.h11_impl", "--hidden-import", "uvicorn.lifespan.on",
        "--distpath", str(ROOT / ".local/frozen"), "--workpath", str(ROOT / ".local/freeze-build"),
        "--specpath", str(ROOT / ".local"), str(ROOT / "scripts/bridge-entry.py"),
    ], check=True, cwd=ROOT)
