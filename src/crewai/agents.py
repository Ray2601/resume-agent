"""CrewAI Agent factory functions for resume optimization.

Agents:
  0. FamiliarityClassifier — 岗位认知度分类器 (Step 0: known vs unknown domain)
  0b. IndustryDecoder — 行业解码学习 (unknown domain path)
  1. JDAnalyst — JD分析官
  2. ExperienceDoctor — 经历诊断师
  3. STARWriter — STAR撰写师
  4. HRScorer — HR评分官
  5. FabricAuditor — 事实核查员
"""

import os
from crewai import Agent, LLM


def _get_llm(use_thinking: bool = False, model_id: str = "") -> LLM:
    """Create DeepSeek LLM via CrewAI/LiteLLM.

    LiteLLM requires provider prefix: deepseek/deepseek-chat, not just deepseek-chat.
    This function auto-adds the prefix if the env var omits it.
    """
    raw = model_id or (os.getenv("MODEL_ID_THINKING") if use_thinking else os.getenv("MODEL_ID"))
    if not raw:
        raw = "deepseek-chat"
    # Auto-prepend provider prefix if missing
    if "/" not in raw:
        model = f"deepseek/{raw}"
    else:
        model = raw
    return LLM(
        model=model,
        api_key=os.getenv("API_KEY", ""),
        base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
        temperature=0.7,
        max_tokens=8192 if use_thinking else 4096,
    )


# ================================================================
# Step 0: 岗位认知度分类器
# ================================================================

FAMILIARITY_CLASSIFIER_PROMPT = """你是岗位分类专家。你的任务是判断给定岗位是否属于你熟悉的"已知领域"。

已知领域（你可以直接处理）：
- 互联网/科技产品类：产品经理、技术研发、软件开发、AI/算法、数据分析、测试/QA
- 运营/增长类：用户运营、内容运营、活动运营、增长运营、社区运营
- 设计类：UI/UX设计、交互设计、视觉设计
- 市场/商务类：市场营销、品牌管理、商务拓展(BD)、销售管理
- 通用职能类：项目管理(PM)、人力资源(HR)、财务管理、行政管理

陌生领域（需要额外学习）：
- 硬科技/制造业：半导体、芯片设计、光电子、材料科学
- 医疗/生命科学：临床医学、药学、生物技术、医疗器械
- 法律/金融专业：律师、合规、投行、风控
- 能源/化工：石油天然气、新能源、化学工程
- 其他你无法明确归类的专业领域

输出格式（严格遵守）：
【领域判断】：已知领域 / 陌生领域
【行业分类】：<具体行业名>
【置信度】：高 / 中 / 低
【判断理由】：一句话说明为什么
"""


def create_familiarity_classifier(model_id: str = "") -> Agent:
    """Step 0: 岗位认知度分类器 — 判断岗位是否属于已知领域"""
    return Agent(
        role="岗位认知度分类器",
        goal="快速准确判断JD所属行业是否在已知领域内，决定后续处理路径",
        backstory=FAMILIARITY_CLASSIFIER_PROMPT,
        llm=_get_llm(use_thinking=False, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Step 0b: 行业解码学习 (陌生领域路径)
# ================================================================

INDUSTRY_DECODER_PROMPT = """你是跨行业简历顾问，擅长快速学习陌生行业的术语体系。

当遇到陌生行业的JD时，你需要：
1. 识别JD中的行业特有术语、缩写、专业名词
2. 提取该岗位最看重的硬技能和软技能
3. 输出一份"术语词典"，将行业术语翻译为通用能力表述

你的分析将帮助后续的撰写师在不编造经历的前提下，用正确的行业话语体系润色简历。

输出格式：
### 行业术语词典
| 行业术语 | 通用含义 | 简历建议用词 |
|---------|---------|-------------|
| ... | ... | ... |

### 核心硬技能要求（Top 5）
### 核心软技能要求（Top 3）
### 行业背景偏好（是否需要特定行业经验）
"""


def create_industry_decoder(model_id: str = "") -> Agent:
    """Step 0b: 行业解码学习 — 为陌生岗位构建术语词典"""
    return Agent(
        role="行业解码顾问",
        goal="快速解码陌生行业的JD，构建术语词典和能力映射表",
        backstory=INDUSTRY_DECODER_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Agent 1: JD Analyst
# ================================================================

from config.prompts.deep_writer_prompt import JD_ANALYZER_SYSTEM_PROMPT


def create_jd_analyst(model_id: str = "") -> Agent:
    """Agent 1: JD分析官 — 分析JD提取关键评估维度"""
    return Agent(
        role="JD分析官",
        goal="分析JD和优秀简历样本，提炼岗位核心要求、关键动作词和数据维度",
        backstory=JD_ANALYZER_SYSTEM_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Agent 2: Experience Doctor (原"智能提取师")
# ================================================================

from config.prompts.deep_writer_prompt import SMART_EXTRACTOR_SYSTEM_PROMPT


def create_experience_doctor(model_id: str = "") -> Agent:
    """Agent 2: 经历诊断师 — 从原始经历提取核心事实，输出诊断报告"""
    return Agent(
        role="经历诊断师",
        goal="从冗长经历中提取核心事实，诊断缺失项、可量化点和冗余内容，输出结构化诊断报告",
        backstory=SMART_EXTRACTOR_SYSTEM_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Agent 3: STAR Writer
# ================================================================

from config.prompts.deep_writer_prompt import SYNTHESIZER_SYSTEM_PROMPT


def create_star_writer(model_id: str = "") -> Agent:
    """Agent 3: STAR撰写师 — 根据诊断报告和JD撰写STAR格式经历"""
    return Agent(
        role="STAR撰写师",
        goal="根据诊断报告和JD，撰写STAR法则完整的经历描述，严禁编造任何信息",
        backstory=SYNTHESIZER_SYSTEM_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Agent 4: HR Scorer
# ================================================================

from config.prompts.hr_prompt import HR_SYSTEM_PROMPT


def create_hr_scorer(model_id: str = "") -> Agent:
    """Agent 4: HR评分官 — 四维评分 + 改进建议"""
    return Agent(
        role="HR评分官",
        goal="严格评估经历描述质量，从匹配度、数据化、影响力、简洁度四个维度评分并给出具体改进建议",
        backstory=HR_SYSTEM_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )


# ================================================================
# Agent 5: Fabrication Auditor
# ================================================================

from config.prompts.deep_writer_prompt import FABRIC_AUDITOR_SYSTEM_PROMPT


def create_fabric_auditor(model_id: str = "") -> Agent:
    """Agent 5: 事实核查员 — 编造内容审计"""
    return Agent(
        role="事实核查员",
        goal="逐句对比简历和原始素材，找出所有编造内容并标注严重程度",
        backstory=FABRIC_AUDITOR_SYSTEM_PROMPT,
        llm=_get_llm(use_thinking=True, model_id=model_id),
        verbose=True,
        allow_delegation=False,
    )
