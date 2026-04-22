from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb


def test_audit_core_cli_creates_outputs(tmp_path: Path) -> None:
    manuscript = Path("examples/software_equivalence_manuscript.md")
    output_dir = tmp_path / "cli_output"
    db_path = tmp_path / "cli.duckdb"
    env = {**os.environ, "PYTHONPATH": "src"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "manuscript_audit.cli",
            "audit-core",
            str(manuscript),
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Completed run" in result.stdout
    assert (output_dir / "reports" / "final_vetting_report.json").exists()

    payload = json.loads((output_dir / "reports" / "final_vetting_report.json").read_text())
    assert payload["classification"]["pathway"] == "data_science"

    connection = duckdb.connect(str(db_path))
    run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    connection.close()
    assert run_count == 1
