"""Generate Prompt patch drafts; never applies them automatically."""

import asyncio
import re
import sqlite3
import uuid
from datetime import datetime

from config.prompts.prompt_optimizer_prompt import PROMPT_OPTIMIZER_SYSTEM_PROMPT
from src.agents.llm_agent import LLMAgent
from src.crewai.database import _resolve_db_path, init_db
from .version_manager import get_prompt_version


def _default_generator(prompt: str) -> str:
    agent = LLMAgent("提示词优化师", PROMPT_OPTIMIZER_SYSTEM_PROMPT, use_thinking=True)
    return asyncio.run(agent.step(prompt))


def generate_patch_proposal(cluster_id: str, generator=None,
                            db_path: str | None = None) -> dict:
    """Create a draft proposal from cluster evidence and the immutable base Prompt."""
    init_db(db_path).close()
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    cluster = conn.execute("SELECT * FROM badcase_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    if not cluster or not cluster["root_cause_summary"]:
        conn.close(); raise ValueError("Root cause summary is required before generating a patch")
    base = get_prompt_version(cluster["prompt_version"], db_path)
    request = f"""请为以下 Badcase 生成一个可人工审核的 Prompt Patch Proposal。

当前完整Prompt：
{base['prompt_content']}

目标问题：{cluster['error_type']}
可能根因（不是确定事实）：
{cluster['root_cause_summary']}

证据：
{cluster['evidence_json']}

输出完整的优化后Prompt；不得改变与目标问题无关的规则，不得声称已经上线。"""
    raw = (generator or _default_generator)(request)
    match = re.search(r"【优化后的Prompt】[：:]?\s*(.*)", raw, re.DOTALL)
    content = (match.group(1) if match else raw).strip()
    if not content:
        conn.close(); raise ValueError("Prompt optimizer returned an empty patch")
    patch_id = f"patch_{uuid.uuid4().hex}"
    reason = f"针对 {cluster['error_type']}：{cluster['root_cause_summary']}"
    conn.execute("""INSERT INTO prompt_patch_proposals
        (patch_id, base_prompt_version, target_error_type, cluster_id,
         patch_content, reason, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')""",
        (patch_id, base["version"], cluster["error_type"], cluster_id, content, reason))
    conn.commit(); conn.close()
    return {"patch_id": patch_id, "base_prompt_version": base["version"],
            "target_error_type": cluster["error_type"], "patch_content": content,
            "reason": reason, "status": "draft"}


def review_patch(patch_id: str, approved: bool, db_path: str | None = None) -> None:
    """Explicit human review gate."""
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path)
    status = "approved" if approved else "rejected"
    cur = conn.execute("""UPDATE prompt_patch_proposals SET status = ?, reviewed_at = ?
                          WHERE patch_id = ? AND status = 'draft'""",
                       (status, datetime.now().isoformat(), patch_id))
    conn.commit(); conn.close()
    if cur.rowcount != 1:
        raise ValueError("Only a draft patch can be reviewed")
