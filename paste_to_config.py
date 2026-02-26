from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Paste links/IDs into config.json as valid paste_input array"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config.json (default: config.json)",
    )
    parser.add_argument(
        "--text",
        help="Raw pasted text containing URLs/IDs",
    )
    parser.add_argument(
        "--file",
        help="Path to text file containing URLs/IDs",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read pasted content from STDIN",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Read pasted content from clipboard (default source if no source flag is set)",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Remove duplicate entries while preserving order",
    )
    return parser.parse_args()


def read_clipboard_text() -> str:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("Clipboard read requires tkinter, but it is not available.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tk.TclError as exc:
        raise RuntimeError("Clipboard is empty or unavailable.") from exc
    finally:
        root.destroy()


def read_raw_input(args: argparse.Namespace) -> str:
    if args.text:
        return args.text

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")
        return file_path.read_text(encoding="utf-8")

    if args.stdin:
        return sys.stdin.read()

    return read_clipboard_text()


def tokenize(raw: str) -> list[str]:
    chunks = re.split(r"[\s,;]+", raw.strip())
    return [chunk for chunk in chunks if chunk]


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def main() -> int:
    args = parse_args()

    try:
        config_path = Path(args.config)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw_text = read_raw_input(args)
        entries = tokenize(raw_text)
        if not entries:
            raise ValueError("No links/IDs found in the provided input.")

        if args.dedupe:
            entries = dedupe(entries)

        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config.json root must be an object")

        config["paste_input"] = entries

        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"Updated {config_path} with {len(entries)} item(s) in paste_input")
        return 0

    except Exception as exc:  # pylint: disable=broad-except
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
