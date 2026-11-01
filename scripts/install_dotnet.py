"""Pinned .NET SDK for the Linux cloud workspace, verified against Microsoft SHA512."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "global.json").read_text())["sdk"]["version"]
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This installer is for Linux x86_64; on Windows install the SDK from Microsoft")
    destination = Path("/workspace/.valheim-tools/dotnet")
    executable = destination / "dotnet"
    if executable.is_file():
        installed = subprocess.run([str(executable), "--list-sdks"], check=True, capture_output=True, text=True).stdout
        if any(line.startswith(version + " ") for line in installed.splitlines()):
            print("Pinned SDK already installed:", version)
            return
    with urllib.request.urlopen("https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json", timeout=30) as response:
        metadata = json.load(response)
    sdks = [sdk for release in metadata["releases"] for sdk in release.get("sdks", [])]
    sdk = next(sdk for sdk in sdks if sdk["version"] == version)
    artifact = next(file for file in sdk["files"] if file["rid"] == "linux-x64" and file["name"].endswith(".tar.gz"))
    cache = Path("/workspace/.valheim-cache")
    cache.mkdir(exist_ok=True)
    archive = cache / f"dotnet-sdk-{version}.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(artifact["url"], timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    if hashlib.sha512(archive.read_bytes()).hexdigest().lower() != artifact["hash"].lower():
        raise RuntimeError(f"SDK checksum mismatch; remove {archive} before retrying")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as package:
        package.extractall(destination, filter="data")
    print("SDK installed with verified SHA512:", version)


if __name__ == "__main__":
    main()
