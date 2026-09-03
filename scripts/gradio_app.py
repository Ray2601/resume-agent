"""Gradio 前端 — 简历深度优化器

支持：JD选择/上传、经历上传、迭代轮次控制、人工规则注入、编造审计、SQLite数据收集、Badcase分析
"""

import os
import sys
import queue
import threading
import traceback
import json
import gradio as gr

# --- Windows 编码修复 ---
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv()

from src.utils.logger import get_logger

logger = get_logger("gradio_app")

BASE_DIR = _project_root
JD_DIR = os.path.join(BASE_DIR, "data", "JD")
REFERENCE_DIR = os.path.join(BASE_DIR, "data", "excellent_resumes")
RAW_EXP_DIR = os.path.join(BASE_DIR, "data", "raw_experiences")
TEST_CASES_DIR = os.path.join(BASE_DIR, "data", "test_cases")
USER_JD_DIR = os.path.join(JD_DIR, "user_uploads")
USER_EXP_DIR = os.path.join(RAW_EXP_DIR, "user_uploads")

# Ensure directories exist
os.makedirs(USER_JD_DIR, exist_ok=True)
os.makedirs(USER_EXP_DIR, exist_ok=True)

# ============================================================
# 核心原则（不可违反）
# ============================================================
def _scan_files_recursive(root_dir: str) -> dict[str, str]:
    """Recursively scan .md/.txt files in a directory → {rel_path: content}."""
    choices = {}
    if not os.path.isdir(root_dir):
        return choices
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            if fname.endswith((".md", ".txt")):
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, root_dir)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    choices[rel_path] = content
    return choices


def load_experience_choices() -> dict[str, str]:
    """加载已有经历文件 → {显示名: 内容}"""
    choices = {}
    # Scan both raw_experiences/ and test_cases/
    for d in [RAW_EXP_DIR, TEST_CASES_DIR]:
        choices.update(_scan_files_recursive(d))
    return choices


def load_jd_choices() -> dict[str, str]:
    """加载已有JD文件 → {显示名: 内容}"""
    choices = {}
    if os.path.isdir(JD_DIR):
        for root, _, files in os.walk(JD_DIR):
            for f in sorted(files):
                if f.endswith((".txt", ".md")):
                    path = os.path.join(root, f)
                    rel_path = os.path.relpath(path, JD_DIR)
                    with open(path, "r", encoding="utf-8") as fp:
                        choices[rel_path] = fp.read()
    return choices


def load_reference_resumes() -> list[str]:
    """加载优秀简历参考"""
    refs = []
    if os.path.isdir(REFERENCE_DIR):
        for f in sorted(os.listdir(REFERENCE_DIR)):
            path = os.path.join(REFERENCE_DIR, f)
            if os.path.isfile(path) and not f.startswith("."):
                with open(path, "r", encoding="utf-8") as fp:
                    refs.append(fp.read())
    return refs


