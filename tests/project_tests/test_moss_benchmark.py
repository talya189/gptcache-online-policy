"""Offline provenance, safety, labeling, and replay tests for MOSS."""

import csv
import hashlib
import importlib.metadata
import json
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.carma.moss import (
    OFFICIAL_SOURCE,
    SCHEMA_VERSION,
    SOURCE_ARCHIVE_SHA256,
    SOURCE_ARCHIVE_SIZE,
    SOURCE_FILENAME,
    SOURCE_MEMBER,
    SOURCE_MEMBER_CRC32,
    SOURCE_MEMBER_SIZE,
    SOURCE_REPOSITORY,
    SOURCE_REVISION,
    SOURCE_XET_HASH,
    TIKTOKEN_VERSION,
    TiktokenCounter,
    fixture_source_spec,
    inspect_archive,
    prepare_archive,
    replay_prepared,
    run_benchmark,
    validate_remote_metadata,
)


FIXTURE = Path(__file__).parent / "fixtures" / "moss_tiny.jsonl"


class FixtureTokenCounter:
    package = "fixture-token-counter"
    version = "1"
    encoding = "utf8-byte-groups"
    test_fixture = True

    @staticmethod
    def count(text):
        encoded = text.encode("utf-8")
        return (len(encoded) + 3) // 4


def _archive(tmp_path, member_name="moss_tiny.jsonl"):
    path = tmp_path / "moss-fixture.zip"
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member_name, FIXTURE.read_bytes())
    return path, fixture_source_spec(path)


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def test_frozen_official_identity_matches_metadata_and_range_inspection():
    assert OFFICIAL_SOURCE.repository == SOURCE_REPOSITORY
    assert OFFICIAL_SOURCE.revision == SOURCE_REVISION
    assert OFFICIAL_SOURCE.filename == SOURCE_FILENAME
    assert OFFICIAL_SOURCE.archive_size == SOURCE_ARCHIVE_SIZE == 392_830_680
    assert OFFICIAL_SOURCE.archive_sha256 == SOURCE_ARCHIVE_SHA256
    assert OFFICIAL_SOURCE.member_name == SOURCE_MEMBER
    assert OFFICIAL_SOURCE.member_size == SOURCE_MEMBER_SIZE == 2_310_713_571
    assert OFFICIAL_SOURCE.member_crc32 == SOURCE_MEMBER_CRC32 == 0x34C7D393


def test_remote_metadata_validation_is_revision_and_checksum_strict():
    repository = {
        "id": SOURCE_REPOSITORY,
        "sha": SOURCE_REVISION,
        "private": False,
        "tags": ["license:cc-by-4.0"],
    }
    tree = [
        {
            "path": SOURCE_FILENAME,
            "size": SOURCE_ARCHIVE_SIZE,
            "lfs": {
                "size": SOURCE_ARCHIVE_SIZE,
                "oid": SOURCE_ARCHIVE_SHA256,
            },
            "xetHash": SOURCE_XET_HASH,
        }
    ]
    result = validate_remote_metadata(repository, tree)
    assert result["verified"] is True
    assert result["download_started"] is False

    wrong_revision = {**repository, "sha": "0" * 40}
    with pytest.raises(ValueError, match="wrong revision"):
        validate_remote_metadata(wrong_revision, tree)
    wrong_tree = [{**tree[0], "lfs": {**tree[0]["lfs"], "oid": "0" * 64}}]
    with pytest.raises(ValueError, match="SHA-256 changed"):
        validate_remote_metadata(repository, wrong_tree)


