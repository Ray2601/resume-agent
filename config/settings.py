"""全局配置管理"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    """简历优化器全局配置"""

    # LLM配置
    model_platform: str = os.getenv("MODEL_PLATFORM", "OPENAI")
    model_type: str = os.getenv("MODEL_TYPE", "GPT_4O_MINI")
    api_key: str = os.getenv("API_KEY", "")
    base_url: str = os.getenv("BASE_URL", "")

    # 优化参数
    target_score: int = 93          # HR评分目标阈值（93分以上才通过）
    max_iterations: int = 10        # 最大迭代次数
    min_score_improvement: int = 3  # 每轮最小改进幅度

    # 输出配置
    output_dir: str = "output"
    save_reports: bool = True
    verbose: bool = True

    # 数据路径
    samples_dir: str = "data/samples"
    test_cases_path: str = "data/test_cases/test_cases.json"
    processed_dir: str = "data/processed"

    # CrewAI 配置
    crewai_verbose: bool = True
    crewai_process: str = "sequential"
    crewai_max_rpm: int = 10
    crewai_use_thinking: bool = True   # 是否启用 thinking 模式


# 全局单例
settings = Settings()