def _scan_all_files_recursive(root_dir: str) -> dict[str, str]:
    """Recursively scan ALL files (no extension filter) → {rel_path: content}."""
    choices = {}
    if not os.path.isdir(root_dir):
        return choices
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            fpath = os.path.join(dirpath, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    rel_path = os.path.relpath(fpath, root_dir)
                    choices[rel_path] = content
            except (UnicodeDecodeError, OSError):
                pass  # skip binary files
    return choices


def load_reference_choices() -> dict[str, str]:
    """加载优秀简历参考文件 → {显示名: 内容}"""
    return _scan_all_files_recursive(REFERENCE_DIR)


def _save_jd_text(jd_text: str) -> str:
    """Save pasted JD to data/JD/user_uploads/ and return the filename."""
    import re
    # Generate filename from first line or first 30 chars
    first_line = jd_text.strip().split("\n")[0][:60]
    safe_name = re.sub(r'[^\w一-鿿\-\s]', '', first_line).strip()[:30] or "jd"
    timestamp = __import__('datetime').datetime.now().strftime("%m%d_%H%M%S")
    fname = f"{safe_name}_{timestamp}.txt"
    fpath = os.path.join(USER_JD_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(jd_text)
    logger.info(f"JD saved: {fpath}")
    return fname


def _save_exp_text(exp_text: str, source_name: str = "") -> str:
    """Save uploaded experience to data/raw_experiences/user_uploads/ and return the filename."""
    import re
    base = source_name or "experience"
    safe_name = re.sub(r'[^\w一-鿿\-]', '', os.path.splitext(base)[0])[:30] or "exp"
    timestamp = __import__('datetime').datetime.now().strftime("%m%d_%H%M%S")
    fname = f"{safe_name}_{timestamp}.md"
    fpath = os.path.join(USER_EXP_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(exp_text)
    logger.info(f"Experience saved: {fpath}")
    return fname


def _status_table(phase0: str, phase1: str, phase2: str, phase3: str, current: str) -> str:
    """构建统一的进度面板 markdown"""
    return f"""## 🔄 实时进度

| 阶段 | Agent | 状态 |
|------|-------|------|
| Phase 0: JD分析 | 📋 JD分析师 | {phase0} |
| Phase 1: 智能提取 | 🔍 智能提取师 | {phase1} |
| Phase 2: 撰写迭代 | ✍️ 简历撰写师 + 👩‍💼 HR筛选官 | {phase2} |
| Phase 3: 编造审计 | 🛡️ 事实核查员 | {phase3} |

{current}"""


def _run_optimization_sync(
    jd_source: str,
    jd_upload: str | None,
    exp_source: str,
    exp_paste: str | None,
    related_files: list[str] | None,
    max_iterations: int,
    enable_human_rules: bool,
    human_rules: str,
    session_id: str,
    position_category: str,
    reference_select: list[str] | None,
    reference_paste_list: list[str] | None,
    fabrication_tolerance: int,
    result_queue: queue.Queue,
):
    """Synchronous optimization using CrewAI pipeline (runs in thread)."""
    from src.crewai.pipeline import CrewAIResumePipeline

    def _emit(status_txt, optimized="等待中...", audit="等待中...", before="等待中...",
              highlights="等待中...", score="等待中...", phase1="等待中...", badcase="等待中..."):
        result_queue.put((status_txt, optimized, audit, before, highlights, score, phase1, badcase))

    def _done(status_txt, optimized, audit, before, highlights, score, phase1, badcase):
        result_queue.put((status_txt, optimized, audit, before, highlights, score, phase1, badcase))

    try:
        # --- 1. JD ---
        jd_saved = False
        if jd_upload and jd_upload.strip():
            jd_content = jd_upload.strip()
            jd_name = _save_jd_text(jd_content)  # auto-save to local
            jd_saved = True
        elif jd_source:
            all_jds = load_jd_choices()
            jd_content = all_jds.get(jd_source, "")
            jd_name = jd_source
        else:
            _emit("## ❌ 错误\n\n请选择或上传JD", optimized="错误：请选择或上传JD")
            return

        if not jd_content.strip():
            _emit("## ❌ 错误\n\nJD内容为空", optimized="错误：JD内容为空")
            return

        # --- 2. 经历 ---
        if exp_paste and exp_paste.strip():
            raw_experience = exp_paste.strip()
            exp_filename = _save_exp_text(raw_experience, "pasted_experience")
        elif exp_source:
            all_exps = load_experience_choices()
            raw_experience = all_exps.get(exp_source, "")
            exp_filename = exp_source
        else:
            _emit("## ❌ 错误\n\n请选择或上传经历", optimized="错误：请选择或上传经历")
            return

        if not raw_experience.strip():
            _emit("## ❌ 错误\n\n经历内容为空", optimized="错误：经历内容为空")
            return

        # --- 3. 锚定内容 ---
        anchor_content = ""
        if related_files:
            parts = []
            for fp in related_files:
                if fp is not None:
                    with open(fp, "r", encoding="utf-8") as f:
                        parts.append(f.read().strip())
            anchor_content = "\n\n".join(parts)

        # --- 4. 参考简历 ---
        reference_resumes = load_reference_resumes()
        # Merge selected reference files from dropdown
        ref_choices = load_reference_choices()
        if reference_select:
            for ref_name in reference_select:
                if ref_name in ref_choices:
                    reference_resumes.append(ref_choices[ref_name])
        # Merge pasted reference text (multiple entries)
        if reference_paste_list:
            for pasted in reference_paste_list:
                if pasted.strip():
                    reference_resumes.append(pasted.strip())

        # --- 5. 初始化状态面板 ---
        save_hint = " (已自动保存)" if jd_saved else ""
        ref_from_select = len(reference_select) if reference_select else 0
        ref_from_paste = len(reference_paste_list) if reference_paste_list else 0
        ref_count = ref_from_select + ref_from_paste + len(reference_resumes)
        _emit(_status_table(
            "⏳ 等待中", "⏳ 等待中", "⏳ 等待中", "⏳ 等待中",
            f"**输入**: JD `{jd_name}`{save_hint}, 经历 `{exp_filename}`\n\n"
            f"**岗位分类**: {position_category} | 锚定: {'有' if anchor_content else '无'} | 参考简历: {ref_count}份\n\n"
            f"**Step 0**: 正在判断岗位领域..."
        ))

        # --- 6. 构建 pipeline 并运行 ---
        pipeline = CrewAIResumePipeline()
        pipeline.reference_resumes = reference_resumes

        # 进度回调 — 每阶段推送状态面板更新
        def on_progress(step: str):
            status_map = {
                "step0": (
                    "🔄 工作中...", "⏳ 等待中", "⏳ 等待中", "⏳ 等待中",
                    "**当前**: 🔍 **Step 0: 岗位认知度分类器** 判断岗位领域..."
                ),
                "step0b": (
                    "✅ 待定", "⏳ 等待中", "⏳ 等待中", "⏳ 等待中",
                    "**当前**: 📖 **Step 0b: 行业解码顾问** 学习陌生领域术语..."
                ),
                "phase0": (
                    "✅ 完成", "⏳ 等待中", "⏳ 等待中", "⏳ 等待中",
                    "**当前**: 📋 **Agent 1: JD分析官** 解析岗位需求..."
                ),
                "phase1": (
                    "✅ 完成", "🔄 工作中...", "⏳ 等待中", "⏳ 等待中",
                    "**当前**: 🔍 **Agent 2: 经历诊断师** 诊断经历，找缺失项/可量化点/冗余点..."
                ),
                "phase3": (
                    "✅ 完成", "✅ 完成",
                    f"✅ 完成 ({len(pipeline.history)}轮迭代)",
                    "🔄 工作中...",
                    "**当前**: 🛡️ **Agent 5: 事实核查员** 逐条审计编造内容..."
                ),
            }

            # Phase 2 iteration
            if step.startswith("phase2_write_"):
                rnd = step.split("_")[-1]
                status_map[step] = (
                    "✅ 完成", "✅ 完成",
                    f"🔄 第 {rnd} 轮撰写中",
                    "⏳ 等待中",
                    f"**当前**: ✍️ **Agent 3: STAR撰写师** 第 {rnd} 轮撰写..."
                )
            elif step.startswith("phase2_score_"):
                rnd = step.split("_")[-1]
                status_map[step] = (
                    "✅ 完成", "✅ 完成",
                    f"🔄 第 {rnd} 轮评分中",
                    "⏳ 等待中",
                    f"**当前**: 👩‍💼 **Agent 4: HR评分官** 第 {rnd} 轮四维评分..."
                )

            if step in status_map:
                _emit(_status_table(*status_map[step]))

        human_rules_text = human_rules.strip() if enable_human_rules else ""

        # --- 7. 运行 pipeline ---
        result = pipeline.run(
            jd=jd_content,
            raw_experience=raw_experience,
            anchor_content=anchor_content,
            target_score=93,
            max_iterations=max_iterations,
            human_rules=human_rules_text,
            session_id=session_id,
            progress_callback=on_progress,
            position_category=position_category,
            fabrication_tolerance=fabrication_tolerance,
        )

        # --- 8. 整理输出 ---
        last_hr = result["history"][-1]["hr_feedback"] if result["history"] else {}

        hr_score_detail = f"""## HR 评分详情

**总体评分: {result['final_score']}/100**

| 维度 | 得分 |
|------|------|
| 匹配度 | {last_hr.get('match_score', 'N/A')}/25 |
| 数据化 | {last_hr.get('data_score', 'N/A')}/25 |
| 影响力 | {last_hr.get('impact_score', 'N/A')}/25 |
| 简洁度 | {last_hr.get('brevity_score', 'N/A')}/25 |

**录用建议**: {last_hr.get('recommendation', 'N/A')}

### 优点
{chr(10).join(f'- {s}' for s in last_hr.get('strengths', [])) or 'N/A'}

### 改进点
{chr(10).join(f'- {s}' for s in last_hr.get('improvements', [])) or 'N/A'}

**HR评语**: {last_hr.get('summary', 'N/A')}

**迭代轮次**: {result['iterations']}轮

### Step 0: 岗位领域分类
- **领域判断**: {'已知领域' if result['familiarity']['is_known'] else '陌生领域'}
- **行业分类**: {result['familiarity'].get('industry', 'N/A')}
"""

        if result.get('industry_glossary'):
            hr_score_detail += f"\n\n### 行业术语词典\n\n{result['industry_glossary'][:2000]}"

        # --- Badcase dashboard ---
        from src.crewai.database import (
            get_stats, get_recent_records, init_db, ERROR_TYPES, track_event,
        )
        init_db()  # ensure DB exists
        track_event("result_viewed", session_id, result["run_id"], {"score": result["final_score"]})
        track_event("badcase_viewed", session_id, result["run_id"], {})
        stats = get_stats(session_id=session_id)
        recent = get_recent_records(session_id=session_id, limit=10)

        trace_rows = result.get("traces", [])
        trace_md = f"""## Harness Console

**Run ID**: `{result.get('run_id', 'N/A')}`  
**Prompt**: `{result.get('prompt_versions', {}).get('writer', '-')}` / `{result.get('prompt_versions', {}).get('hr', '-')}` / `{result.get('prompt_versions', {}).get('fact_checker', '-')}`

| Step | Agent | Round | 耗时 | Score | 状态 |
|------|-------|-------|------|-------|------|
"""
        for trace in trace_rows:
            latency = f"{trace.get('latency_ms', 0) / 1000:.1f}s"
            score = trace.get("score") if trace.get("score") is not None else "-"
            trace_md += (f"| {trace.get('step_name', '-')} | {trace.get('agent_name', '-')} "
                         f"| {trace.get('iteration') or '-'} | {latency} | {score} | {trace.get('status', '-')} |\n")

        badcase_md = trace_md + f"""

## Badcase 分析面板

### 会话统计 ({session_id})
| 指标 | 值 |
|------|-----|
| 总记录数 | {stats['total_records']} |
| 平均评分 | {stats['avg_score']}/100 |
| Badcase数 | {stats['badcase_count']} |
| 已修复率 | {stats['resolution_rate']} |

### 四维平均分
| 匹配度 | 数据化 | 影响力 | 简洁度 |
|--------|--------|--------|--------|
| {stats['avg_dimensions']['match']} | {stats['avg_dimensions']['data']} | {stats['avg_dimensions']['impact']} | {stats['avg_dimensions']['conciseness']} |

### 错误类型分布
"""

        if stats['error_distribution']:
            for etype, cnt in sorted(stats['error_distribution'].items(), key=lambda x: -x[1]):
                label = ERROR_TYPES.get(etype, etype)
                severity = "🔴" if etype in ("mismatch", "hallucination", "data_empty", "star_missing", "timeout") else "🟡" if etype in ("low_impact", "redundant", "fabrication", "keyword_missing") else "🟢"
                badcase_md += f"- {severity} **{label}** ({etype}): {cnt}次\n"
        else:
            badcase_md += "暂无Badcase记录\n"

        badcase_md += f"""
### 最近记录
| ID | 总分 | 匹配 | 数据 | 影响 | 简洁 | 状态 | 错误类型 | 领域 |
|----|------|------|------|------|------|------|---------|------|
"""
        for rec in recent:
            try:
                labels = [ERROR_TYPES.get(item["type"], item["type"])
                          for item in json.loads(rec.get("badcase_labels") or "[]")]
            except (json.JSONDecodeError, TypeError, KeyError):
                labels = []
            error_label = "、".join(labels) or ERROR_TYPES.get(
                rec.get('error_type', 'none'), rec.get('error_type', '-'))
            badcase_md += (
                f"| {rec['id']} | {rec['total_score']} | {rec['match_score']} "
                f"| {rec['data_score']} | {rec['impact_score']} | {rec['conciseness_score']} "
                f"| {rec['status']} | {error_label[:12]} | {rec.get('familiarity', '-')[:8]} |\n"
            )

        top_errors = ", ".join(
            f"{ERROR_TYPES.get(k, k)}" for k in list(stats['error_distribution'].keys())[:3]
        ) if stats['error_distribution'] else "暂无"
        badcase_md += f"\n> Run ID: `{result.get('run_id', 'N/A')}` | 记录ID: `{result.get('record_id', 'N/A')}` | Top错误: `{top_errors}`"

        pre_optimization = raw_experience[:3000]
        if len(raw_experience) > 3000:
            pre_optimization += f"\n\n... (原始内容共{len(raw_experience)}字，仅展示前3000字)"

        highlights_parts = ["## 优化要点\n"]
        for h in result["history"]:
            fb = h["hr_feedback"]
            highlights_parts.append(f"### 第{h['iteration']}轮 (评分: {h['score']}/100)")
            if fb.get("strengths"):
                highlights_parts.append(f"**保持**: {', '.join(fb['strengths'][:2])}")
            if fb.get("improvements"):
                highlights_parts.append(f"**改进方向**: {', '.join(fb['improvements'][:3])}")
            highlights_parts.append("")
        optimization_highlights = "\n".join(highlights_parts) if result["history"] else "N/A"

        iter_count = result["iterations"]
        final_status = f"""## ✅ 优化完成

| 阶段 | Agent | 状态 |
|------|-------|------|
| Step 0: 领域分类 | 🔍 岗位认知度分类器 | ✅ {'已知 → 快速路径' if result['familiarity']['is_known'] else '陌生 → 行业解码+改写'} |
| Phase 0: JD分析 | 📋 JD分析官 | ✅ 完成 |
| Phase 1: 经历诊断 | 🔍 经历诊断师 | ✅ 完成 |
| Phase 2: 撰写迭代 | ✍️ STAR撰写师 + 👩‍💼 HR评分官 | ✅ 完成 ({iter_count}轮) |
| Phase 3: 编造审计 | 🛡️ 事实核查员 | ✅ 完成 |

**最终评分**: **{result['final_score']}/100** | 迭代 {iter_count} 轮
"""

        _done(
            final_status,
            result["final_result"] or "N/A",
            result.get("fabrication_report", "N/A"),
            pre_optimization,
            optimization_highlights,
            hr_score_detail,
            result["phase1_summary"] or "N/A",
            badcase_md,
        )

    except Exception:
        try:
            from src.crewai.database import init_db, track_event
            init_db().close()
            track_event("optimization_failed", session_id,
                        getattr(locals().get("pipeline"), "run_id", ""),
                        {"error": traceback.format_exc()[-2000:]})
        except Exception:
            pass
        error_msg = f"## ❌ 运行时错误\n\n```\n{traceback.format_exc()}\n```"
        _done(error_msg, error_msg, "", "", "", "", "", "")
    finally:
        result_queue.put(None)  # 信号：完成


def run_optimization(
    jd_source: str,
    jd_upload: str | None,
    exp_source: str,
    exp_paste: str | None,
    related_files: list[str] | None,
    max_iterations: int,
    enable_human_rules: bool,
    human_rules: str,
    session_id: str,
    position_category: str,
    reference_select: list[str] | None,
    reference_paste_list: list[str] | None,
    fabrication_tolerance: int,
):
    """同步生成器：线程驱动 CrewAI pipeline，yield 中间结果给 Gradio。"""
    q: queue.Queue = queue.Queue()

    def _thread_target():
        _run_optimization_sync(
            jd_source, jd_upload, exp_source, exp_paste, related_files,
            max_iterations, enable_human_rules, human_rules, session_id,
            position_category, reference_select, reference_paste_list,
            fabrication_tolerance, q,
        )

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is None:
            break
        yield item

    t.join()


# ============================================================
# Gradio UI
# ============================================================

def create_ui():
    jd_choices = load_jd_choices()
    jd_names = list(jd_choices.keys())
    exp_choices = load_experience_choices()
    exp_names = list(exp_choices.keys())

    with gr.Blocks(title="简历深度优化器") as app:
        gr.Markdown("## 简历深度优化器")

        with gr.Row():
            # --- 左栏 2/3：输入 + 设置 ---
            with gr.Column(scale=2):
                with gr.Row():
                    session_id = gr.Textbox(
                        label="会话标识（区分不同朋友/用户）",
                        value="default",
                        placeholder="如：friend_01, user_zhang",
                        scale=1,
                    )
                    position_category = gr.Dropdown(
                        label="岗位分类",
                        choices=["互联网产品经理", "硬件产品经理", "机械/风电塔相关", "BD（商务拓展）", "其他"],
                        value="互联网产品经理",
                        scale=1,
                    )
                    template_select = gr.Dropdown(
                        label="缓存栏（选择历史模板自动填充）",
                        choices=[],
                        value=None,
                        scale=1,
                    )

                with gr.Row():
                    jd_source = gr.Dropdown(
                        label="JD（选择或粘贴覆盖）",
                        choices=jd_names,
                        value=jd_names[0] if jd_names else None,
                        scale=1,
                    )
                    jd_upload = gr.Textbox(
                        label="粘贴JD文本",
                        lines=3,
                        placeholder="粘贴后覆盖上方选择...",
                        scale=1,
                    )

                with gr.Row():
                    exp_source = gr.Dropdown(
                        label="已有经历（从文件选择）",
                        choices=exp_names,
                        value=exp_names[0] if exp_names else None,
                        scale=1,
                    )
                    exp_paste = gr.Textbox(
                        label="或粘贴已有经历文本",
                        lines=5,
                        placeholder="粘贴经历文本后覆盖上方选择...",
                        scale=1,
                    )

                with gr.Row():
                    related_files = gr.File(
                        label="关联内容上传 (可选, 多文件)",
                        file_types=[".md", ".txt"],
                        type="filepath",
                        file_count="multiple",
                        scale=1,
                    )

                with gr.Row():
                    reference_select = gr.Dropdown(
                        label="优秀简历参考（从文件选择，可多选）",
                        choices=list(load_reference_choices().keys()),
                        multiselect=True,
                        scale=1,
                    )
                with gr.Row():
                    reference_paste_text = gr.Textbox(
                        label="粘贴优秀简历参考文本",
                        lines=4,
                        placeholder="粘贴参考文本后点击「添加」...",
                        scale=3,
                    )
                    with gr.Column(scale=1, min_width=80):
                        add_reference_btn = gr.Button("添加", variant="secondary")
                        clear_references_btn = gr.Button("清空", variant="secondary", size="sm")
                    reference_paste_list = gr.State([])
                    reference_count_disp = gr.Markdown("已添加: **0** 条")

                with gr.Row():
                    max_iterations = gr.Slider(
                        label="迭代轮次",
                        minimum=1, maximum=5, value=3, step=1,
                        scale=1,
                    )
                    fabrication_tolerance = gr.Slider(
                        label="数据虚构容忍度",
                        info="0%=严禁编造 | 60%=可合理补充行业通用数据",
                        minimum=0, maximum=60, value=0, step=5,
                        scale=1,
                    )
                    enable_human_rules = gr.Checkbox(
                        label="注入人工规则",
                        value=False,
                        scale=1,
                    )

                human_rules = gr.Textbox(
                    label="人工规则（勾选后生效，以人工规则为先，不违反核心原则）",
                    lines=2,
                    placeholder="如：突出「从0到1」项目经验 / 强调跨部门协作 / 使用增长黑客术语...",
                )

                with gr.Row():
                    save_template_btn = gr.Button("保存为模板", variant="secondary", scale=1)
                    run_btn = gr.Button("开始优化", variant="primary", scale=3)

            # --- 右栏 1/3：输出 ---
            with gr.Column(scale=1):
                output_status = gr.Markdown("## 等待运行\n\n点击 **开始优化** 后这里会显示实时进度。")

                with gr.Tabs():
                    with gr.TabItem("优化后"):
                        output_optimized = gr.Markdown("等待运行...")
                    with gr.TabItem("编造审计"):
                        output_audit = gr.Markdown("等待运行...")
                    with gr.TabItem("优化前"):
                        output_before = gr.Markdown("等待运行...")
                    with gr.TabItem("优化要点"):
                        output_highlights = gr.Markdown("等待运行...")
                    with gr.TabItem("HR评分"):
                        output_score = gr.Markdown("等待运行...")
                    with gr.TabItem("Phase1"):
                        output_phase1 = gr.Markdown("等待运行...")
                    with gr.TabItem("Badcase分析"):
                        output_badcase = gr.Markdown("等待运行...\n\n运行优化后这里会显示会话统计数据。")

        # --- 事件绑定：添加/清空参考简历 ---
        def _add_reference(text: str, acc: list[str]) -> tuple[str, list[str], str]:
            if text and text.strip():
                acc = (acc or []) + [text.strip()]
            return "", acc, f"已添加: **{len(acc)}** 条"

        def _clear_references() -> tuple[list[str], str]:
            return [], "已添加: **0** 条"

        add_reference_btn.click(
            fn=_add_reference,
            inputs=[reference_paste_text, reference_paste_list],
            outputs=[reference_paste_text, reference_paste_list, reference_count_disp],
        )
        clear_references_btn.click(
            fn=_clear_references,
            inputs=[],
            outputs=[reference_paste_list, reference_count_disp],
        )

        # --- 事件绑定：模板管理 ---
        def _refresh_template_choices(sid: str):
            from src.crewai.database import init_db, get_templates
            init_db()
            templates = get_templates(session_id=sid)
            # Return (label, value) pairs for the dropdown
            return gr.update(choices=[
                (f"{t['name']} ({t['position_category']}, {t['created_at'][:10]})", t['id'])
                for t in templates
            ])

        def _track_ui_event(event_name: str, sid: str, value):
            from src.crewai.database import init_db, track_event
            init_db().close()
            track_event(event_name, sid or "default", properties={"source": str(value or "")[:200]})

        def _on_app_open(sid: str):
            _track_ui_event("app_open", sid, "gradio")
            return _refresh_template_choices(sid)

        def _save_template(
            sid: str, jd_src: str, jd_up: str, exp_src: str, exp_txt: str,
            pos_cat: str, ref_list: list[str],
        ):
            from src.crewai.database import init_db, save_template, get_templates
            from datetime import datetime

            # Determine JD content
            jd = (jd_up or "").strip()
            if not jd and jd_src:
                jd = load_jd_choices().get(jd_src, "")
            # Determine experience content
            exp = (exp_txt or "").strip()
            if not exp and exp_src:
                exp = load_experience_choices().get(exp_src, "")

            if not jd.strip() or not exp.strip():
                return gr.update()

            init_db()
            ts = datetime.now().strftime("%m%d_%H%M%S")
            name = f"{pos_cat}_{ts}" if pos_cat and pos_cat != "其他" else f"模板_{ts}"
            save_template(sid, name, jd, exp, pos_cat, ref_list or [])

            # Refresh dropdown
            templates = get_templates(session_id=sid)
            choices = [
                (f"{t['name']} ({t['position_category']}, {t['created_at'][:10]})", t['id'])
                for t in templates
            ]
            return gr.update(choices=choices, value=choices[-1][1] if choices else None)

        def _load_template(template_id: int | None):
            if template_id is None:
                return (
                    gr.update(), gr.update(), gr.update(),
                    gr.update(value=[]), gr.update(value="已添加: **0** 条"),
                )
            from src.crewai.database import init_db, get_template
            init_db()
            t = get_template(int(template_id))
            if not t:
                return (
                    gr.update(), gr.update(), gr.update(),
                    gr.update(value=[]), gr.update(value="已添加: **0** 条"),
                )
            refs = t.get("reference_resumes", []) or []
            cat = t.get("position_category", "互联网产品经理")
            cat = cat if cat in ["互联网产品经理", "硬件产品经理", "机械/风电塔相关", "BD（商务拓展）", "其他"] else "互联网产品经理"
            return (
                gr.update(value=t["jd_text"]),
                gr.update(value=t["exp_text"]),
                gr.update(value=cat),
                gr.update(value=refs),
                gr.update(value=f"已添加: **{len(refs)}** 条"),
            )

        save_template_btn.click(
            fn=_save_template,
            inputs=[session_id, jd_source, jd_upload, exp_source, exp_paste,
                    position_category, reference_paste_list],
            outputs=[template_select],
        )

        template_select.change(
            fn=_load_template,
            inputs=[template_select],
            outputs=[jd_upload, exp_paste, position_category, reference_paste_list, reference_count_disp],
        )

        # Refresh template list when session_id changes
        session_id.change(
            fn=_refresh_template_choices,
            inputs=[session_id],
            outputs=[template_select],
        )

        jd_source.change(fn=lambda sid, value: _track_ui_event("jd_loaded", sid, value),
                         inputs=[session_id, jd_source], outputs=[])
        jd_upload.blur(fn=lambda sid, value: _track_ui_event("jd_loaded", sid, "pasted" if value else ""),
                       inputs=[session_id, jd_upload], outputs=[])
        exp_source.change(fn=lambda sid, value: _track_ui_event("resume_loaded", sid, value),
                          inputs=[session_id, exp_source], outputs=[])
        exp_paste.blur(fn=lambda sid, value: _track_ui_event("resume_loaded", sid, "pasted" if value else ""),
                       inputs=[session_id, exp_paste], outputs=[])

        # --- 事件绑定：同步生成器直接传 fn ---
        run_btn.click(
            fn=run_optimization,
            inputs=[
                jd_source, jd_upload, exp_source, exp_paste, related_files,
                max_iterations, enable_human_rules, human_rules, session_id,
                position_category, reference_select, reference_paste_list,
                fabrication_tolerance,
            ],
            outputs=[
                output_status,
                output_optimized, output_audit, output_before,
                output_highlights, output_score, output_phase1, output_badcase,
            ],
        )

        # --- 页面加载时初始化模板下拉 ---
        app.load(
            fn=_on_app_open,
            inputs=[session_id],
            outputs=[template_select],
        )

    return app


if __name__ == "__main__":
    app = create_ui()
    app.queue(default_concurrency_limit=1)
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=True,
        inbrowser=True,
        theme=gr.themes.Soft(),
    )
