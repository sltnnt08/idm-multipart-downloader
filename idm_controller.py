from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from config_loader import AppConfig
from url_generator import FilePart


class IDMController:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def _run_idm_command(self, command: list[str], action: str) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.config.request_timeout,
            creationflags=creation_flags,
        )

        command_text = subprocess.list2cmdline(command)
        logging.info("[IDM][%s] rc=%s cmd=%s", action, result.returncode, command_text)

        stdout_text = (result.stdout or "").strip()
        stderr_text = (result.stderr or "").strip()

        if stdout_text:
            logging.info("[IDM][%s][stdout] %s", action, stdout_text)
        if stderr_text:
            logging.warning("[IDM][%s][stderr] %s", action, stderr_text)

        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )

    @staticmethod
    def _resume_tokens(url: str) -> set[str]:
        parsed = urlparse(url)
        base_url = parsed._replace(fragment="").geturl().lower().strip()
        path_name = Path(unquote(parsed.path)).name.lower().strip()
        fragment_name = Path(unquote(parsed.fragment)).name.lower().strip()

        tokens = {base_url}
        if path_name:
            tokens.add(path_name)
        if fragment_name:
            tokens.add(fragment_name)

        return {token for token in tokens if token}

    def _load_idm_state_blob(self, max_file_size_bytes: int = 2 * 1024 * 1024) -> str:
        state_dir = Path(self.config.idm_state_dir)
        if not state_dir.exists() or not state_dir.is_dir():
            logging.warning("IDM state directory not found for resume validation: %s", state_dir)
            return ""

        chunks: list[str] = []

        for file_path in state_dir.rglob("*"):
            if not file_path.is_file():
                continue

            try:
                if file_path.stat().st_size > max_file_size_bytes:
                    continue
                data = file_path.read_bytes()
            except OSError:
                continue

            if not data:
                continue

            chunks.append(data.decode("latin-1", errors="ignore").lower())

        if not chunks:
            logging.warning("No readable IDM state files found in: %s", state_dir)
            return ""

        return "\n".join(chunks)

    def reconcile_resume_urls(self, resume_urls: set[str]) -> set[str]:
        if not resume_urls:
            return resume_urls

        state_blob = self._load_idm_state_blob()
        if not state_blob:
            return resume_urls

        kept: set[str] = set()
        for url in resume_urls:
            tokens = self._resume_tokens(url)
            if any(token in state_blob for token in tokens):
                kept.add(url)

        removed_count = len(resume_urls) - len(kept)
        if removed_count > 0:
            logging.warning(
                "Resume sync: removed %s URL(s) not found in IDM state (likely deleted from IDM queue/history).",
                removed_count,
            )

        return kept

    def validate_idm_paths(self) -> None:
        idm_path = Path(self.config.idm_path)
        shortcut_path = Path(self.config.idm_shortcut_path)

        if not idm_path.exists() or not idm_path.is_file():
            raise FileNotFoundError(f"IDM executable not found: {idm_path}")

        if self.config.launch_idm_shortcut and (not shortcut_path.exists() or not shortcut_path.is_file()):
            raise FileNotFoundError(f"IDM shortcut not found: {shortcut_path}")

    def queue_download(self, part: FilePart) -> None:
        command = [
            self.config.idm_path,
            "/d",
            part.url,
            "/p",
            self.config.download_path,
            "/f",
            part.filename,
            "/n",
        ]

        if self.config.queue_only:
            command.append("/a")

        self._run_idm_command(command, action="queue")

    def launch_via_shortcut(self) -> None:
        try:
            os.startfile(self.config.idm_shortcut_path)
            logging.info("IDM launched via shortcut: %s", self.config.idm_shortcut_path)
        except OSError as exc:
            logging.warning("Failed to launch IDM shortcut: %s", exc)

    def start_queue(self) -> None:
        command = [self.config.idm_path, "/s"]
        self._run_idm_command(command, action="start")
        logging.info("IDM queue started")
