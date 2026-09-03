# 简历深度优化器（AI 员工团队）

基于 **CrewAI** 的多智能体协作系统，实现「岗位解析 → 经历诊断 → STAR 撰写 → HR 评分 → 迭代优化 → 编造审计」的自动化闭环。提供 Web UI（Gradio）与命令行两种入口，支持 natapp 内网穿透对外服务。

---

## 架构概览

```
用户输入（JD + 经历 + 参考简历 + 参数）
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│                 CrewAIResumePipeline（编排层）            │
├─────────────────────────────────────────────────────────┤
│  Step 0  岗位认知度分类器        → 已知领域 / 陌生领域     │
│  Step 0b 行业解码顾问（仅陌生领域） → 术语词典           │
│  Phase 0 JD分析官               → 关键词 + 数据维度 + 优先级│
│  Phase 1 经历诊断师             → 结构化事实 + 诊断报告    │
│  Phase 2 STAR撰写师 ↔ HR评分官  → 多轮迭代（带早停）      │
│  Phase 3 事实核查员             → 编造内容审计             │
└─────────────────────────────────────────────────────────┘
        │
        ▼
  优化结果 + 四维评分 + 审计报告 + Badcase 分析
```

## 核心特性

### 1. 多智能体分工（7 个 Agent）

| Agent | 职责 | 模型模式 |
|-------|------|---------|
| 岗位认知度分类器 | 判断岗位是否属于已知领域，决定处理路径 | 非思考 |
| 行业解码顾问 | 陌生领域构建术语词典与能力映射 | 思考 |
| JD 分析官 | 提炼关键动作词、数据维度、能力优先级 | 思考 |
| 经历诊断师 | 提取核心事实，诊断缺失项/可量化点/冗余点 | 思考 |
| STAR 撰写师 | 按 STAR 法则撰写经历，严格字数控制 | 思考 |
| HR 评分官 | 四维评分（匹配度/数据化/影响力/简洁度） | 思考 |
| 事实核查员 | 逐句对比素材与简历，审计编造内容 | 思考 |

### 2. 三级缓存（提速）

| 层级 | 实现 | 命中场景 | 效果 |
|------|------|---------|------|
| 内存缓存 | 类级 `dict` + LRU | 同一进程内重复请求 | 秒级返回 |
| 持久缓存 | SQLite `cache` 表 | 跨会话/重启复用 | 跳过 LLM 调用 |
| API 调用 | 真实 LLM | 首次请求 | 全流程 |

缓存对象：`familiarity`、`industry_decoding`、`jd_analysis`、`experience_diagnosis`、`full_result`（完整结果直接秒出）。

### 3. 迭代稳定性（防"越改越差"）

针对多 Agent 循环退化问题，实施三项强制约束：

- **锚定原始输入**：每轮迭代 prompt 中，原始经历摘要和 JD 作为 `🔒 不可变上下文锚点` 固定在最前，防止语境漂移。
- **差异补丁策略**：要求保留上一版 90% 内容，仅修改 HR 点名批评的句子，防止过度修正。
- **早停机制**：分数下降至 80 以下，或连续两轮下降时立即止损，返回历史最优版本。

### 4. 字数硬校验（85 中文字/条）

- **Prompt 层**：所有撰写 prompt 强制声明「每条要点 ≤ 85 中文字」。
- **代码层**：`enforce_word_limit()` 在每轮 Writer 输出后、HR 评分前自动校验，超标 bullet 在句号/分号/逗号处智能拆分（纯 Python，<1ms，零 API 成本）。

### 5. 数据虚构容忍度（0%–60%）

UI 滑块控制撰写师的编造自由度：

| 级别 | 范围 | 规则 |
|------|------|------|
| 严禁 | 0% | 所有数字必须有素材来源，缺失标注【需用户补充】 |
| 极低 | 5-15% | 方向性描述可估算，核心指标不编造 |
| 中等 | 20-35% | 可按行业常识补充 JD 关注指标，要求逻辑自洽 |
| 较高 | 40-60% | 自由补充合理数据，禁编极端异常值和虚构项目名 |

