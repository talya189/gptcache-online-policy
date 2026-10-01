"""Command-line regression tests for the full QQP validation wrapper."""

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = PROJECT_ROOT / "scripts" / "run_qqp_validation.sh"


def test_wrapper_help_is_side_effect_free_and_describes_environment():
    result = subprocess.run(
        ["bash", str(WRAPPER), "--help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage: run_qqp_validation.sh [OUTPUT_DIR]" in result.stdout
    assert "PYTHON_BIN" in result.stdout
    assert result.stderr == ""


def test_wrapper_uses_python_override_for_each_pinned_stage(tmp_path):
    calls = tmp_path / "calls.txt"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$QQP_WRAPPER_CALLS\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    output = tmp_path / "qqp"
    environment = {
        **os.environ,
        "PYTHON_BIN": str(fake_python),
        "QQP_WRAPPER_CALLS": str(calls),
    }

    result = subprocess.run(
        ["bash", str(WRAPPER), str(output)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "-m benchmarks.carma.qqp prepare "
        f"--archive {PROJECT_ROOT / 'examples/benchmark/similiar_qqp_full.json.gz'} "
        f"--output {output / 'prepared'}",
        "-m benchmarks.carma.qqp embed "
        f"--prepared {output / 'prepared'} "
        f"--output {output / 'embeddings'} --batch-size 32",
        "-m benchmarks.carma.qqp_v2 "
        f"--prepared {output / 'prepared'} "
        f"--embeddings {output / 'embeddings'} "
        f"--output {output / 'evaluation'}",
    ]
