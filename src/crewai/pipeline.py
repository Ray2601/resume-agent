"""CrewAIResumePipeline — CrewAI-based multi-agent resume optimization.

Architecture:
  Step 0: FamiliarityClassifier → 分流
    ├── 已知领域: Agent 1→2→3↔4→5  (fast path)
    └── 陌生领域: Decoder→1→2→3↔4→5 (with industry glossary)

Every step's input/output is printed via verbose mode + callbacks.
The Phase 2 iteration loop is pure synchronous Python — breakpoint-friendly.

Caching layers (fastest → slowest):
  1. In-memory dict  — session-level, instant (jd_analysis, full_result)
  2. SQLite cache    — persistent, cross-session (jd_analysis, extraction, full_result)
  3. API call        — the real LLM call (slowest)
"""

import os
import re
import hashlib
import json
import time
import uuid
from datetime import datetime
from functools import lru_cache
from crewai import Crew, Process

from src.crewai.agents import (
    create_familiarity_classifier,
    create_industry_decoder,
    create_jd_analyst,
    create_experience_doctor,
    create_star_writer,
    create_hr_scorer,
    create_fabric_auditor,
)
from src.crewai.tasks import (
    create_familiarity_task,
    create_industry_learning_task,
    create_jd_analysis_task,
    create_extraction_task,
    create_synthesis_task,
    create_iteration_synthesis_task,
    create_hr_evaluation_task,
    create_audit_task,
)
from src.crewai.callbacks import StepLoggingCallback
from src.crewai.database import (
    save_record, get_cached, set_cached, init_db, save_trace, get_run_traces, track_event,
)
from src.agents.hr_screener import parse_hr_response
from config.settings import settings
from config.prompts.writer_prompt import WRITER_PROMPT_VERSION
from config.prompts.hr_prompt import HR_PROMPT_VERSION
from config.prompts.deep_writer_prompt import FACT_CHECKER_PROMPT_VERSION


class PipelineStageError(RuntimeError):
    """Agent call failure with enough context for API and Neon diagnostics."""
    def __init__(self, stage: str, agent: str, iteration: int, call_number: int, original: Exception):
        self.stage = stage
        self.agent = agent
        self.iteration = iteration
        self.call_number = call_number
        self.original = original
        super().__init__(
            f"LLM call failed: stage={stage}, agent={agent}, iteration={iteration}, "
            f"call_number={call_number}: {original}"
        )


