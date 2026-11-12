"""Create a ready-to-copy BepInEx plugin bundle with its own local runtime."""
import argparse
import hashlib
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", type=Path, default=root / "mod/ValheimAIHelper/bin/Release/net48/ValheimAIHelper.dll")
    parser.add_argument("--runtime", type=Path, help="PyInstaller Windows onedir directory")
    args = parser.parse_args()
    if not args.dll.is_file():
        parser.error("Build the plugin before packaging")
    version = ET.parse(root / "mod/ValheimAIHelper/ValheimAIHelper.csproj").findtext("PropertyGroup/Version")
    if not version:
        parser.error("Plugin version is missing")
    if not args.runtime or not (args.runtime / "ValheimAIBridge.exe").is_file():
        parser.error("Pass --runtime with the frozen Windows runtime (run scripts/freeze_bridge.py on Windows)")
    output = root / f"dist/windows/ValheimAIHelper-windows-{version}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [root / "README.md", root / "LICENSE", root / "bridge/requirements.txt", root / "scripts/start-windows.ps1", root / "scripts/start-bridge.ps1"]
    files += sorted((root / "docs").glob("*.md"))
    files += sorted(p for p in (root / "bridge/src/valheim_ai").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        plugin = "ValheimAIHelper/BepInEx/plugins/ValheimAIHelper/"
        archive.write(args.dll, plugin + "ValheimAIHelper.dll")
        for path in sorted(args.runtime.rglob("*")):
            if path.is_file(): archive.write(path, plugin + "runtime/" + path.relative_to(args.runtime).as_posix())
        for path in files:
            archive.write(path, "ValheimAIHelper/" + path.relative_to(root).as_posix())
        archive.writestr("ValheimAIHelper/INSTALL.txt", "Valheim AI Helper " + version + "\r\n\r\n"
                         "1. Close Valheim. Install BepInExPack Valheim if missing.\r\n"
                         "2. Remove old BepInEx/plugins/ValheimAIHelper.dll to avoid a duplicate plugin.\r\n"
                         "3. Copy this BepInEx folder into the Valheim game directory. Keep runtime and _internal folders.\r\n"
                         "4. Start Valheim, enter a world, press Esc -> Character control (F8).\r\n"
                         "The local AI service starts and stops with the game. No Python/PowerShell needed.\r\n"
                         "Choose Ollama (local), OpenAI or Gemini in the game. Cloud keys remain in memory.\r\n"
                         "Ollama: start it on this PC and install a local text model first. Default http://127.0.0.1:11434.\r\n"
                         "No API key needed for Ollama. Thinking is disabled (think=false). Models are not bundled.\r\n"
                         "Leader chat: address the character by name, e.g. Bjorn, pick up stones.\r\n"
                         "Ordinary follow-up messages are interpreted from context. F9: emergency stop.\r\n")
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(output)
    print("SHA256:", checksum)


if __name__ == "__main__":
    main()
