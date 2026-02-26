from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import main
from download_link_resolver import resolve_download_button_link
from config_loader import load_config
from idm_controller import IDMController
from url_generator import FilePart
from url_generator import URLGenerator
from validator import URLValidator


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.headers = headers


class _FakeSession:
    def __init__(self) -> None:
        self.head_called = 0
        self.get_called = 0

    def head(self, *_args, **_kwargs):
        self.head_called += 1
        return _FakeResponse(200, {})

    def get(self, *_args, **_kwargs):
        self.get_called += 1
        return _FakeResponse(206, {"Content-Range": "bytes 0-0/10485760"})


class _FakeTinyProbeSession:
    def __init__(self) -> None:
        self.head_called = 0
        self.get_called = 0

    def head(self, *_args, **_kwargs):
        self.head_called += 1
        return _FakeResponse(200, {})

    def get(self, *_args, **_kwargs):
        self.get_called += 1
        return _FakeResponse(206, {"Content-Range": "bytes 0-0/1", "Content-Length": "1"})


class _FakeHtmlSession:
    @staticmethod
    def head(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return _FakeResponse(200, {"Content-Type": "text/html; charset=utf-8", "Content-Length": "4096"})


class _FakeNonRarSession:
    @staticmethod
    def head(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return _FakeResponse(200, {"Content-Type": "application/octet-stream", "Content-Length": "2097152"})


class _ValidatorStub:
    def __init__(self, outcomes: dict[int, tuple[bool, int, str]]) -> None:
        self.outcomes = outcomes

    def validate(self, url: str):
        index_str = url.split("part")[-1].split(".")[0]
        index = int(index_str)
        valid, size, reason = self.outcomes.get(index, (False, 0, "HTTP 404"))
        return SimpleNamespace(is_valid=valid, size_bytes=size, reason=reason)


class CoreTests(unittest.TestCase):
    @staticmethod
    def _base_config() -> dict[str, object]:
        return {
            "base_url": "https://example.com",
            "filename_pattern": "file.part{index}.rar",
            "start_index": 1,
            "end_index": 2,
            "auto_detect_parts": False,
            "padding": 3,
            "min_size_mb": 1,
            "max_part": 10,
            "download_path": "downloads",
            "idm_path": "IDMan.exe",
            "idm_shortcut_path": "IDMan.exe.lnk",
            "queue_only": True,
            "auto_start_queue": True,
            "request_timeout": 10,
            "retry_count": 1,
            "retry_backoff_seconds": 1,
            "head_fallback_get": True,
            "resume_mode": True,
            "resume_state_file": "resume_state.json",
            "dry_run": False,
            "log_file": "log.txt",
            "log_max_mb": 5,
            "launch_idm_shortcut": True,
            "verify_ssl": True,
            "validate_resume_with_idm": True,
            "idm_state_dir": "%APPDATA%/IDM/DwnlData",
            "resolve_download_button_links": True,
            "selenium_fallback_enabled": False,
            "selenium_headless": True,
            "existing_file_action": "ask",
        }

    def test_config_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            config_file.write_text(json.dumps(self._base_config()), encoding="utf-8")

            config = load_config(str(config_file))
            self.assertTrue(Path(config.download_path).is_absolute())
            self.assertTrue(Path(config.log_file).is_absolute())
            self.assertTrue(Path(config.resume_state_file).is_absolute())

    def test_missing_config_creates_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "new_config.json"

            with self.assertRaises(FileNotFoundError):
                load_config(str(config_file))

            self.assertTrue(config_file.exists())
            created = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertIn("base_url", created)
            self.assertIn("filename_pattern", created)

    def test_idm_path_auto_detect_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["idm_path"] = ""
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            with patch("config_loader._detect_idm_executable", return_value="C:/Program Files/Internet Download Manager/IDMan.exe"):
                config = load_config(str(config_file))

            self.assertEqual(
                Path(config.idm_path),
                Path("C:/Program Files/Internet Download Manager/IDMan.exe"),
            )

    def test_validator_uses_get_fallback_when_head_lacks_size(self) -> None:
        validator = URLValidator(
            timeout=5,
            min_size_bytes=1024,
            verify_ssl=False,
            retry_count=0,
            retry_backoff_seconds=0,
            head_fallback_get=True,
            require_rar_extension=True,
            reject_html_content=True,
        )
        fake_session = _FakeSession()
        validator.session = fake_session  # type: ignore[assignment]

        result = validator.validate("https://example.com/archive.part001.rar")
        self.assertTrue(result.is_valid)
        self.assertEqual(fake_session.head_called, 1)
        self.assertEqual(fake_session.get_called, 1)

    def test_validator_treats_one_byte_probe_as_unknown_size(self) -> None:
        validator = URLValidator(
            timeout=5,
            min_size_bytes=1024,
            verify_ssl=False,
            retry_count=0,
            retry_backoff_seconds=0,
            head_fallback_get=True,
            require_rar_extension=False,
            reject_html_content=True,
        )
        fake_session = _FakeTinyProbeSession()
        validator.session = fake_session  # type: ignore[assignment]

        result = validator.validate("https://example.com/dl/token")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, 206)
        self.assertEqual(result.size_bytes, 0)
        self.assertIn("File too small", result.reason)

    def test_validator_rejects_html_landing_page(self) -> None:
        validator = URLValidator(
            timeout=5,
            min_size_bytes=1,
            verify_ssl=False,
            retry_count=0,
            retry_backoff_seconds=0,
            head_fallback_get=False,
            require_rar_extension=True,
            reject_html_content=True,
        )

        class _Session:
            @staticmethod
            def head(*_args, **_kwargs):
                return _FakeResponse(
                    200,
                    {"Content-Type": "text/html; charset=utf-8", "Content-Length": "4096"},
                )

        validator.session = _Session()  # type: ignore[assignment]
        result = validator.validate("https://example.com/landing")
        self.assertFalse(result.is_valid)
        self.assertIn("Landing page/HTML", result.reason)

    def test_validator_rejects_non_rar_target(self) -> None:
        validator = URLValidator(
            timeout=5,
            min_size_bytes=1,
            verify_ssl=False,
            retry_count=0,
            retry_backoff_seconds=0,
            head_fallback_get=False,
            require_rar_extension=True,
            reject_html_content=True,
        )

        class _Session:
            @staticmethod
            def head(*_args, **_kwargs):
                return _FakeResponse(
                    200,
                    {"Content-Type": "application/octet-stream", "Content-Length": "2097152"},
                )

        validator.session = _Session()  # type: ignore[assignment]
        result = validator.validate("https://example.com/download/abc123")
        self.assertFalse(result.is_valid)
        self.assertIn("not recognized as RAR", result.reason)

    def test_generator_stops_on_first_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["end_index"] = None
            payload["auto_detect_parts"] = True
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            config = load_config(str(config_file))
            validator = _ValidatorStub(
                {
                    1: (True, 10_000_000, "OK"),
                    2: (True, 10_000_000, "OK"),
                    3: (False, 0, "HTTP 404"),
                }
            )
            generator = URLGenerator(config=config, validator=validator)  # type: ignore[arg-type]
            report = generator.generate()

            self.assertEqual(len(report.parts), 2)
            self.assertEqual(report.examined_count, 3)
            self.assertEqual(report.stop_reason, "HTTP 404")
            self.assertEqual(report.stop_index, 3)

    def test_queue_parts_handles_keyboard_interrupt(self) -> None:
        part = FilePart(
            index=1,
            url="https://example.com/file.part001.rar",
            filename="file.part001.rar",
            size_bytes=1024,
        )
        controller = cast(
            IDMController,
            SimpleNamespace(
                config=SimpleNamespace(
                    download_path=tempfile.gettempdir(),
                    existing_file_action="skip",
                )
            ),
        )

        with patch("main._queue_or_dry_run", side_effect=KeyboardInterrupt):
            queued, skipped, updated, interrupted = main.queue_parts(
                controller=controller,
                parts=[part],
                resume_urls=set(),
                dry_run=False,
            )

        self.assertEqual(queued, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(updated, set())
        self.assertTrue(interrupted)

    def test_queue_parts_skips_when_existing_file_and_policy_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            existing_file = Path(temp_dir) / "file.part001.rar"
            existing_file.write_text("already-here", encoding="utf-8")

            part = FilePart(
                index=1,
                url="https://example.com/file.part001.rar",
                filename="file.part001.rar",
                size_bytes=1024,
            )
            controller = cast(
                IDMController,
                SimpleNamespace(
                    config=SimpleNamespace(
                        download_path=temp_dir,
                        existing_file_action="skip",
                    )
                ),
            )

            queued, skipped, updated, interrupted = main.queue_parts(
                controller=controller,
                parts=[part],
                resume_urls=set(),
                dry_run=False,
            )

            self.assertEqual(queued, 0)
            self.assertEqual(skipped, 1)
            self.assertEqual(updated, set())
            self.assertFalse(interrupted)

    def test_input_urls_multiline_string_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["input_urls"] = "https://a.test/1\n\nhttps://b.test/2\n"
            payload["base_url"] = ""
            payload["filename_pattern"] = ""
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            config = load_config(str(config_file))
            self.assertEqual(config.input_urls, ["https://a.test/1", "https://b.test/2"])

    def test_input_ids_are_converted_to_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["input_urls"] = []
            payload["input_ids"] = ["abc", "xyz"]
            payload["id_url_template"] = "https://host.test/{id}"
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            config = load_config(str(config_file))
            self.assertEqual(config.input_urls, ["https://host.test/abc", "https://host.test/xyz"])

    def test_input_ids_require_template_with_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["input_ids"] = ["abc"]
            payload["id_url_template"] = "https://host.test/no-placeholder"
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_config(str(config_file))

    def test_paste_input_auto_detects_mixed_ids_and_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["paste_input"] = "abc123\nhttps://host.test/direct-url"
            payload["id_url_template"] = "https://host.test/{id}"
            payload["input_urls"] = []
            payload["input_ids"] = []
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            config = load_config(str(config_file))
            self.assertEqual(
                config.input_urls,
                ["https://host.test/abc123", "https://host.test/direct-url"],
            )

    def test_paste_input_supports_multiple_separators(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["paste_input"] = "abc123,def456;https://host.test/u1\thttps://host.test/u2"
            payload["id_url_template"] = "https://host.test/{id}"
            payload["input_urls"] = []
            payload["input_ids"] = []
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            config = load_config(str(config_file))
            self.assertEqual(
                config.input_urls,
                [
                    "https://host.test/abc123",
                    "https://host.test/def456",
                    "https://host.test/u1",
                    "https://host.test/u2",
                ],
            )

    def test_paste_input_ids_require_id_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["paste_input"] = "abc123"
            payload["id_url_template"] = ""
            payload["input_urls"] = []
            payload["input_ids"] = []
            config_file.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_config(str(config_file))

    def test_build_report_from_input_urls_keeps_only_valid_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.json"
            payload = self._base_config()
            payload["input_urls"] = ["https://ok.test/file1.rar", "https://bad.test/file2.rar"]
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            config = load_config(str(config_file))

            class _Validator:
                @staticmethod
                def validate(url: str):
                    if "ok.test" in url:
                        return SimpleNamespace(
                            is_valid=True,
                            status_code=200,
                            size_bytes=1234,
                            reason="OK",
                        )
                    return SimpleNamespace(
                        is_valid=False,
                        status_code=404,
                        size_bytes=0,
                        reason="HTTP 404",
                    )

            report = main.build_report_from_input_urls(
                config,
                cast(URLValidator, _Validator()),
            )
            self.assertEqual(report.examined_count, 2)
            self.assertEqual(len(report.parts), 1)
            self.assertEqual(report.parts[0].url, "https://ok.test/file1.rar")

    def test_filename_uses_fragment_rar_when_available(self) -> None:
        filename = main._filename_from_url(
            "https://fuckingfast.co/6rvvnzxyknbr#Death_Stranding.part040.rar",
            index=40,
        )
        self.assertEqual(filename, "Death_Stranding.part040.rar")

    def test_queue_status_label_reflects_resume_skips(self) -> None:
        status = main._queue_status_label(dry_run=False, queued_count=0, skipped_count=92)
        self.assertEqual(status, "NO NEW QUEUE (SKIPPED)")

    def test_reconcile_resume_urls_removes_deleted_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / "IDM" / "DwnlData"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "state.txt").write_text(
                "https://fuckingfast.co/keepme\nkeep_file.part001.rar",
                encoding="utf-8",
            )

            config_file = root / "config.json"
            payload = self._base_config()
            payload["idm_state_dir"] = str(state_dir)
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            config = load_config(str(config_file))

            controller = IDMController(config)
            resume_urls = {
                "https://fuckingfast.co/keepme#keep_file.part001.rar",
                "https://fuckingfast.co/deleteme#delete_file.part002.rar",
            }

            reconciled = controller.reconcile_resume_urls(resume_urls)
            self.assertEqual(
                reconciled,
                {"https://fuckingfast.co/keepme#keep_file.part001.rar"},
            )

    def test_resolver_extracts_window_open_download_link(self) -> None:
        html = '''
            <html>
                <script>
                    function download(){
                        window.open("https://fuckingfast.co/dl/abc123TOKEN");
                    }
                </script>
            </html>
        '''

        class _Resp:
            status_code = 200
            url = "https://fuckingfast.co/6rvvnzxyknbr"
            headers = {"Content-Type": "text/html; charset=utf-8"}
            text = html

        with patch("download_link_resolver.requests.get", return_value=_Resp()):
            result = resolve_download_button_link(
                "https://fuckingfast.co/6rvvnzxyknbr#file.part001.rar",
                timeout=10,
                verify_ssl=True,
                enabled=True,
                selenium_fallback_enabled=False,
                selenium_headless=True,
            )

        self.assertTrue(result.was_resolved)
        self.assertEqual(result.resolved_url, "https://fuckingfast.co/dl/abc123TOKEN")

    def test_resolver_uses_selenium_fallback_when_html_parse_fails(self) -> None:
        class _Resp:
            status_code = 200
            url = "https://fuckingfast.co/6rvvnzxyknbr"
            headers = {"Content-Type": "text/html; charset=utf-8"}
            text = "<html><body>No direct link in static HTML</body></html>"

        with patch("download_link_resolver.requests.get", return_value=_Resp()):
            with patch(
                "download_link_resolver._resolve_with_selenium_click",
                return_value=SimpleNamespace(
                    resolved_url="https://fuckingfast.co/dl/SELENIUM_TOKEN",
                    was_resolved=True,
                    reason="resolved via selenium click",
                ),
            ):
                result = resolve_download_button_link(
                    "https://fuckingfast.co/6rvvnzxyknbr#file.part001.rar",
                    timeout=10,
                    verify_ssl=True,
                    enabled=True,
                    selenium_fallback_enabled=True,
                    selenium_headless=True,
                )

        self.assertTrue(result.was_resolved)
        self.assertEqual(result.resolved_url, "https://fuckingfast.co/dl/SELENIUM_TOKEN")


if __name__ == "__main__":
    unittest.main()