def test_prepare_streams_fixture_and_derives_context_sensitive_concepts(tmp_path):
    archive, source = _archive(tmp_path)
    output = tmp_path / "prepared"
    manifest = prepare_archive(
        archive,
        output,
        sample_size=6,
        seed=17,
        source_spec=source,
        token_counter=FixtureTokenCounter(),
    )
    rows = _jsonl(output / "pool.jsonl")

    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["archive"]["member_crc_verified_during_stream"] is True
    assert manifest["raw_conversations"] == 3
    assert manifest["raw_turns"] == 6
    assert manifest["pool_rows"] == 6
    assert manifest["token_accounting"]["test_fixture"] is True
    assert len({row["concept_id"] for row in rows}) == 6
    first = next(
        row for row in rows if row["conversation_id"] == 101 and row["round"] == 1
    )
    second = next(
        row for row in rows if row["conversation_id"] == 101 and row["round"] == 2
    )
    assert first["prior_context_sha256"] != second["prior_context_sha256"]
    assert first["concept_id"] != second["concept_id"]
    assert first["response_id"].startswith("moss-response-")
    assert first["request_text"]
    assert first["request_sha256"] == hashlib.sha256(
        first["request_text"].encode("utf-8")
    ).hexdigest()
    assert first["total_tokens"] == (
        first["context_tokens"] + first["prompt_tokens"] + first["answer_tokens"]
    )

    second_output = tmp_path / "prepared-repeat"
    prepare_archive(
        archive,
        second_output,
        sample_size=6,
        seed=17,
        source_spec=source,
        token_counter=FixtureTokenCounter(),
    )
    assert (output / "pool.jsonl").read_bytes() == (
        second_output / "pool.jsonl"
    ).read_bytes()


def test_recorded_response_replay_has_no_model_calls_and_is_deterministic(tmp_path):
    archive, source = _archive(tmp_path)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    kwargs = {
        "sample_size": 6,
        "sample_seed": 11,
        "requests": 6,
        "capacity": 6,
        "trace_seed": 29,
        "selection": "all",
        "repeat_passes": 2,
        "source_spec": source,
        "token_counter": FixtureTokenCounter(),
    }
    manifest = run_benchmark(archive, first_output, **kwargs)
    run_benchmark(archive, second_output, **kwargs)

    for name in ("pool.jsonl", "requests.jsonl", "runs.csv", "manifest.json"):
        assert (first_output / name).read_bytes() == (second_output / name).read_bytes()
    requests = _jsonl(first_output / "requests.jsonl")
    with (first_output / "runs.csv").open(newline="") as source_file:
        run = next(csv.DictReader(source_file))
    assert len(requests) == 12
    assert sum(row["recorded_response_replayed"] for row in requests) == 6
    assert sum(row["valid_hit"] for row in requests) == 6
    assert all(row["live_model_called"] is False for row in requests)
    assert all(
        row["delivered_response_id"] == row["expected_response_id"]
        for row in requests
    )
    assert run["recorded_response_replays"] == "6"
    assert run["live_model_calls"] == "0"
    assert run["false_hits"] == "0"
    assert manifest["no_live_llm_or_api"] is True


def test_novel_long_selection_is_unique_and_replays_every_miss(tmp_path):
    archive, source = _archive(tmp_path)
    prepared = tmp_path / "prepared"
    prepare_archive(
        archive,
        prepared,
        sample_size=6,
        seed=0,
        source_spec=source,
        token_counter=FixtureTokenCounter(),
    )
    replayed = tmp_path / "replayed"
    manifest = replay_prepared(
        prepared,
        replayed,
        requests=1,
        capacity=2,
        seed=0,
        selection="novel-long",
    )
    row = _jsonl(replayed / "requests.jsonl")[0]
    assert row["reuse_opportunity"] is False
    assert row["recorded_response_replayed"] is True
    assert manifest["inferential_role"] == "preregistered novel-long negative control"


def test_archive_rejects_traversal_and_checksum_mismatch(tmp_path):
    malicious, malicious_source = _archive(tmp_path, member_name="../escape.jsonl")
    with pytest.raises(ValueError, match="unsafe MOSS ZIP member"):
        inspect_archive(malicious, malicious_source)

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    archive, source = _archive(safe_dir)
    wrong_hash = replace(source, archive_sha256="0" * 64)
    with pytest.raises(ValueError, match="checksum mismatch"):
        inspect_archive(archive, wrong_hash)


def test_production_token_counter_enforces_pinned_version():
    observed = importlib.metadata.version("tiktoken")
    if observed == TIKTOKEN_VERSION:
        counter = TiktokenCounter()
        assert counter.package == "tiktoken"
        assert counter.version == TIKTOKEN_VERSION
    else:
        with pytest.raises(RuntimeError, match="requires tiktoken"):
            TiktokenCounter()
