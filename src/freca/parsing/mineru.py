from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from freca.config import MinerUConfig, MinerUMode


@dataclass(frozen=True)
class NormalizedMinerUBlock:
    page: int
    kind: str
    text: str
    level: int | None = None
    bbox: list[float] | list[int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MinerUParseResult:
    markdown: str
    content_list: list[Any]
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MinerUClient(Protocol):
    def parse(self, source: Path, output_dir: Path) -> MinerUParseResult: ...


def _load_content_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("MinerU content_list must be a JSON list")
    return parsed


def _flatten_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "".join(_flatten_content(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("content"), str):
            return value["content"]
        preferred = (
            "title_content",
            "paragraph_content",
            "page_footnote_content",
            "math_content",
            "table_body",
            "code_body",
            "text",
            "list_items",
        )
        for key in preferred:
            if key in value:
                return _flatten_content(value[key])
        return "".join(_flatten_content(item) for item in value.values())
    return ""


def _legacy_text(item: dict[str, Any]) -> str:
    for key in (
        "text",
        "table_body",
        "code_body",
        "content",
        "list_items",
        "latex",
        "equation",
    ):
        if key in item:
            text = _flatten_content(item[key]).strip()
            if text:
                return text
    captions = []
    for key in (
        "image_caption",
        "table_caption",
        "chart_caption",
        "code_caption",
    ):
        if key in item:
            value = _flatten_content(item[key]).strip()
            if value:
                captions.append(value)
    return "\n".join(captions)


def _kind(item_type: str, level: int | None) -> str:
    lowered = item_type.lower()
    if lowered in {"title", "heading"} or (lowered == "text" and level):
        return "heading"
    if lowered in {"table", "chart"}:
        return "table"
    if lowered in {"image"}:
        return "image"
    if lowered in {"equation", "equation_interline"}:
        return "equation"
    if lowered in {"list", "index"}:
        return "list"
    if lowered in {"code", "algorithm"}:
        return "code"
    return "paragraph"


def normalize_content_list(value: Any) -> list[NormalizedMinerUBlock]:
    raw = _load_content_list(value)
    normalized: list[NormalizedMinerUBlock] = []
    is_v2 = bool(raw) and all(isinstance(page, list) for page in raw)
    if is_v2:
        iterator = (
            (page_index, item)
            for page_index, page_items in enumerate(raw)
            for item in page_items
        )
    else:
        iterator = (
            (int(item.get("page_idx", 0)), item)
            for item in raw
            if isinstance(item, dict)
        )
    for page_index, item in iterator:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "text"))
        content = item.get("content")
        if isinstance(content, dict):
            text = _flatten_content(content).strip()
            level_value = content.get("level")
        else:
            text = _legacy_text(item)
            level_value = item.get("text_level")
        if not text:
            continue
        level = int(level_value) if isinstance(level_value, (int, float)) else None
        normalized.append(
            NormalizedMinerUBlock(
                page=page_index + 1,
                kind=_kind(item_type, level),
                text=text,
                level=level,
                bbox=item.get("bbox"),
                metadata={"mineru_type": item_type},
            )
        )
    return normalized


