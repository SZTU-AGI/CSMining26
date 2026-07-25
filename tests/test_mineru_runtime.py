from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from freca.config import MinerUConfig, MinerUMode
from freca.manifest import sha256_file
from freca.models import SourceRecord, SourceType
from freca.parsing.mineru import (
    MinerUParseResult,
    MinerUOpenSdkClient,
    MinerURemoteApiClient,
    normalize_content_list,
)
from freca.parsing.pdf import parse_pdf


def test_mineru_config_defaults_to_explicit_disabled_mode() -> None:
    config = MinerUConfig()

    assert config.mode == MinerUMode.DISABLED
    assert config.token_env == "MINERU_TOKEN"


def test_normalizes_legacy_content_list_with_one_based_pages() -> None:
    blocks = normalize_content_list(
        [
            {"type": "text", "text": "Part 1", "text_level": 1, "page_idx": 0},
            {"type": "table", "table_body": "<table>A</table>", "page_idx": 1},
        ]
    )

    assert [(block.page, block.kind, block.text) for block in blocks] == [
        (1, "heading", "Part 1"),
        (2, "table", "<table>A</table>"),
    ]


def test_normalizes_v2_page_grouped_content_list() -> None:
    blocks = normalize_content_list(
        [
            [
                {
                    "type": "title",
                    "content": {
                        "title_content": [{"type": "text", "content": "Chapter 4"}],
                        "level": 2,
                    },
                    "bbox": [1, 2, 3, 4],
                }
            ],
            [
                {
                    "type": "paragraph",
                    "content": {
                        "paragraph_content": [
                            {"type": "text", "content": "Records "},
                            {"type": "text", "content": "must be kept."},
                        ]
                    },
                }
            ],
        ]
    )

    assert blocks[0].page == 1
    assert blocks[0].level == 2
    assert blocks[0].bbox == [1, 2, 3, 4]
    assert blocks[1].page == 2
    assert blocks[1].text == "Records must be kept."


def test_cloud_sdk_requires_token_before_import(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MINERU_TEST_TOKEN", raising=False)
    config = MinerUConfig(mode="cloud_sdk", token_env="MINERU_TEST_TOKEN")

    with pytest.raises(RuntimeError, match="MINERU_TEST_TOKEN"):
        MinerUOpenSdkClient(config).parse(tmp_path / "policy.pdf", tmp_path / "out")


def test_remote_api_extracts_zip_content_list_and_markdown(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MINERU_REMOTE_TOKEN", "secret")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "policy/policy_content_list.json",
            json.dumps([{"type": "text", "text": "Rule 4", "page_idx": 3}]),
        )
        bundle.writestr("policy/policy.md", "# Rule 4")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/file_parse"
        assert request.headers["Authorization"] == "Bearer secret"
        assert b'response_format_zip' in request.content
        return httpx.Response(
            200,
            content=archive.getvalue(),
            headers={"content-type": "application/zip"},
        )

    config = MinerUConfig(
        mode="remote_api",
        base_url="https://mineru.example",
        token_env="MINERU_REMOTE_TOKEN",
    )
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"pdf")
    client = MinerURemoteApiClient(config, transport=httpx.MockTransport(handler))

    result = client.parse(source, tmp_path / "out")

    assert result.markdown == "# Rule 4"
    assert result.content_list[0]["page_idx"] == 3
    assert (tmp_path / "out" / "policy" / "policy_content_list.json").exists()


def test_pdf_parser_uses_structured_mineru_blocks_when_client_is_configured(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "policy.pdf"
    source_path.write_bytes(b"not-opened-by-fallback")
    source = SourceRecord(
        source_id="policy",
        path=source_path,
        source_type=SourceType.PDF,
        sha256=sha256_file(source_path),
    )

    class Client:
        def parse(self, path: Path, output_dir: Path) -> MinerUParseResult:
            assert path == source_path
            return MinerUParseResult(
                markdown="# Rule",
                content_list=[
                    {
                        "type": "text",
                        "text": "Registration requirement",
                        "text_level": 1,
                        "page_idx": 4,
                        "bbox": [1, 2, 3, 4],
                    }
                ],
                provider="fake-mineru",
            )

    chunks = parse_pdf(source, tmp_path / "mineru", mineru_client=Client())

    assert len(chunks) == 1
    assert chunks[0].location.page == 5
    assert chunks[0].content_kind.value == "heading"
    assert chunks[0].parser_name == "fake-mineru"
    assert chunks[0].metadata["bbox"] == [1, 2, 3, 4]
    assert "mineru_generated" in chunks[0].flags
