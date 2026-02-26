from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from config_loader import AppConfig
from validator import URLValidator


@dataclass(frozen=True)
class FilePart:
    index: int
    url: str
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class GenerationReport:
    parts: list[FilePart]
    examined_count: int
    stop_reason: str
    stop_index: int | None


class URLGenerator:
    def __init__(self, config: AppConfig, validator: URLValidator) -> None:
        self.config = config
        self.validator = validator

    def _build_url(self, index: int) -> str:
        padded_index = str(index).zfill(self.config.padding)

        candidate = self.config.filename_pattern.format(
            index=padded_index,
            index_raw=index,
            base_url=self.config.base_url.rstrip("/"),
        )

        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate

        return f"{self.config.base_url.rstrip('/')}/{candidate.lstrip('/')}"

    @staticmethod
    def _extract_filename(url: str, index: int) -> str:
        path = urlparse(url).path
        name = PurePosixPath(path).name
        return name if name else f"part_{index}"

    def generate(self) -> GenerationReport:
        parts: list[FilePart] = []
        seen_urls: set[str] = set()
        index = self.config.start_index
        examined = 0
        stop_reason = "completed"
        stop_index: int | None = None

        while examined < self.config.max_part:
            if self.config.end_index is not None and index > self.config.end_index:
                stop_reason = "reached end_index"
                break

            url = self._build_url(index)
            examined += 1

            if url in seen_urls:
                stop_reason = "duplicate URL generated"
                stop_index = index
                break

            seen_urls.add(url)
            result = self.validator.validate(url)

            if not result.is_valid:
                # Always stop on first invalid part to avoid generating non-existing chunks.
                stop_reason = result.reason
                stop_index = index
                break

            parts.append(
                FilePart(
                    index=index,
                    url=url,
                    filename=self._extract_filename(url, index),
                    size_bytes=result.size_bytes,
                )
            )

            index += 1

        if examined >= self.config.max_part and stop_reason == "completed":
            stop_reason = "reached max_part"
            stop_index = index

        return GenerationReport(
            parts=parts,
            examined_count=examined,
            stop_reason=stop_reason,
            stop_index=stop_index,
        )
