"""快速深度优化流水线

Phase 0: JD能力映射 (会话缓存) → Phase 1: 智能提取(一步到位) → Phase 2: 合成+评估(≤3轮) → Phase 3: 编造审计
"""

from datetime import datetime

from src.agents.llm_agent import LLMAgent
from src.agents.hr_screener import create_hr_agent, build_hr_evaluation, parse_hr_response
from config.prompts.deep_writer_prompt import (
    JD_ANALYZER_SYSTEM_PROMPT,
    JD_ANALYZER_TEMPLATE,
    SMART_EXTRACTOR_SYSTEM_PROMPT,
    SMART_EXTRACTOR_TEMPLATE,
    SYNTHESIZER_SYSTEM_PROMPT,
    SYNTHESIZER_TEMPLATE,
    SYNTHESIZER_ITERATION_TEMPLATE,
    FABRIC_AUDITOR_SYSTEM_PROMPT,
    FABRIC_AUDITOR_TEMPLATE,
)
from config.settings import settings
from config.prompts.hr_prompt import HR_WRITING_REQUIREMENTS


class DeepOptimizationPipeline:
    """快速深度优化流水线

    相比旧版：
    - Phase 1 合并了提取+压缩+数据挖掘，一步到位 400-600 字
    - Phase 0 JD 分析结果可跨 JD 缓存复用
    - 新增 Phase 3 编造审计
    """

    # 类级别缓存：跨实例共享 JD 分析结果
    _jd_cache: dict[str, str] = {}

    def __init__(self):
        self.jd_analyzer = LLMAgent(
            role_name="JD分析师",
            system_prompt=JD_ANALYZER_SYSTEM_PROMPT,
        )
        self.extractor = LLMAgent(
            role_name="智能提取师",
            system_prompt=SMART_EXTRACTOR_SYSTEM_PROMPT,
        )
        self.synthesizer = LLMAgent(
            role_name="简历撰写师",
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
        )
        self.auditor = LLMAgent(
            role_name="事实核查员",
            system_prompt=FABRIC_AUDITOR_SYSTEM_PROMPT,
        )
        self.history: list[dict] = []
        self.reference_resumes: list[str] = []

    # ================================================================
    # Phase 0: JD 能力映射（缓存）
    # ================================================================

    async def analyze_jd(self, jd: str) -> str:
        """分析JD，提取关键能力维度。结果按JD文本缓存。"""
        cache_key = jd[:200]  # 用JD前200字做缓存键
        if cache_key in self._jd_cache:
            return self._jd_cache[cache_key]

        ref_text = "\n---\n".join(self.reference_resumes[:3]) if self.reference_resumes else "（无参考简历）"
        analyzer_input = JD_ANALYZER_TEMPLATE.format(
            jd=jd,
            reference_resumes=ref_text,
        )
        result = await self.jd_analyzer.step(analyzer_input)
        self._jd_cache[cache_key] = result
        return result

    # ================================================================
    # 主流程
    # ================================================================

    async def run(
        self,
        jd: str,
        raw_experience: str,
        anchor_content: str = "",
        target_score: int | None = None,
        max_iterations: int | None = None,
    ) -> dict:
        """执行快速深度优化流水线

        Args:
            jd: 目标JD文本
            raw_experience: 原始详细经历
            anchor_content: 锚定内容（来自 test_cases，不可压缩，放在提取结果最前面）
            target_score: 目标分数，默认93
            max_iterations: 最大迭代轮数，默认3

        Returns:
            {
                "success": bool,
                "final_result": str,
                "final_score": int,
                "iterations": int,
                "jd_analysis": str,
                "phase1_summary": str,
                "fabrication_report": str,
                "history": list[dict],
            }
        """
        target = target_score or settings.target_score
        max_iter = max_iterations or 3

        # 截断
        content = raw_experience
        if len(content) > 12000:
            if settings.verbose:
                print(f"  [!] 内容过长({len(content)}字)，截取前12000字")
            content = content[:12000] + "\n\n... (内容已截断)"

        # ================================================================
        # Phase 0: JD 分析（首次调用，后续缓存命中）
        # ================================================================
        if settings.verbose:
            cached = " (缓存)" if jd[:200] in self._jd_cache else ""
            print(f"  [Phase 0] JD能力分析{cached}...")

        jd_analysis = await self.analyze_jd(jd)
        if settings.verbose and jd[:200] not in self._jd_cache:
            preview = jd_analysis[:150].replace("\n", " ")
            print(f"    分析完成 ({len(jd_analysis)}字): {preview}...")

        # ================================================================
        # Phase 1: 智能提取（一步到位 400-600 字，含数据）
        # ================================================================
        if settings.verbose:
            print(f"  [Phase 1] 智能提取 (目标400-600字)...")

        extractor_input = SMART_EXTRACTOR_TEMPLATE.format(
            anchor_content=anchor_content or "（无锚定内容）",
            raw_content=content,
        )
        phase1_summary = await self.extractor.step(extractor_input)
        if settings.verbose:
            print(f"    提取完成 ({len(phase1_summary)}字)")

        # ================================================================
        # Phase 2: 合成 + 迭代
        # ================================================================
        best_result = ""
        best_score = 0

        for iteration in range(1, max_iter + 1):
            if settings.verbose:
                print(f"  [Phase 2] 合成+评估 (第 {iteration}/{max_iter} 轮)")

            if iteration == 1:
                synth_input = SYNTHESIZER_TEMPLATE.format(
                    jd=jd,
                    content_summary=phase1_summary,
                    optimization_focus="首轮：只提炼通用产品能力，保持经历原始行业语境，不套用JD的行业场景。",
                    position_instructions="",
                    fabrication_guidance="数据必须来自原始经历摘要，不得编造。",
                )
                synth_input += f"\n\n{HR_WRITING_REQUIREMENTS}"
            else:
                last_record = self.history[-1]
                improvements = last_record["hr_feedback"].get("improvements", [])
                improvement_text = "\n".join(f"- {imp}" for imp in improvements[:5])
                synth_input = SYNTHESIZER_ITERATION_TEMPLATE.format(
                    jd=jd,
                    content_summary=phase1_summary,
                    hr_feedback=last_record["hr_feedback"].get("summary", ""),
                    previous_version=best_result or last_record["result"],
                    improvement_items=improvement_text or "进一步提升量化数据和STAR完整性",
                )

            synth_result = await self.synthesizer.step(synth_input)

            # HR 评估
            hr_input = build_hr_evaluation(
                jd, synth_result,
                reference_resumes=self.reference_resumes,
            )
            hr_agent = create_hr_agent()
            hr_response = await hr_agent.step(hr_input)
            hr_result = parse_hr_response(hr_response)
            current_score = hr_result["total_score"]

            record = {
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(),
                "result": synth_result,
                "score": current_score,
                "hr_feedback": hr_result,
            }
            self.history.append(record)

            if current_score > best_score:
                best_score = current_score
                best_result = synth_result

            if settings.verbose:
                print(f"    评分: {current_score}/100 (最佳: {best_score}/100)")

            if current_score >= target:
                if settings.verbose:
                    print(f"    [OK] 达标！{current_score} >= {target}")
                break

        # ================================================================
        # Phase 3: 编造审计
        # ================================================================
        if settings.verbose:
            print(f"  [Phase 3] 编造审计...")

        auditor_input = FABRIC_AUDITOR_TEMPLATE.format(
            source_material=phase1_summary,
            resume=best_result,
        )
        fabrication_report = await self.auditor.step(auditor_input)
        if settings.verbose:
            has_fabric = "编造" in fabrication_report and "未发现" not in fabrication_report
            print(f"    审计完成: {'[!] 发现编造内容' if has_fabric else '未发现编造'}")

        return {
            "success": best_score >= target,
            "final_result": best_result,
            "final_score": best_score,
            "iterations": len(self.history),
            "jd_analysis": jd_analysis,
            "phase1_summary": phase1_summary,
            "fabrication_report": fabrication_report,
            "history": self.history,
        }
