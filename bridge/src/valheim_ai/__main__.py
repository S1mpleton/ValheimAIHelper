import argparse
import configparser
import os
from pathlib import Path

import uvicorn

from .app import create_app, load_token


def main():
    parser = argparse.ArgumentParser(description="Valheim AI local bridge")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".local"))
    parser.add_argument("--game-dir", type=Path, help="Pair the mod in this Valheim installation")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    token = load_token(args.data_dir)
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
    print(f"Pairing code file: {args.data_dir.resolve() / 'bridge-token'}")
    print("Open the local dashboard on this computer at port", args.port)
    print("API keys entered in the dashboard remain in memory until shutdown.")
    uvicorn.run(create_app(token=token), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
