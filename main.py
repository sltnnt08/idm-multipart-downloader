from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urlparse

from config_loader import AppConfig, load_config
from download_link_resolver import resolve_download_button_link
from idm_controller import IDMController
from url_generator import FilePart, GenerationReport, URLGenerator
from validator import URLValidator


class CliStyle:
    RESET = "\033[0m"
    INFO = "\033[96m"
    WARNING = "\033[93m"
    ERROR = "\033[91m"
    BOLD = "\033[1m"


def _print_colored(message: str, color: str) -> None:
    print(f"{color}{message}{CliStyle.RESET}")


def print_banner() -> None:
    _print_colored(
        "\n=== IDM Mass Multipart Downloader (CLI) ===",
        f"{CliStyle.INFO}{CliStyle.BOLD}",
    )


def setup_logging(log_file: str, log_max_mb: int) -> None:
    max_bytes = log_max_mb * 1024 * 1024
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler(log_file, mode="a", encoding="utf-8", maxBytes=max_bytes, backupCount=3),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_resume_state(state_file: str) -> set[str]:
    path = Path(state_file)
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        urls = payload.get("queued_urls", [])
        return {url for url in urls if isinstance(url, str)}
    except (json.JSONDecodeError, OSError):
        logging.warning("Failed to read resume state. Starting with empty state.")
        return set()


