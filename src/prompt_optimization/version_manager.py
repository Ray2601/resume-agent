"""Immutable Prompt version registry and human-approved candidate creation."""

import re
import sqlite3
from datetime import datetime

from config.prompts.hr_prompt import HR_WRITING_REQUIREMENTS
from src.crewai.database import _resolve_db_path, init_db


BUILTIN_WRITER_PROMPTS = {
    "writer_v1.0": "",
    "writer_v1.1": HR_WRITING_REQUIREMENTS,
}


def ensure_builtin_versions(db_path: str | None = None) -> None:
    init_db(db_path).close()
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    for version, content in BUILTIN_WRITER_PROMPTS.items():
        conn.execute("""INSERT OR IGNORE INTO prompt_versions
            (version, prompt_type, prompt_content, status, is_active)
            VALUES (?, 'writer', ?, ?, ?)""",
            (version, content, "active" if version == "writer_v1.1" else "archived",
             1 if version == "writer_v1.1" else 0))
    conn.commit(); conn.close()


def get_prompt_version(version: str, db_path: str | None = None) -> dict:
    ensure_builtin_versions(db_path)
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM prompt_versions WHERE version = ?", (version,)).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"Unknown immutable prompt version: {version}")
    return dict(row)


def _next_candidate_version(base_version: str, conn: sqlite3.Connection) -> str:
    match = re.fullmatch(r"writer_v(\d+)\.(\d+)(?:_candidate)?", base_version)
    if not match:
        raise ValueError(f"Unsupported writer version format: {base_version}")
    major, minor = map(int, match.groups())
    while True:
        candidate = f"writer_v{major}.{minor + 1}_candidate"
        if not conn.execute("SELECT 1 FROM prompt_versions WHERE version = ?", (candidate,)).fetchone():
            return candidate
        minor += 1


def create_candidate_version(patch_id: str, db_path: str | None = None) -> str:
    """Create a candidate only from a human-approved patch; never mutate its parent."""
    ensure_builtin_versions(db_path)
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    patch = conn.execute("SELECT * FROM prompt_patch_proposals WHERE patch_id = ?", (patch_id,)).fetchone()
    if not patch or patch["status"] != "approved":
        conn.close(); raise ValueError("Patch must be human-approved before candidate creation")
    if patch["candidate_version"]:
        conn.close(); return patch["candidate_version"]
    get_prompt_version(patch["base_prompt_version"], db_path)
    version = _next_candidate_version(patch["base_prompt_version"], conn)
    conn.execute("""INSERT INTO prompt_versions
        (version, prompt_type, prompt_content, parent_version, source_patch_id, status, is_active)
        VALUES (?, 'writer', ?, ?, ?, 'candidate', 0)""",
        (version, patch["patch_content"], patch["base_prompt_version"], patch_id))
    conn.execute("UPDATE prompt_patch_proposals SET candidate_version = ? WHERE patch_id = ?",
                 (version, patch_id))
    conn.commit(); conn.close(); return version
