from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


try:
    import yaml
except ImportError:  # Optional dependency; only needed for YAML config.
    yaml = None


INDEX_PLACEHOLDER = "{index}"
INDEX_RAW_PLACEHOLDER = "{index_raw}"


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    filename_pattern: str
    start_index: int
    end_index: int | None
    auto_detect_parts: bool
    padding: int
    min_size_mb: float
    max_part: int
    download_path: str
    idm_path: str
    idm_shortcut_path: str
    queue_only: bool
    auto_start_queue: bool
    request_timeout: int
    resume_mode: bool
    resume_state_file: str
    dry_run: bool
    log_file: str
    launch_idm_shortcut: bool
    verify_ssl: bool
    retry_count: int
    retry_backoff_seconds: float
    head_fallback_get: bool
    log_max_mb: int
    input_urls: list[str]
    validate_resume_with_idm: bool
    idm_state_dir: str
    require_rar_extension: bool
    reject_html_content: bool
    resolve_download_button_links: bool
    selenium_fallback_enabled: bool
    selenium_headless: bool
    existing_file_action: str

    @property
    def min_size_bytes(self) -> int:
        return int(self.min_size_mb * 1024 * 1024)


def _read_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        _create_default_config(config_path)
        raise FileNotFoundError(
            f"Config file not found. A template has been created at: {config_path}. "
            "Please edit it and run again."
        )

    extension = config_path.suffix.lower()
    content = config_path.read_text(encoding="utf-8")

    if extension == ".json":
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON format in {config_path} at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}"
            ) from exc

    if extension in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError(
                "PyYAML is not installed. Install it or use JSON config instead."
            )
        parsed = yaml.safe_load(content)
        return parsed if isinstance(parsed, dict) else {}

    raise ValueError("Unsupported config format. Use .json, .yaml, or .yml")


def _create_default_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)

    template = {
        "base_url": "https://example.com/downloads",
        "filename_pattern": f"archive.part{INDEX_PLACEHOLDER}.rar",
        "start_index": 1,
        "end_index": None,
        "auto_detect_parts": True,
        "padding": 3,
        "min_size_mb": 5,
        "max_part": 200,
        "download_path": "./downloads",
        "auto_detect": True,
        "idm_path": "",
        "idm_shortcut_path": "./IDMan.exe.lnk",
        "queue_only": True,
        "auto_start_queue": True,
        "request_timeout": 10,
        "retry_count": 2,
        "retry_backoff_seconds": 1.5,
        "head_fallback_get": True,
        "require_rar_extension": True,
        "reject_html_content": True,
        "resolve_download_button_links": True,
        "selenium_fallback_enabled": False,
        "selenium_headless": True,
        "existing_file_action": "ask",
        "resume_mode": True,
        "resume_state_file": "./resume_state.json",
        "dry_run": False,
        "log_file": "./log.txt",
        "log_max_mb": 10,
        "launch_idm_shortcut": True,
        "verify_ssl": True,
        "validate_resume_with_idm": True,
        "idm_state_dir": "%APPDATA%/IDM/DwnlData",
        "source_url": "",
        "paste_input": "",
        "input_urls": [],
        "input_ids": [],
        "id_url_template": "https://example.com/{id}",
    }

    config_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")