def save_resume_state(state_file: str, queued_urls: set[str]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "queued_urls": sorted(queued_urls),
    }
    state_path = Path(state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=state_path.parent,
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_file.write(json.dumps(payload, indent=2, ensure_ascii=False))
        temp_name = temp_file.name

    os.replace(temp_name, state_path)


def format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def ensure_download_path(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _filename_from_url(url: str, index: int) -> str:
    parsed = urlparse(url)

    fragment_name = Path(unquote(parsed.fragment)).name
    if fragment_name.lower().endswith(".rar"):
        return fragment_name

    name = Path(unquote(parsed.path)).name
    if name and "." in name:
        return name

    return f"file_{index}"


def _create_validator(config: AppConfig) -> URLValidator:
    return URLValidator(
        timeout=config.request_timeout,
        min_size_bytes=config.min_size_bytes,
        verify_ssl=config.verify_ssl,
        retry_count=config.retry_count,
        retry_backoff_seconds=config.retry_backoff_seconds,
        head_fallback_get=config.head_fallback_get,
        require_rar_extension=config.require_rar_extension,
        reject_html_content=config.reject_html_content,
    )


def _resolve_and_validate_input_url(
    config: AppConfig,
    index: int,
    url: str,
    validator: URLValidator | None = None,
) -> FilePart | None:
    active_validator = validator or _create_validator(config)
    resolved = resolve_download_button_link(
        url=url,
        timeout=config.request_timeout,
        verify_ssl=config.verify_ssl,
        enabled=config.resolve_download_button_links,
        selenium_fallback_enabled=config.selenium_fallback_enabled,
        selenium_headless=config.selenium_headless,
    )
    target_url = resolved.resolved_url

    if resolved.was_resolved:
        logging.info("[RESOLVED] %s -> %s", url, target_url)
    elif config.resolve_download_button_links:
        logging.info("[RESOLVE SKIP] %s | %s", url, resolved.reason)

    result = active_validator.validate(target_url)
    size_bytes = result.size_bytes
    if not result.is_valid:
        allow_unknown_size = (
            result.status_code in {200, 206}
            and result.size_bytes == 0
            and result.reason.startswith("File too small")
        )
        if allow_unknown_size:
            logging.warning(
                "[INPUT URL SIZE UNKNOWN] %s | Proceeding with IDM queue (size not provided by server)",
                target_url,
            )
            size_bytes = 0
        else:
            logging.warning("[INPUT URL INVALID] %s | %s", target_url, result.reason)
            return None

    return FilePart(
        index=index,
        url=target_url,
        filename=_filename_from_url(url, index=index),
        size_bytes=size_bytes,
        http_status=getattr(result, "status_code", None),
    )


def build_report_from_input_urls(config: AppConfig, validator: URLValidator) -> GenerationReport:
    checked = len(config.input_urls)
    if checked == 0:
        return GenerationReport(parts=[], examined_count=0, stop_reason="input_urls processed", stop_index=None)

    worker_limit = getattr(config, "worker_count", 1)
    if not isinstance(validator, URLValidator):
        worker_limit = 1

    max_workers = min(worker_limit, checked)
    ordered_parts: dict[int, FilePart] = {}

    if max_workers == 1:
        for index, url in enumerate(config.input_urls, start=1):
            part = _resolve_and_validate_input_url(
                config=config,
                index=index,
                url=url,
                validator=validator,
            )
            if part is not None:
                ordered_parts[index] = part
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="input") as executor:
            futures = {
                executor.submit(_resolve_and_validate_input_url, config, index, url): index
                for index, url in enumerate(config.input_urls, start=1)
            }
            for future in as_completed(futures):
                index = futures[future]
                part = future.result()
                if part is not None:
                    ordered_parts[index] = part

    parts = [ordered_parts[index] for index in sorted(ordered_parts)]
    stop_reason = f"input_urls processed ({max_workers} worker(s))"
    stop_index = checked

    return GenerationReport(
        parts=parts,
        examined_count=checked,
        stop_reason=stop_reason,
        stop_index=stop_index,
    )


def _generate_report(config: AppConfig, validator: URLValidator) -> GenerationReport:
    if config.input_urls:
        logging.info("Using input_urls mode with %s URL(s)", len(config.input_urls))
        return build_report_from_input_urls(config, validator)

    generator = URLGenerator(config=config, validator=validator)
    return generator.generate()


def _post_queue_actions(
    config: AppConfig,
    dry_run: bool,
    queued_count: int,
    interrupted: bool,
    controller: IDMController,
) -> None:
    if interrupted:
        _print_colored("[!] Process interrupted by user (Ctrl+C).", CliStyle.WARNING)
        return

    if not config.auto_start_queue or dry_run or queued_count <= 0:
        return

    if not config.queue_only:
        logging.warning(
            "auto_start_queue is ignored because queue_only=false (downloads were started immediately)."
        )
        return

    if config.launch_idm_shortcut:
        controller.launch_via_shortcut()
    controller.start_queue()


def _should_skip_part(part: FilePart, seen_urls: set[str], resume_urls: set[str]) -> bool:
    if part.url in seen_urls:
        logging.info("[SKIP][DUPLICATE] %s", part.url)
        return True

    seen_urls.add(part.url)

    if part.url in resume_urls:
        logging.info("[SKIP][RESUME] %s", part.url)
        return True

    return False


def _queue_or_dry_run(part: FilePart, controller: IDMController, dry_run: bool) -> bool:
    if dry_run:
        logging.info("[DRY-RUN] Would queue: %s", part.url)
        return True

    try:
        controller.queue_download(part)
        logging.info("[QUEUED] %s", part.url)
        return True
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.output or "").strip()
        logging.error("IDM returned non-zero exit code for URL: %s | rc=%s", part.url, exc.returncode)
        if stderr:
            logging.error("IDM stderr: %s", stderr)
        if stdout:
            logging.error("IDM stdout: %s", stdout)
    except subprocess.TimeoutExpired:
        logging.error("Timeout while queueing URL: %s", part.url)
    except PermissionError:
        logging.error("Permission denied when running IDM for URL: %s", part.url)
    except FileNotFoundError:
        logging.error("IDM executable not found when queueing URL: %s", part.url)
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("Failed to queue URL %s. Error: %s", part.url, exc)

    return False