class MinerUOpenSdkClient:
    def __init__(
        self,
        config: MinerUConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.client_factory = client_factory

    def parse(self, source: Path, output_dir: Path) -> MinerUParseResult:
        token = os.environ.get(self.config.token_env) if self.config.token_env else None
        if not token:
            raise RuntimeError(
                f"required MinerU credential environment variable is unset: {self.config.token_env}"
            )
        factory = self.client_factory
        if factory is None:
            try:
                from mineru import MinerU
            except ImportError as exc:
                raise RuntimeError(
                    "mineru-open-sdk is not installed; install the 'mineru' optional dependency"
                ) from exc
            factory = MinerU
        output_dir.mkdir(parents=True, exist_ok=True)
        client = factory(token, base_url=self.config.base_url)
        try:
            result = client.extract(
                str(source),
                model=self.config.model,
                language=self.config.language,
                ocr=self.config.ocr,
                formula=self.config.formula,
                table=self.config.table,
                timeout=self.config.timeout_seconds,
            )
            state = getattr(result, "state", "done")
            if state == "failed":
                raise RuntimeError("MinerU cloud extraction failed")
            if hasattr(result, "save_all"):
                result.save_all(str(output_dir))
            content_list = _load_content_list(getattr(result, "content_list", None))
            markdown = str(getattr(result, "markdown", "") or "")
            if not content_list:
                raise RuntimeError("MinerU cloud response has no structured content_list")
            return MinerUParseResult(
                markdown=markdown,
                content_list=content_list,
                provider="mineru-open-sdk",
                metadata={"task_id": getattr(result, "task_id", None), "state": state},
            )
        finally:
            if hasattr(client, "close"):
                client.close()


def _safe_extract(archive: zipfile.ZipFile, output_dir: Path) -> None:
    root = output_dir.resolve()
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"unsafe path in MinerU archive: {member.filename}")
    archive.extractall(root)


def _read_remote_artifacts(output_dir: Path) -> tuple[str, list[Any]]:
    content_paths = sorted(output_dir.rglob("*_content_list_v2.json"))
    if not content_paths:
        content_paths = sorted(output_dir.rglob("*_content_list.json"))
    if not content_paths:
        raise RuntimeError("MinerU response archive has no content_list JSON")
    markdown_paths = sorted(output_dir.rglob("*.md"))
    markdown = markdown_paths[0].read_text(encoding="utf-8") if markdown_paths else ""
    return markdown, _load_content_list(
        json.loads(content_paths[0].read_text(encoding="utf-8"))
    )


class MinerURemoteApiClient:
    def __init__(
        self,
        config: MinerUConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def parse(self, source: Path, output_dir: Path) -> MinerUParseResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        headers: dict[str, str] = {}
        if self.config.token_env:
            token = os.environ.get(self.config.token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        with source.open("rb") as handle, httpx.Client(
            transport=self.transport,
            timeout=self.config.timeout_seconds,
        ) as client:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/file_parse",
                headers=headers,
                files={"files": (source.name, handle, "application/pdf")},
                data={
                    "return_md": "true",
                    "response_format_zip": "true",
                    "formula_enable": str(self.config.formula).lower(),
                    "table_enable": str(self.config.table).lower(),
                    "lang_list": self.config.language,
                },
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "zip" in content_type or response.content.startswith(b"PK"):
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                _safe_extract(archive, output_dir)
            markdown, content_list = _read_remote_artifacts(output_dir)
        else:
            body = response.json()
            payload: Any = body.get("data", body)
            if isinstance(payload, dict) and isinstance(payload.get("results"), dict):
                payload = next(iter(payload["results"].values()))
            if isinstance(body.get("results"), dict):
                payload = next(iter(body["results"].values()))
            if not isinstance(payload, dict):
                raise RuntimeError("MinerU remote response has an unsupported JSON shape")
            markdown = str(payload.get("md") or payload.get("markdown") or "")
            content_list = _load_content_list(
                payload.get("content_list") or payload.get("content_list_v2")
            )
        if not content_list:
            raise RuntimeError("MinerU remote response has no structured content_list")
        return MinerUParseResult(
            markdown=markdown,
            content_list=content_list,
            provider="mineru-remote-api",
        )


def build_mineru_client(config: MinerUConfig) -> MinerUClient | None:
    if config.mode == MinerUMode.DISABLED:
        return None
    if config.mode == MinerUMode.CLOUD_SDK:
        return MinerUOpenSdkClient(config)
    if config.mode == MinerUMode.REMOTE_API:
        return MinerURemoteApiClient(config)
    raise ValueError(f"unsupported MinerU mode: {config.mode}")
