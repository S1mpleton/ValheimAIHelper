"""Create a Windows runtime archive containing our own code and plugin only."""
import argparse
import hashlib
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", type=Path, default=root / "mod/ValheimAIHelper/bin/Release/net48/ValheimAIHelper.dll")
    args = parser.parse_args()
    if not args.dll.is_file():
        parser.error("Build the plugin before packaging")
    output = root / "dist/ValheimAIHelper-windows-0.1.0.zip"
    output.parent.mkdir(exist_ok=True)
    files = [root / "README.md", root / "LICENSE", root / "bridge/requirements.txt", root / "scripts/start-windows.ps1"]
    files += sorted((root / "docs").glob("*.md"))
    files += sorted(p for p in (root / "bridge/src").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(args.dll, "ValheimAIHelper/plugin/ValheimAIHelper.dll")
        for path in files:
            archive.write(path, "ValheimAIHelper/" + path.relative_to(root).as_posix())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(output)
    print("SHA256:", checksum)


if __name__ == "__main__":
    main()