### 6. 数据收集与 Badcase 分析

每次优化自动落库到 SQLite（`data/optimization.db`），支持：

- 完整记录（JD、经历、输出、四维评分、迭代历史、审计报告）
- 错误自动分类（数据空洞/STAR缺失/编造/关键词缺失/超时等）
- 会话统计、平均分、错误分布、最近记录
- 模板保存与复用（历史 JD+经历一键回填）

### 7. Harness v1

- 每次优化生成独立 `run_id`，记录 Writer/HR/Fact Checker Prompt 版本、模型、目标分、迭代上限、虚构容忍度和总耗时。
- 将匹配度、数据化、影响力、简洁度和总分固定为 `eval_metrics`。
- `agent_traces` 保存每次 Agent 执行的输入、输出、轮次、评分、耗时和状态；缓存命中也会记录为 `cached`。
- `events` 保存 `app_open`、素材加载、优化开始/完成/失败、结果与 Badcase 查看等核心事件。
- `badcase_labels` 支持一次 Run 命中多个错误标签，同时保留旧 `error_type` 主错误字段兼容已有查询。
- Web UI 的 Badcase 页顶部展示 Harness Console。底层暂未稳定返回 token usage 时，`token_total/token_count` 明确记为 `0`，不做推测。

### 8. 固定数据集 Prompt Evaluation

第二阶段提供 `dataset_v1.0` 的20条固定 Case、实验批量运行和版本对比：

```bash
python scripts/seed_eval_cases.py
python scripts/run_evaluation.py run --writer writer_v1.0 --name baseline
python scripts/run_evaluation.py run --writer writer_v1.1 --name candidate
python scripts/run_evaluation.py compare <baseline_experiment_id> <candidate_experiment_id>
```

比较结果包含四维平均分、总分、通过率、编造率、Badcase率、平均迭代轮数、平均耗时，以及每个 Case 的 `improved / stable / regressed` 判断。`writer_v1.0` 是未前置 HR 验收标准的基线，`writer_v1.1` 是当前首轮前置标准的候选版本。

### 9. 半自动 Prompt Optimization Loop

第三阶段按人工审核门禁执行：

```bash
# 按多标签、岗位类型聚类，可选传入基线实验计算 score delta
python scripts/optimize_prompt.py cluster <experiment_id> --baseline <baseline_experiment_id>
python scripts/optimize_prompt.py root-cause <cluster_id>

# LLM 只生成 draft，approve 是显式人工动作
python scripts/optimize_prompt.py propose <cluster_id>
python scripts/optimize_prompt.py approve <patch_id>

# Candidate 自动跑同一固定集并给出 PASS/REJECT；不会自动上线
python scripts/optimize_prompt.py validate <patch_id> <baseline_experiment_id>
python scripts/optimize_prompt.py promote <validation_id>
# 或人工拒绝
python scripts/optimize_prompt.py reject <patch_id>
```

Prompt 版本内容不可变：批准 Patch 只创建新的 `_candidate` 版本；验证通过后仍需人工执行 `promote`，届时复制为正式版本并归档旧 Active 版本。Promotion Check 要求平均分及通过率不下降、幻觉率增幅不超过阈值、严重回归为0且目标 Badcase 比例下降。

---

## 项目结构

