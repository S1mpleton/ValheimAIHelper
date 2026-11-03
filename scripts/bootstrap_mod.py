"""Download only BepInEx compiler references; never installs a loader into a game."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.2/BepInEx_win_x64_5.4.23.2.zip"
# Pin the upstream release artifact downloaded over verified HTTPS.
SHA256 = "f752ce4e838f4c305b9da1404b6745f2cff23b8bfd494f79f0c84d0a01f59b46"


def main():
    cache = ROOT / ".local"
    cache.mkdir(exist_ok=True)
    archive = cache / "BepInEx_win_x64_5.4.23.2.zip"
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        try:
            # Default trust context and inherited proxy; no verification bypasses.
            with urllib.request.urlopen(URL, timeout=90) as response, partial.open("wb") as output:
                while data := response.read(1024 * 1024):
                    output.write(data)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError(f"Checksum mismatch: remove {archive} and retry; do NOT change the expected hash")
    destination = cache / "bepinex"
    # Extract only compiler references, not the loader, injector or other executables.
    with zipfile.ZipFile(archive) as package:
        for name in ["BepInEx/core/BepInEx.dll", "BepInEx/core/BepInEx.xml", "BepInEx/core/0Harmony.dll"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = package.read(name)
            if not target.exists() or target.read_bytes() != content:
                target.write_bytes(content)
    print("BepInEx compiler references verified:", destination / "BepInEx/core")


if __name__ == "__main__":
    main()