class CrewAIResumePipeline:
    """CrewAI-based resume optimization pipeline.

    Usage:
        pipeline = CrewAIResumePipeline()
        pipeline.reference_resumes = [...]  # optional
        result = pipeline.run(jd="...", raw_experience="...")
        # result is a dict with final_result, final_score, history, etc.
    """

    # Class-level in-memory caches (shared across all instances in the same process)
    _jd_cache: dict[str, str] = {}           # JD analysis by jd[:200]
    _result_cache: dict[str, dict] = {}      # Full optimization results (LRU, max 64 entries)
    _RESULT_CACHE_MAX = 64

    def __init__(self, model_id: str = ""):
        self._familiarity = create_familiarity_classifier(model_id)
        self._decoder = create_industry_decoder(model_id)
        self._jd_analyst = create_jd_analyst(model_id)
        self._doctor = create_experience_doctor(model_id)
        self._writer = create_star_writer(model_id)
        self._hr_scorer = create_hr_scorer(model_id)
        self._auditor = create_fabric_auditor(model_id)
        self._callback = StepLoggingCallback(verbose=settings.verbose)
        self.reference_resumes: list[str] = []
        self.history: list[dict] = []
        self.run_id = ""
        self._run_db_path: str | None = None
        self._llm_call_count = 0

    def _trace(self, step_name: str, agent_name: str, task_input: str, action,
               iteration: int = 0, score_getter=None):
        """Execute one agent call and persist its full Harness trace."""
        started = time.perf_counter()
        last_error = None
        for attempt in range(1, 4):
            self._llm_call_count += 1
            call_number = self._llm_call_count
            try:
                output = action()
                text = str(output.raw) if hasattr(output, "raw") else str(output)
                score = score_getter(text) if score_getter else None
                save_trace(self.run_id, step_name, agent_name, iteration, task_input, text,
                           score, int((time.perf_counter() - started) * 1000), 0, "success",
                           self._run_db_path)
                return output
            except Exception as exc:
                last_error = exc
                is_empty_response = "Invalid response from LLM call - None or empty" in str(exc)
                if is_empty_response and attempt < 3:
                    time.sleep(attempt)
                    continue
                save_trace(self.run_id, step_name, agent_name, iteration, task_input, str(exc),
                           None, int((time.perf_counter() - started) * 1000), 0, "failed",
                           self._run_db_path)
                raise PipelineStageError(step_name, agent_name, iteration, call_number, exc) from exc
        raise PipelineStageError(step_name, agent_name, iteration, self._llm_call_count, last_error)

    def _trace_cached(self, step_name: str, agent_name: str, task_input: str, output: str):
        save_trace(self.run_id, step_name, agent_name, 0, task_input, output,
                   None, 0, 0, "cached", self._run_db_path)

    # ================================================================
    # Cache helpers
    # ================================================================

    @staticmethod
    def _make_result_cache_key(jd: str, raw_exp: str) -> str:
        """Generate a composite cache key for full optimization results."""
        jd_hash = hashlib.md5(jd[:2000].encode()).hexdigest()
        exp_hash = hashlib.md5(raw_exp[:2000].encode()).hexdigest()
        return f"{jd_hash}:{exp_hash}"

    @classmethod
    def _check_result_cache(cls, cache_key: str) -> dict | None:
        """Check in-memory result cache. Returns None on miss."""
        if cache_key in cls._result_cache:
            if settings.verbose:
                print(f"  ⚡ [ResultCache HIT] 直接返回缓存结果 (跳过API调用)")
            # Move to end for LRU
            result = cls._result_cache.pop(cache_key)
            cls._result_cache[cache_key] = result
            return result
        return None

    @classmethod
    def _set_result_cache(cls, cache_key: str, result: dict):
        """Store result in in-memory cache with LRU eviction."""
        if cache_key in cls._result_cache:
            cls._result_cache.pop(cache_key)
        elif len(cls._result_cache) >= cls._RESULT_CACHE_MAX:
            # Evict oldest (first key)
            oldest = next(iter(cls._result_cache))
            cls._result_cache.pop(oldest)
        cls._result_cache[cache_key] = result

    # ================================================================
    # Word limit enforcement (pure post-processing, 0 API cost)
    # ================================================================

    MAX_CHARS_PER_BULLET = 85

    @staticmethod
    def _count_chinese_chars(text: str) -> int:
        """Count Chinese characters (CJK + Chinese punctuation) in a string."""
        count = 0
        for ch in text:
            cp = ord(ch)
            if (0x4E00 <= cp <= 0x9FFF or      # CJK Unified
                0x3400 <= cp <= 0x4DBF or       # CJK Extension A
                0x20000 <= cp <= 0x2A6DF or     # CJK Extension B
                0x3000 <= cp <= 0x303F or       # CJK punctuation
                0xFF00 <= cp <= 0xFFEF or       # Fullwidth forms
                0xFE30 <= cp <= 0xFE4F):        # CJK Compatibility
                count += 1
        return count

    @staticmethod
    def _split_bullet_at_boundary(text: str, max_chars: int) -> list[str]:
        """Split an over-length bullet at the best sentence boundary.

        Tries: 。→ ；→ ，→ space → forced split at max_chars.
        Returns list of sub-bullets, each within the limit.
        """
        parts = []
        remaining = text

        while CrewAIResumePipeline._count_chinese_chars(remaining) > max_chars:
            # Find the best split point within range [max_chars//2, max_chars]
            search_start = max_chars // 2
            search_text = remaining[:max_chars]

            # Try to find sentence break: 。
            split_at = -1
            for delimiter in ['。', '；', '，', '；', '、']:
                # Find last occurrence of delimiter within the limit
                pos = search_text.rfind(delimiter, search_start)
                if pos > search_start:
                    split_at = pos + 1  # include the delimiter
                    break

            if split_at < 0:
                # No good delimiter found, try space before a CJK char
                for i in range(max_chars - 1, search_start, -1):
                    if remaining[i] == ' ' or remaining[i] == '\n':
                        split_at = i
                        break

            if split_at < 0:
                # Force split at max_chars
                split_at = max_chars

            parts.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

        if remaining.strip():
            parts.append(remaining.strip())

        return parts

    @classmethod
    def enforce_word_limit(cls, text: str) -> tuple[str, int]:
        """Check and enforce 85-char limit on all bullet points.

        Parses bullet points (lines starting with -, *, •, 1., etc.),
        splits any that exceed MAX_CHARS_PER_BULLET, and returns
        corrected text + count of violations fixed.

        Runs in < 1ms — no API cost, no display delay.
        """
        lines = text.split('\n')
        fixed_lines = []
        violations = 0

        bullet_pattern = re.compile(
            r'^(\s*)(?:[-*•]|\d+[.、．])\s*(.*)$'
        )

        for line in lines:
            m = bullet_pattern.match(line)
            if not m:
                fixed_lines.append(line)
                continue

            indent = m.group(1)
            content = m.group(2)
            char_count = cls._count_chinese_chars(content)

            if char_count <= cls.MAX_CHARS_PER_BULLET:
                fixed_lines.append(line)
                continue

            # Over limit — split into sub-bullets
            violations += 1
            sub_parts = cls._split_bullet_at_boundary(content, cls.MAX_CHARS_PER_BULLET)

            for i, part in enumerate(sub_parts):
                if i == 0:
                    fixed_lines.append(f"{indent}- {part}")
                else:
                    # Sub-bullets get extra indent
                    fixed_lines.append(f"{indent}  - {part}")

        corrected = '\n'.join(fixed_lines)
        return corrected, violations

    # ================================================================
    # Public API
    # ================================================================

    def run(
        self,
        jd: str,
        raw_experience: str,
        anchor_content: str = "",
        target_score: int | None = None,
        max_iterations: int | None = None,
        human_rules: str = "",
        session_id: str = "default",
        db_path: str | None = None,
        progress_callback: callable = None,
        position_category: str = "",
        reference_uploads: list[str] | None = None,
        fabrication_tolerance: int = 0,
        writer_prompt_version: str = WRITER_PROMPT_VERSION,
        hr_prompt_version: str = HR_PROMPT_VERSION,
        experiment_id: str = "",
        case_id: str = "",
        model_id: str = "",
    ) -> dict:
        """Execute the full multi-agent pipeline.

        Args:
            jd: 目标JD文本
            raw_experience: 原始经历文本
            anchor_content: 锚定内容（不可压缩，放在最前面）
            target_score: 目标分数 (默认93)
            max_iterations: 最大迭代轮数 (默认3)
            human_rules: 用户自定义规则
            session_id: 会话标识
            db_path: SQLite数据库路径
            fabrication_tolerance: 数据虚构容忍度 0-60，默认0

        Returns:
            dict with: success, final_result, final_score, iterations,
                       jd_analysis, phase1_summary, fabrication_report, history,
                       record_id, familiarity, industry_glossary (陌生领域时)
        """
        target = target_score or settings.target_score
        max_iter = max_iterations or 3
        effective_model_id = model_id or os.getenv("MODEL_ID_THINKING") or os.getenv("MODEL_ID") or settings.model_type
        if hr_prompt_version != HR_PROMPT_VERSION:
            raise ValueError(f"Unsupported HR prompt version: {hr_prompt_version}")
        from src.prompt_optimization.version_manager import get_prompt_version
        writer_version_record = get_prompt_version(writer_prompt_version, db_path)
        writer_prompt_content = writer_version_record["prompt_content"]
        self.history = []
        self._llm_call_count = 0
        self.run_id = f"run_{uuid.uuid4().hex}"
        self._run_db_path = db_path
        init_db(db_path).close()
        run_started = time.perf_counter()
        track_event("optimization_started", session_id, self.run_id, {
            "writer_prompt_version": writer_prompt_version,
            "target_score": target, "max_iterations": max_iter,
            "fabrication_tolerance": fabrication_tolerance,
        }, db_path)

        # Merge uploaded reference resumes
        if reference_uploads:
            for fpath in reference_uploads:
                if fpath and os.path.isfile(fpath):
                    with open(fpath, "r", encoding="utf-8") as f:
                        self.reference_resumes.append(f.read())

        # Full-result cache is intentionally not used by Harness runs: every run must
        # produce its own trace and metrics. Stage-level caches remain enabled.
        result_cache_key = self._make_result_cache_key(jd, raw_experience)

        def _progress(step: str):
            if progress_callback:
                progress_callback(step)

        # ----------------------------------------------------------
        # Step 0: 岗位认知度分类 + 分流
        # ----------------------------------------------------------
        _progress("step0")
        familiarity_result = self._run_familiarity_check(jd)
        is_known = familiarity_result["is_known"]
        industry_glossary = ""

        if not is_known:
            # 陌生领域路径: 行业解码学习
            _progress("step0b")
            industry_glossary = self._run_industry_decoding(jd)
            print(f"    陌生领域 — 已完成行业解码 ({len(industry_glossary)} 字术语词典)")

        # ----------------------------------------------------------
        # Phase 0: JD分析
        # ----------------------------------------------------------
        _progress("phase0")
        jd_analysis = self._run_jd_analysis(jd, industry_glossary)

        # ----------------------------------------------------------
        # Phase 1: 经历诊断 + 提取
        # ----------------------------------------------------------
        _progress("phase1")
        phase1_summary = self._run_extraction(raw_experience, anchor_content)

        # ----------------------------------------------------------
        # Phase 2: STAR撰写 ↔ HR评分 多轮迭代
        # ----------------------------------------------------------
        best_result, best_score = self._run_iteration_loop(
            jd=jd,
            phase1_summary=phase1_summary,
            target_score=target,
            max_iterations=max_iter,
            human_rules=human_rules,
            industry_glossary=industry_glossary,
            progress_callback=progress_callback,
            position_category=position_category,
            fabrication_tolerance=fabrication_tolerance,
            writer_prompt_version=writer_prompt_version,
            writer_prompt_content=writer_prompt_content,
        )

        # ----------------------------------------------------------
        # Phase 3: 编造审计
        # ----------------------------------------------------------
        _progress("phase3")
        fabrication_report = self._run_audit(phase1_summary, best_result)

        # Extract scores from last HR evaluation
        last_hr = self.history[-1]["hr_feedback"] if self.history else {}
        match_score = last_hr.get("match_score", 0)
        data_score = last_hr.get("data_score", 0)
        impact_score = last_hr.get("impact_score", 0)
        conciseness_score = last_hr.get("brevity_score", 0)

        # Auto-save to database
        status = "success" if best_score >= target else (
            "timeout" if len(self.history) >= max_iter else "failed"
        )
        record_id = save_record(
            session_id=session_id,
            jd_text=jd,
            original_exp=raw_experience,
            optimized_output=best_result,
            match_score=match_score,
            data_score=data_score,
            impact_score=impact_score,
            conciseness_score=conciseness_score,
            total_score=best_score,
            iterations=len(self.history),
            status=status,
            hr_recommendation=last_hr.get("recommendation", ""),
            hr_summary=last_hr.get("summary", ""),
            fabrication_report=fabrication_report,
            familiarity=familiarity_result.get("raw", ""),
            is_known_domain=familiarity_result["is_known"],
            industry_glossary=industry_glossary,
            human_rules=human_rules,
            anchor_content=anchor_content,
            iteration_history=self.history,
            max_iterations=max_iter,
            run_id=self.run_id,
            writer_prompt_version=writer_prompt_version,
            hr_prompt_version=hr_prompt_version,
            fact_checker_prompt_version=FACT_CHECKER_PROMPT_VERSION,
            model_id=effective_model_id,
            target_score=target,
            fabrication_tolerance=fabrication_tolerance,
            latency_ms=int((time.perf_counter() - run_started) * 1000),
            token_total=0,
            experiment_id=experiment_id,
            case_id=case_id,
            db_path=db_path,
        )

        result = {
            "success": best_score >= target,
            "final_result": best_result,
            "final_score": best_score,
            "iterations": len(self.history),
            "jd_analysis": jd_analysis,
            "phase1_summary": phase1_summary,
            "fabrication_report": fabrication_report,
            "history": self.history,
            "familiarity": familiarity_result,
            "industry_glossary": industry_glossary,
            "record_id": record_id,
            "run_id": self.run_id,
            "prompt_versions": {"writer": writer_prompt_version, "hr": hr_prompt_version,
                                "fact_checker": FACT_CHECKER_PROMPT_VERSION},
            "eval_metrics": {"match": match_score, "data": data_score,
                             "impact": impact_score, "conciseness": conciseness_score,
                             "total": best_score},
        }
        result["traces"] = get_run_traces(self.run_id, db_path)
        track_event("optimization_completed", session_id, self.run_id, {
            "score": best_score, "iterations": len(self.history),
            "prompt_version": writer_prompt_version,
            "fabrication_count": 0 if "未发现" in fabrication_report else fabrication_report.count("编造"),
            "latency_ms": int((time.perf_counter() - run_started) * 1000),
        }, db_path)

        # Cache the full result for future reuse
        self._set_result_cache(result_cache_key, result)
        try:
            cache_text = jd[:2000] + "\n---EXP---\n" + raw_experience[:2000]
            set_cached("full_result", cache_text, json.dumps(result, ensure_ascii=False), db_path)
        except Exception:
            pass  # Cache write failure is non-fatal

        return result

    # ================================================================
    # Step 0: Familiarity Check
    # ================================================================

    def _run_familiarity_check(self, jd: str) -> dict:
        """判断岗位属于已知领域还是陌生领域。"""
        if settings.verbose:
            print(f"\n{'=' * 60}")
            print(f"  [Step 0] 岗位认知度分类...")
            print(f"{'=' * 60}")

        # Cache check (persistent)
        cached = get_cached("familiarity", jd[:3000], self._run_db_path)
        if cached:
            try:
                self._trace_cached("position_classifier", "Position Classifier", jd, cached)
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

        task = create_familiarity_task(self._familiarity, jd)
        result = self._trace(
            "position_classifier", "Position Classifier", task.description,
            lambda: self._familiarity.execute_task(task, context=None),
        )
        raw = str(result)

        # Parse the response
        is_known = "已知" in raw and "陌生" not in raw.split("【领域判断】")[-1].split("\n")[0]

        # Extract industry category
        industry = ""
        for line in raw.split("\n"):
            if "行业分类" in line:
                industry = line.split("】", 1)[-1].strip() if "】" in line else line.split("：", 1)[-1].strip()
                break

        parsed = {
            "is_known": is_known,
            "industry": industry,
            "raw": raw,
        }

        # Cache the result
        set_cached("familiarity", jd[:3000], json.dumps(parsed, ensure_ascii=False), self._run_db_path)

        if settings.verbose:
            path_label = "已知领域 → 快速路径" if is_known else "陌生领域 → 行业解码"
            print(f"    分类结果: {parsed['industry'] or '未识别'} → {path_label}")

        return parsed

    # ================================================================
    # Step 0b: Industry Decoding (陌生领域)
    # ================================================================

    def _run_industry_decoding(self, jd: str) -> str:
        """为陌生领域构建行业术语词典。"""
        if settings.verbose:
            print(f"  [Step 0b] 行业解码学习...")

        cached = get_cached("industry_decoding", jd[:3000], self._run_db_path)
        if cached:
            self._trace_cached("industry_decoding", "Industry Decoder", jd, cached)
            if settings.verbose:
                print(f"    解码完成 (缓存命中, {len(cached)} 字)")
            return cached

        task = create_industry_learning_task(self._decoder, jd)
        task.callback = self._callback.on_step
        result = self._trace(
            "industry_decoding", "Industry Decoder", task.description,
            lambda: self._decoder.execute_task(task, context=None),
        )

        glossary = str(result)
        set_cached("industry_decoding", jd[:3000], glossary, self._run_db_path)
        if settings.verbose:
            print(f"    解码完成 ({len(glossary)} 字)")

        return glossary

    # ================================================================
    # Phase 0: JD Analysis
    # ================================================================

    def _run_jd_analysis(self, jd: str, industry_glossary: str = "") -> str:
        """JD分析（三级缓存：内存 → SQLite → API调用）。"""
        cache_key = jd[:200]

        # Level 1: In-memory cache
        if cache_key in self._jd_cache:
            self._trace_cached("jd_analysis", "JD Analyst", jd, self._jd_cache[cache_key])
            if settings.verbose:
                print(f"  [Phase 0] JD分析 (内存缓存命中)")
            return self._jd_cache[cache_key]

        # Level 2: SQLite persistent cache
        cached = get_cached("jd_analysis", jd[:3000], self._run_db_path)
        if cached:
            self._trace_cached("jd_analysis", "JD Analyst", jd, cached)
            self._jd_cache[cache_key] = cached
            if settings.verbose:
                print(f"  [Phase 0] JD分析 (持久缓存命中)")
            return cached

        if settings.verbose:
            print(f"  [Phase 0] JD分析...")

        task = create_jd_analysis_task(
            self._jd_analyst, jd, self.reference_resumes, industry_glossary,
        )
        task.callback = self._callback.on_step

        crew = Crew(
            agents=[self._jd_analyst],
            tasks=[task],
            process=Process.sequential,
            verbose=settings.verbose,
            step_callback=self._callback.on_step,
        )
        result = self._trace("jd_analysis", "JD Analyst", task.description, crew.kickoff)
        jd_analysis = str(result.raw) if hasattr(result, 'raw') else str(result)
        self._jd_cache[cache_key] = jd_analysis
        set_cached("jd_analysis", jd[:3000], jd_analysis, self._run_db_path)

        if settings.verbose:
            preview = jd_analysis[:150].replace("\n", " ")
            print(f"    分析完成 ({len(jd_analysis)} 字): {preview}...")

        return jd_analysis

    # ================================================================
    # Phase 1: Experience Extraction / Diagnosis
    # ================================================================

    def _run_extraction(self, raw_experience: str, anchor_content: str) -> str:
        """经历诊断提取 — 200-400字事实 + 诊断报告。"""
        if settings.verbose:
            print(f"  [Phase 1] 经历诊断...")

        # Cache check (key includes anchor_content since it affects output)
        cache_text = raw_experience[:3000] + ("|anchor|" + anchor_content[:1000] if anchor_content else "")
        cached = get_cached("experience_diagnosis", cache_text, self._run_db_path)
        if cached:
            self._trace_cached("experience_diagnosis", "Experience Doctor", cache_text, cached)
            if settings.verbose:
                print(f"    诊断完成 (缓存命中, {len(cached)} 字)")
            return cached

        task = create_extraction_task(self._doctor, raw_experience, anchor_content)
        task.callback = self._callback.on_step

        crew = Crew(
            agents=[self._doctor],
            tasks=[task],
            process=Process.sequential,
            verbose=settings.verbose,
            step_callback=self._callback.on_step,
        )
        result = self._trace("experience_diagnosis", "Experience Doctor", task.description, crew.kickoff)
        phase1_summary = str(result.raw) if hasattr(result, 'raw') else str(result)

        set_cached("experience_diagnosis", cache_text, phase1_summary, self._run_db_path)

        if settings.verbose:
            print(f"    诊断完成 ({len(phase1_summary)} 字)")

        return phase1_summary

    # ================================================================
    # Phase 2: STAR Writer ↔ HR Scorer Iteration
    # ================================================================

    def _run_iteration_loop(
        self,
        jd: str,
        phase1_summary: str,
        target_score: int,
        max_iterations: int,
        human_rules: str,
        industry_glossary: str,
        progress_callback=None,
        position_category: str = "",
        fabrication_tolerance: int = 0,
        writer_prompt_version: str = WRITER_PROMPT_VERSION,
        writer_prompt_content: str | None = None,
    ) -> tuple[str, int]:
        """Python for-loop iteration between STARWriter and HRScorer.

        This is intentionally a plain synchronous loop — breakpoint anywhere.
        """
        best_result = ""
        best_score = 0

        for iteration in range(1, max_iterations + 1):
            if progress_callback:
                progress_callback(f"phase2_write_{iteration}")
            if settings.verbose:
                print(f"\n  ── [Phase 2] 第 {iteration}/{max_iterations} 轮 ──")

            # --- 撰写 ---
            if iteration == 1:
                synth_task = create_synthesis_task(
                    self._writer, jd, phase1_summary,
                    optimization_focus="首轮：只提炼通用产品能力，保持经历原始行业语境，不套用JD的行业场景。",
                    human_rules=human_rules,
                    industry_glossary=industry_glossary,
                    position_category=position_category,
                    fabrication_tolerance=fabrication_tolerance,
                    writer_prompt_version=writer_prompt_version,
                    writer_prompt_content=writer_prompt_content,
                )
            else:
                last = self.history[-1]
                improvements = last["hr_feedback"].get("improvements", [])
                synth_task = create_iteration_synthesis_task(
                    self._writer, jd, phase1_summary,
                    hr_feedback=last["hr_feedback"].get("summary", ""),
                    previous_version=best_result or last["result"],
                    improvement_items="\n".join(f"- {i}" for i in improvements[:5])
                    or "进一步提升量化数据和STAR完整性",
                    human_rules=human_rules,
                    industry_glossary=industry_glossary,
                    position_category=position_category,
                    fabrication_tolerance=fabrication_tolerance,
                    writer_prompt_version=writer_prompt_version,
                    writer_prompt_content=writer_prompt_content,
                )

            synth_task.callback = self._callback.on_step
            synth_result = self._trace(
                "star_writer", "STAR Writer", synth_task.description,
                lambda: self._writer.execute_task(synth_task, context=None), iteration,
            )
            synth_text = str(synth_result)

            # --- 字数硬校验：检查每条bullet不超过85中文字，超标自动拆分 ---
            synth_text, word_violations = self.enforce_word_limit(synth_text)
            if word_violations > 0 and settings.verbose:
                print(f"    ✂️ 字数校验：自动拆分 {word_violations} 条超标要点")

            # --- HR评分 ---
            if progress_callback:
                progress_callback(f"phase2_score_{iteration}")

            hr_task = create_hr_evaluation_task(
                self._hr_scorer, jd, synth_text, self.reference_resumes,
            )
            hr_task.callback = self._callback.on_step
            hr_raw = self._trace(
                "hr_reviewer", "HR Reviewer", hr_task.description,
                lambda: self._hr_scorer.execute_task(hr_task, context=None), iteration,
                lambda text: parse_hr_response(text)["total_score"],
            )
            hr_result = parse_hr_response(str(hr_raw))
            current_score = hr_result["total_score"]

            record = {
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(),
                "result": synth_text,
                "score": current_score,
                "hr_feedback": hr_result,
            }
            self.history.append(record)

            if current_score > best_score:
                best_score = current_score
                best_result = synth_text

            if settings.verbose:
                dimensions = (
                    f"匹配{hr_result['match_score']}/数据{hr_result['data_score']}"
                    f"/影响{hr_result['impact_score']}/简洁{hr_result['brevity_score']}"
                )
                status = "✅ 达标!" if current_score >= target_score else "→ 继续迭代"
                print(f"    评分: {current_score}/100 ({dimensions}) | 最佳: {best_score} {status}")

            if current_score >= target_score:
                break

            # --- 早停机制：分数下降时立即止损 ---
            if iteration >= 2 and current_score < best_score:
                if current_score < 80:
                    if settings.verbose:
                        print(f"    🛑 第{iteration}轮分数下降至{current_score}(<80)且低于最佳{best_score}，立即止损")
                    break
                # Consecutive decline check
                prev_score = self.history[-2]["score"] if len(self.history) >= 2 else 0
                if current_score < prev_score:
                    if settings.verbose:
                        print(f"    🛑 连续两轮分数下降({prev_score}→{current_score})，立即止损，返回最佳版本({best_score})")
                    break
        else:
            if settings.verbose:
                print(f"    ⚠ 已达最大迭代轮次({max_iterations})，取最佳结果({best_score}/100)")

        return best_result, best_score

    # ================================================================
    # Phase 3: Fabrication Audit
    # ================================================================

    def _run_audit(self, source_material: str, resume: str) -> str:
        """编造审计 — 逐句对比素材和最终简历。"""
        if settings.verbose:
            print(f"\n  [Phase 3] 编造审计...")

        task = create_audit_task(self._auditor, source_material, resume)
        task.callback = self._callback.on_step

        crew = Crew(
            agents=[self._auditor],
            tasks=[task],
            process=Process.sequential,
            verbose=settings.verbose,
            step_callback=self._callback.on_step,
        )
        result = self._trace("fact_check", "Fact Checker", task.description, crew.kickoff)
        report = str(result.raw) if hasattr(result, 'raw') else str(result)

        if settings.verbose:
            has_fabric = "编造" in report and "未发现" not in report
            print(f"    审计完成: {'[!] 发现编造内容' if has_fabric else '✅ 未发现编造'}")

        return report
