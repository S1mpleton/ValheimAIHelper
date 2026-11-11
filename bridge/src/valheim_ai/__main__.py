import argparse
import configparser
import os
from pathlib import Path
import threading

import uvicorn

from .app import create_app, load_token
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Valheim AI local bridge")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".local"))
    parser.add_argument("--game-dir", type=Path, help="Pair the mod in this Valheim installation")
    parser.add_argument("--parent-pid", type=int, help="Exit when the owning Valheim process closes (bundled Windows service)")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    token = load_token(args.data_dir)
    if args.parent_pid is not None:
        if os.name != "nt" or args.parent_pid <= 0:
            parser.error("parent-pid requires a valid Windows parent process")
        watch_parent(args.parent_pid)
    if args.game_dir:
        config_dir = args.game_dir / "BepInEx" / "config"
        if not (args.game_dir / "BepInEx" / "core" / "BepInEx.dll").is_file():
            parser.error("BepInEx is not installed at --game-dir")
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "dev.valheim.aihelper.cfg"
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        if path.exists():
            config.read(path, encoding="utf-8-sig")
        if not config.has_section("Bridge"):
            config.add_section("Bridge")
        config["Bridge"]["Token"] = token
        config["Bridge"]["Url"] = f"http://127.0.0.1:{args.port}"
        with path.open("w", encoding="utf-8") as file:
            config.write(file, space_around_delimiters=True)
        print("Mod paired. Restart Valheim if it is running.")
    print("Valheim AI Helper", __version__)
    print(f"Pairing code file: {args.data_dir.resolve() / 'bridge-token'}")
    print("In Valheim: Esc -> Character control (or F8).")
    print("Optional diagnostic dashboard port:", args.port)
    print("API keys entered in the game or dashboard remain in memory until shutdown.")
    uvicorn.run(create_app(token=token), host="127.0.0.1", port=args.port, access_log=False,
                loop="asyncio", http="h11", ws="none")


def watch_parent(pid: int):
    # Synchronize on the process handle, avoiding PID reuse after game shutdown.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        raise RuntimeError("Owning game process is unavailable")
    def wait():
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel.CloseHandle(handle)
        os._exit(0)
    threading.Thread(target=wait, name="game-lifetime", daemon=True).start()


if __name__ == "__main__":
    main()