```
├── config/
│   ├── settings.py              # 全局配置
│   └── prompts/                 # 各 Agent 系统提示词
│       ├── deep_writer_prompt.py    # 撰写师/提取师/审计 Prompt
│       ├── writer_prompt.py         # 通用撰写 Prompt + 岗位差异化模板
│       ├── position_prompts.py      # 5 类岗位差异化配置
│       └── hr_prompt.py             # HR 评分 Prompt
├── src/
│   ├── agents/
│   │   ├── hr_screener.py       # HR 评分响应解析
│   │   └── llm_agent.py         # LLM Agent 基类
│   ├── crewai/
│   │   ├── pipeline.py          # 核心编排（缓存/迭代/字数校验）
│   │   ├── agents.py            # Agent 工厂
│   │   ├── tasks.py             # Task 工厂
│   │   ├── callbacks.py         # 步骤日志回调
│   │   └── database.py          # SQLite 持久化 + 缓存 CRUD
│   ├── utils/                   # 文件解析、日志、经历解析
│   └── workforce/               # 旧版 Workforce 实现（保留）
├── scripts/
│   ├── gradio_app.py            # Web UI（主入口）
│   └── run_deep_optimize.py     # 命令行批处理
├── data/
│   ├── JD/                      # 岗位 JD 文件
│   ├── raw_experiences/         # 原始经历文件
│   ├── test_cases/              # 锚定内容（个人职责）
│   ├── excellent_resumes/       # 优秀简历参考
│   └── optimization.db          # SQLite 数据库（运行时生成）
├── output/                      # 优化结果输出
├── tests/                       # 单元测试
├── config.ini                   # natapp 隧道配置
├── start_server.bat / stop_server.bat  # 服务启停脚本
└── requirements.txt
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env`：

```env
API_KEY=your_api_key
BASE_URL=https://api.deepseek.com
MODEL_ID=deepseek-chat          # 普通模式
MODEL_ID_THINKING=deepseek-reasoner  # 思考模式
```

### 3. 运行

```bash
# Web UI（推荐）
python scripts/gradio_app.py
# 本地访问 http://127.0.0.1:7860

# 命令行批处理
python scripts/run_deep_optimize.py                    # 交互式
python scripts/run_deep_optimize.py --default          # 第一个文件 + 第一个 JD
python scripts/run_deep_optimize.py --all-jds          # 第一个文件 + 全部 JD
python scripts/run_deep_optimize.py --file my.md --jd=产品经理
```

### 4. 对外服务（natapp 内网穿透）

```bash
# 方式一：直接运行批处理
start_server.bat

# 方式二：手动启动
python scripts/gradio_app.py          # 启动 Web 服务（7860 端口）
natapp.exe -authtoken=你的token        # 启动隧道
```

`config.ini` 中已配置 `lport=7860`，隧道公网地址由 natapp 控制台分配。

---

## 工作流程

```
Step 0   岗位认知度分类 ──→ 已知领域（快速路径）
                │
                └──→ 陌生领域：行业解码学习
Phase 0  JD 分析（关键词/数据维度/优先级）
Phase 1  经历诊断（事实提取 + 缺失项/可量化点/冗余点）
Phase 2  STAR 撰写 ↔ HR 评分（多轮迭代，带早停）
Phase 3  编造审计（逐句对比，输出编造清单）
          ↓
      落库 + Badcase 分析 + 输出
```

### HR 四维评分标准（每维 25 分，满分 100）

| 维度 | 考察点 |
|------|--------|
| 匹配度 | 与 JD 岗位职责和技能的精准匹配 |
| 数据化 | 量化成果支撑（每条至少 2 个量化数据） |
| 影响力 | 说服力、业务价值、个人贡献 |
| 简洁度 | 语言精炼、专业、有力 |

> 目标阈值默认 93 分，未达标则进入下一轮迭代。

---

## 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `target_score` | 93 | HR 评分目标阈值 |
| `max_iterations` | 3 | 最大迭代轮次 |
| `MAX_CHARS_PER_BULLET` | 85 | 每条要点中文字数上限 |
| `fabrication_tolerance` | 0 | 数据虚构容忍度（0-60%） |
| `RESULT_CACHE_MAX` | 64 | 内存结果缓存容量 |

---

## 测试

```bash
pytest tests/
```

覆盖：CrewAI pipeline 流程、数据库 CRUD、HR 评分解析。

## License

MIT