def _prompt_existing_file_action(file_path: Path) -> tuple[str, str | None]:
    while True:
        _print_colored(
            f"[FILE EXISTS] {file_path}",
            CliStyle.WARNING,
        )
        _print_colored(
            "Pilih aksi: [s]kip sekali, [o]verwrite sekali, s[k]ip all, [a]ll overwrite",
            CliStyle.WARNING,
        )

        try:
            choice = input("Masukkan pilihan (s/o/k/a): ").strip().lower()
        except EOFError:
            return "skip", None

        if choice == "s":
            return "skip", None
        if choice == "o":
            return "overwrite", None
        if choice == "k":
            return "skip", "skip"
        if choice == "a":
            return "overwrite", "overwrite"

        _print_colored("Pilihan tidak valid. Coba lagi.", CliStyle.ERROR)


def _handle_existing_local_file(
    part: FilePart,
    controller: IDMController,
    dry_run: bool,
    sticky_action: str | None,
) -> tuple[bool, str | None]:
    target_path = Path(controller.config.download_path) / part.filename
    if not target_path.exists():
        return False, sticky_action

    configured_action = controller.config.existing_file_action
    effective_action = sticky_action or configured_action

    if effective_action == "ask":
        effective_action, sticky_update = _prompt_existing_file_action(target_path)
    else:
        sticky_update = sticky_action

    if effective_action == "skip":
        logging.warning("[SKIP][EXISTS] %s", target_path)
        return True, sticky_update

    if not dry_run:
        try:
            if target_path.is_file():
                target_path.unlink()
                logging.warning("[OVERWRITE][EXISTS] Existing file removed: %s", target_path)
            else:
                logging.error("[SKIP][EXISTS] Target exists but is not a file: %s", target_path)
                return True, sticky_update
        except OSError as exc:
            logging.error("[SKIP][EXISTS] Failed to remove existing file %s: %s", target_path, exc)
            return True, sticky_update

    return False, sticky_update


def queue_parts(
    controller: IDMController,
    parts: list[FilePart],
    resume_urls: set[str],
    dry_run: bool,
) -> tuple[int, int, set[str], bool]:
    queued_count = 0
    skipped_count = 0
    updated_resume = set(resume_urls)
    seen_urls: set[str] = set()
    interrupted = False
    sticky_existing_action: str | None = None
    queue_candidates: list[FilePart] = []

    try:
        for part in parts:
            if _should_skip_part(part, seen_urls=seen_urls, resume_urls=resume_urls):
                skipped_count += 1
                continue

            should_skip_existing, sticky_existing_action = _handle_existing_local_file(
                part=part,
                controller=controller,
                dry_run=dry_run,
                sticky_action=sticky_existing_action,
            )
            if should_skip_existing:
                skipped_count += 1
                continue

            queue_candidates.append(part)

        worker_limit = getattr(controller.config, "worker_count", 1)
        max_workers = min(worker_limit, len(queue_candidates)) if queue_candidates else 0
        if max_workers <= 1:
            for part in queue_candidates:
                if _queue_or_dry_run(
                    part=part,
                    controller=controller,
                    dry_run=dry_run,
                ):
                    queued_count += 1
                    updated_resume.add(part.url)
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="idm") as executor:
                futures = {
                    executor.submit(_queue_or_dry_run, part, controller, dry_run): part
                    for part in queue_candidates
                }
                for future in as_completed(futures):
                    part = futures[future]
                    if future.result():
                        queued_count += 1
                        updated_resume.add(part.url)
    except KeyboardInterrupt:
        interrupted = True
        logging.warning("Interrupted by user while queueing. Saving partial progress.")

    return queued_count, skipped_count, updated_resume, interrupted


def _queue_status_label(dry_run: bool, queued_count: int, skipped_count: int) -> str:
    if dry_run:
        return "DRY-RUN"
    if queued_count > 0:
        return "READY IN IDM QUEUE"
    if skipped_count > 0:
        return "NO NEW QUEUE (SKIPPED)"
    return "NOT QUEUED"


def _http_status_summary(parts: list[FilePart]) -> str:
    if not parts:
        return "-"

    counts: dict[str, int] = {}
    for part in parts:
        key = str(part.http_status) if part.http_status is not None else "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: item[0])
    return ", ".join(f"{code}:{count}" for code, count in ordered)