def _detect_idm_executable() -> str | None:
    candidates = [
        Path("C:/Program Files (x86)/Internet Download Manager/IDMan.exe"),
        Path("C:/Program Files/Internet Download Manager/IDMan.exe"),
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    return None


def _normalize_input_urls(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        lines = [line.strip() for line in value.splitlines()]
        return [line for line in lines if line]

    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                urls.append(item.strip())
        return urls

    raise ValueError("input_urls must be a list of URLs or multiline string")


def _normalize_input_ids(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        lines = [line.strip() for line in value.splitlines()]
        return [line for line in lines if line]

    if isinstance(value, list):
        ids: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                ids.append(item.strip())
        return ids

    raise ValueError("input_ids must be a list of IDs or multiline string")


def _normalize_paste_input(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        chunks = re.split(r"[\s,;]+", value.strip())
        return [chunk for chunk in chunks if chunk]

    if isinstance(value, list):
        tokens: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                tokens.append(item.strip())
        return tokens

    raise ValueError("paste_input must be a list or multiline string")


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _compose_urls_from_ids(input_ids: list[str], id_url_template: str) -> list[str]:
    if not input_ids:
        return []

    if not id_url_template or "{id}" not in id_url_template:
        raise ValueError("id_url_template must be provided and contain {id} when input_ids is used")

    return [id_url_template.format(id=identifier) for identifier in input_ids]


def _compose_urls_from_paste_tokens(tokens: list[str], id_url_template: str) -> list[str]:
    if not tokens:
        return []

    urls: list[str] = []
    for token in tokens:
        if _is_url(token):
            urls.append(token)
            continue

        if not id_url_template or "{id}" not in id_url_template:
            raise ValueError(
                "paste_input contains ID-like values. Provide id_url_template with {id}."
            )

        urls.append(id_url_template.format(id=token))

    return urls


def _derive_pattern_from_source_url(source_url: str) -> str:
    pattern = re.sub(
        r"part\d+",
        f"part{INDEX_PLACEHOLDER}",
        source_url,
        flags=re.IGNORECASE,
        count=1,
    )
    if pattern != source_url:
        return pattern

    pattern = re.sub(
        r"(\d+)(?=\.[A-Za-z0-9]{2,5}(?:$|[?#]))",
        INDEX_PLACEHOLDER,
        source_url,
        count=1,
    )
    return pattern


def _to_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean value for {field_name}: {value}")


def _resolve_path(base_dir: Path, value: str) -> str:
    expanded = os.path.expandvars(value)
    path = Path(expanded).expanduser()
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _validate_url_or_pattern(base_url: str, filename_pattern: str) -> None:
    parsed_base = urlparse(base_url)
    if not parsed_base.scheme or not parsed_base.netloc:
        raise ValueError("base_url must be an absolute URL, e.g. https://domain.com/path")

    if INDEX_PLACEHOLDER not in filename_pattern and INDEX_RAW_PLACEHOLDER not in filename_pattern:
        raise ValueError(
            f"filename_pattern must contain {INDEX_PLACEHOLDER} or {INDEX_RAW_PLACEHOLDER}"
        )


def _validate_config_values(config: AppConfig) -> None:
    if config.start_index < 0:
        raise ValueError("start_index must be >= 0")
    if config.end_index is not None and config.end_index < config.start_index:
        raise ValueError("end_index must be >= start_index")
    if config.padding < 1:
        raise ValueError("padding must be >= 1")
    if config.max_part < 1:
        raise ValueError("max_part must be >= 1")
    if config.request_timeout < 1:
        raise ValueError("request_timeout must be >= 1")
    if config.min_size_mb < 0:
        raise ValueError("min_size_mb must be >= 0")
    if config.retry_count < 0:
        raise ValueError("retry_count must be >= 0")
    if config.retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if config.log_max_mb < 1:
        raise ValueError("log_max_mb must be >= 1")
    if config.end_index is None and not config.auto_detect_parts:
        raise ValueError(
            "When end_index is null, auto_detect_parts must be true to avoid ambiguous range."
        )
    if config.existing_file_action not in {"ask", "skip", "overwrite"}:
        raise ValueError("existing_file_action must be one of: ask, skip, overwrite")


def load_config(config_path: str) -> AppConfig:
    config_file_path = Path(config_path).resolve()
    raw = _read_config_file(config_file_path)
    base_dir = config_file_path.parent

    source_url = str(raw.get("source_url", "")).strip()
    if source_url:
        raw.setdefault("base_url", source_url)
        raw.setdefault("filename_pattern", _derive_pattern_from_source_url(source_url))

    input_urls = _normalize_input_urls(raw.get("input_urls", raw.get("paste_urls", raw.get("urls"))))
    input_ids = _normalize_input_ids(raw.get("input_ids"))
    id_url_template = str(raw.get("id_url_template", "")).strip()
    paste_tokens = _normalize_paste_input(raw.get("paste_input", raw.get("paste")))

    if paste_tokens:
        input_urls = _compose_urls_from_paste_tokens(paste_tokens, id_url_template)
    elif input_ids:
        input_urls = _compose_urls_from_ids(input_ids, id_url_template)

    filename_pattern = raw.get("filename_pattern", raw.get("pattern", INDEX_PLACEHOLDER))
    auto_detect_parts = raw.get("auto_detect_parts", raw.get("auto_detect", True))

    raw_idm_path = str(raw.get("idm_path", "")).strip()
    if not raw_idm_path:
        detected = _detect_idm_executable()
        if detected is not None:
            raw_idm_path = detected

    config = AppConfig(
        base_url=str(raw.get("base_url", "https://example.com")),
        filename_pattern=str(filename_pattern),
        start_index=int(raw.get("start_index", 1)),
        end_index=(
            None
            if raw.get("end_index") is None
            else int(raw["end_index"])
        ),
        auto_detect_parts=_to_bool(auto_detect_parts, "auto_detect_parts"),
        padding=int(raw.get("padding", 3)),
        min_size_mb=float(raw.get("min_size_mb", 5)),
        max_part=int(raw.get("max_part", 200)),
        download_path=_resolve_path(base_dir, str(raw.get("download_path", "./downloads"))),
        idm_path=_resolve_path(base_dir, raw_idm_path) if raw_idm_path else "",
        idm_shortcut_path=_resolve_path(base_dir, str(raw.get("idm_shortcut_path", "./IDMan.exe.lnk"))),
        queue_only=_to_bool(raw.get("queue_only", True), "queue_only"),
        auto_start_queue=_to_bool(raw.get("auto_start_queue", True), "auto_start_queue"),
        request_timeout=int(raw.get("request_timeout", 10)),
        resume_mode=_to_bool(raw.get("resume_mode", True), "resume_mode"),
        resume_state_file=_resolve_path(base_dir, str(raw.get("resume_state_file", "./resume_state.json"))),
        dry_run=_to_bool(raw.get("dry_run", False), "dry_run"),
        log_file=_resolve_path(base_dir, str(raw.get("log_file", "./log.txt"))),
        launch_idm_shortcut=_to_bool(
            raw.get("launch_idm_shortcut", True),
            "launch_idm_shortcut",
        ),
        verify_ssl=_to_bool(raw.get("verify_ssl", True), "verify_ssl"),
        retry_count=int(raw.get("retry_count", 2)),
        retry_backoff_seconds=float(
            raw.get("retry_backoff_seconds", 1.5)
        ),
        head_fallback_get=_to_bool(
            raw.get("head_fallback_get", True),
            "head_fallback_get",
        ),
        log_max_mb=int(raw.get("log_max_mb", 10)),
        input_urls=input_urls,
        validate_resume_with_idm=_to_bool(
            raw.get("validate_resume_with_idm", True),
            "validate_resume_with_idm",
        ),
        idm_state_dir=_resolve_path(
            base_dir,
            str(raw.get("idm_state_dir", "%APPDATA%/IDM/DwnlData")),
        ),
        require_rar_extension=_to_bool(
            raw.get("require_rar_extension", True),
            "require_rar_extension",
        ),
        reject_html_content=_to_bool(
            raw.get("reject_html_content", True),
            "reject_html_content",
        ),
        resolve_download_button_links=_to_bool(
            raw.get("resolve_download_button_links", True),
            "resolve_download_button_links",
        ),
        selenium_fallback_enabled=_to_bool(
            raw.get("selenium_fallback_enabled", False),
            "selenium_fallback_enabled",
        ),
        selenium_headless=_to_bool(
            raw.get("selenium_headless", True),
            "selenium_headless",
        ),
        existing_file_action=str(raw.get("existing_file_action", "ask")).strip().lower(),
    )

    if not config.input_urls:
        _validate_url_or_pattern(config.base_url, config.filename_pattern)

    _validate_config_values(config)

    return config
