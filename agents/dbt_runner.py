import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


ALLOWED_DBT_COMMANDS = {"debug", "compile", "build", "test"}


def run_dbt_command(
    dbt_dir: str,
    command: str,
    database_path: str | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    if command not in ALLOWED_DBT_COMMANDS:
        raise ValueError(f"Unsupported dbt command: {command}")

    project_dir = Path(dbt_dir).resolve()
    if not (project_dir / "dbt_project.yml").exists():
        raise ValueError(f"dbt project not found at {dbt_dir}. Click Proceed first.")

    dbt_exe = _dbt_executable()
    cmd = [
        str(dbt_exe),
        command,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
    ]

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    if command == "build" and database_path:
        _drop_generated_dbt_objects(database_path)

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(Path.cwd()),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "error",
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\ndbt {command} timed out after {timeout_seconds} seconds.",
            "artifacts": {},
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    missing_dbt = "No module named dbt" in stderr or "No module named dbt" in stdout
    status = "ok" if completed.returncode == 0 else "error"
    if missing_dbt:
        stderr = (
            stderr
            + "\nInstall dbt dependencies first: pip install dbt-core dbt-sqlite"
        ).strip()

    return {
        "command": command,
        "status": status,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "artifacts": _read_artifacts(project_dir),
    }


def _dbt_executable() -> Path:
    scripts_dir = Path(sys.executable).parent
    candidate = scripts_dir / ("dbt.exe" if os.name == "nt" else "dbt")
    if candidate.exists():
        return candidate
    return Path("dbt")


def _drop_generated_dbt_objects(database_path: str) -> None:
    if not Path(database_path).exists():
        return
    conn = sqlite3.connect(database_path)
    try:
        rows = conn.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name LIKE 'stg_%'
               OR name LIKE 'mart_%'
            """
        ).fetchall()
        for object_type, name in rows:
            if object_type == "view":
                conn.execute(f'DROP VIEW IF EXISTS "{name}"')
            elif object_type == "table":
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        conn.commit()
    finally:
        conn.close()


def _read_artifacts(project_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    target_dir = project_dir / "target"
    for name in ("run_results.json", "manifest.json"):
        path = target_dir / name
        if path.exists():
            try:
                artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                artifacts[name] = {"path": str(path)}
    return artifacts