def print_summary(
    report: GenerationReport,
    queued_count: int,
    skipped_count: int,
    dry_run: bool,
) -> None:
    total_size = sum(part.size_bytes for part in report.parts)
    status = _queue_status_label(dry_run=dry_run, queued_count=queued_count, skipped_count=skipped_count)
    http_status = _http_status_summary(report.parts)

    print("\n===== SUMMARY =====")
    print(f"Total files detected : {len(report.parts)}")
    print(f"Total URLs checked   : {report.examined_count}")
    print(f"Total size estimate  : {format_size(total_size)}")
    print(f"Queued files         : {queued_count}")
    print(f"Skipped (resume/file): {skipped_count}")
    print(f"Stop reason          : {report.stop_reason}")
    print(f"Stop index           : {report.stop_index if report.stop_index is not None else '-'}")
    print(f"Queue status         : {status} | HTTP {http_status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mass multipart downloader URL queue for IDM")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to external config file (JSON/YAML)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Override config and only generate/validate URLs",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Override config and ignore resume state",
    )
    return parser.parse_args()


def run(config: AppConfig, dry_run: bool, resume_mode: bool) -> None:
    if not dry_run:
        ensure_download_path(config.download_path)

    validator = _create_validator(config)
    report = _generate_report(config, validator)
    parts = report.parts

    if not parts:
        logging.warning("No valid parts detected. Nothing to queue.")
        print_summary(report, queued_count=0, skipped_count=0, dry_run=dry_run)
        return

    controller = IDMController(config)

    resume_urls = load_resume_state(config.resume_state_file) if resume_mode else set()

    if not dry_run:
        controller.validate_idm_paths()
        if resume_mode and config.validate_resume_with_idm:
            resume_urls = controller.reconcile_resume_urls(resume_urls)

    queued_count, skipped_count, updated_resume, interrupted = queue_parts(
        controller=controller,
        parts=parts,
        resume_urls=resume_urls,
        dry_run=dry_run,
    )

    if not dry_run and queued_count == 0:
        if skipped_count > 0 and resume_mode:
            logging.warning(
                "No new queue created (%s skipped). Skips can come from resume state and/or existing local file policy.",
                skipped_count,
            )
        elif len(parts) > 0:
            logging.warning(
                "No URL was queued to IDM despite valid parts. Check IDM command diagnostics in log and verify links are direct-download URLs."
            )

    if resume_mode and not dry_run:
        save_resume_state(config.resume_state_file, updated_resume)

    _post_queue_actions(
        config=config,
        dry_run=dry_run,
        queued_count=queued_count,
        interrupted=interrupted,
        controller=controller,
    )

    print_summary(report, queued_count, skipped_count, dry_run)


def main() -> int:
    args = parse_args()
    print_banner()

    try:
        config: AppConfig = load_config(args.config)
        setup_logging(config.log_file, config.log_max_mb)

        dry_run = args.dry_run or config.dry_run
        resume_mode = (not args.no_resume) and config.resume_mode

        run(config=config, dry_run=dry_run, resume_mode=resume_mode)
        return 0

    except FileNotFoundError as exc:
        _print_colored(f"INFO: {exc}", CliStyle.WARNING)
        return 0
    except PermissionError as exc:
        logging.exception("Permission error")
        _print_colored(f"ERROR: Permission denied: {exc}", CliStyle.ERROR)
        return 1
    except KeyError as exc:
        logging.exception("Invalid config")
        _print_colored(f"ERROR: Invalid config: {exc}", CliStyle.ERROR)
        return 1
    except ValueError as exc:
        logging.exception("Validation error")
        _print_colored(f"ERROR: {exc}", CliStyle.ERROR)
        return 1
    except KeyboardInterrupt:
        _print_colored("INFO: Interrupted by user.", CliStyle.WARNING)
        return 1
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("Unhandled error")
        _print_colored(f"ERROR: {exc}", CliStyle.ERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
