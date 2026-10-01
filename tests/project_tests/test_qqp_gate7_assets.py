"""Regression tests for deterministic Gate 7 asset acquisition."""

import sys
from pathlib import Path
from types import ModuleType

import pytest

from benchmarks.carma import qqp


def _install_fake_huggingface_hub(
    monkeypatch, hf_hub_download, snapshot_download
):
    module = ModuleType("huggingface_hub")
    module.hf_hub_download = hf_hub_download
    module.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)


def _asset_fixture(tmp_path: Path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model-placeholder")
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    for name in qqp.GATE7_TOKENIZER_FILES:
        (tokenizer / name).write_bytes(("placeholder:" + name).encode("utf-8"))
    return model, tokenizer


def test_prefetch_gate7_assets_uses_exact_revisions_and_verifies_full_maps(
    tmp_path, monkeypatch
):
    model, tokenizer = _asset_fixture(tmp_path)
    calls = []

    def fake_model(**kwargs):
        calls.append(("model", kwargs))
        return str(model)

    def fake_tokenizer(**kwargs):
        calls.append(("tokenizer", kwargs))
        return str(tokenizer)

    def fake_sha256(path):
        path = Path(path)
        if path == model:
            return qqp.GATE7_MODEL_FILES["model.onnx"]
        return qqp.GATE7_TOKENIZER_FILES[path.name]

    _install_fake_huggingface_hub(
        monkeypatch, fake_model, fake_tokenizer
    )
    monkeypatch.setattr(qqp, "sha256_file", fake_sha256)

    result = qqp.prefetch_gate7_assets(tmp_path / "cache")

    assert result["status"] == "verified"
    assert result["model_revision"] == qqp.MODEL_REVISION
    assert result["tokenizer_revision"] == qqp.TOKENIZER_REVISION
    assert result["model_files"] == qqp.GATE7_MODEL_FILES
    assert result["tokenizer_files"] == qqp.GATE7_TOKENIZER_FILES
    assert calls == [
        (
            "model",
            {
                "repo_id": qqp.MODEL_REPOSITORY,
                "filename": "model.onnx",
                "revision": qqp.MODEL_REVISION,
                "cache_dir": str((tmp_path / "cache").resolve()),
            },
        ),
        (
            "tokenizer",
            {
                "repo_id": qqp.TOKENIZER_REPOSITORY,
                "revision": qqp.TOKENIZER_REVISION,
                "allow_patterns": (
                    "*.json",
                    "*.txt",
                    "tokenizer.*",
                    "*.model",
                ),
                "cache_dir": str((tmp_path / "cache").resolve()),
            },
        ),
    ]


def test_prefetch_gate7_assets_rejects_checksum_drift(tmp_path, monkeypatch):
    model, tokenizer = _asset_fixture(tmp_path)
    _install_fake_huggingface_hub(
        monkeypatch,
        lambda **_kwargs: str(model),
        lambda **_kwargs: str(tokenizer),
    )

    def drifted_sha256(path):
        path = Path(path)
        if path == model:
            return "0" * 64
        return qqp.GATE7_TOKENIZER_FILES[path.name]

    monkeypatch.setattr(qqp, "sha256_file", drifted_sha256)

    with pytest.raises(RuntimeError, match="differ from frozen identities"):
        qqp.prefetch_gate7_assets()


def test_prefetch_gate7_assets_cli_is_explicit():
    args = qqp.build_parser().parse_args(["prefetch-gate7-assets"])

    assert args.command == "prefetch-gate7-assets"
    assert args.cache_dir is None
