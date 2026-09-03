"""深度经历优化器 - 多阶段逐步提炼 + 多JD输出

流程：
1. 从 data/raw_experiences/ 读取用户上传的 .md/.txt 文件
2. 加载所有 JD（data/JD/ 递归扫描）
3. 选择经历 → 对每个JD独立生成一份简历
4. 每份简历经过：Phase 0 JD分析(缓存) → Phase 1 智能提取 → Phase 2 合成+评估 → Phase 3 编造审计
5. 输出到 output/<jd_category>/deep_optimize/<经历名>_<jd名>.md

与 run_experience_optimizer.py 的区别：
- 一步到位提取300-500字核心素材，简洁无冗余
- 锚定 test_cases 中个人职责，永远放在最前面不压缩
- 只提炼通用能力，不套用JD行业场景
- 一份经历可对多个JD各生成一份简历
- 自动编造审计，输出编造内容清单

用法：
  python scripts/run_deep_optimize.py                     # 交互式
  python scripts/run_deep_optimize.py --default            # 第一个文件 + 第一个JD
  python scripts/run_deep_optimize.py --all-jds            # 第一个文件 + 全部JD
  python scripts/run_deep_optimize.py --file my.md         # 指定文件 + 交互式选JD
  python scripts/run_deep_optimize.py --file my.md --all-jds  # 指定文件 + 全部JD
"""

import os
import sys
from datetime import datetime
from collections import defaultdict

# --- 修复 Windows 下的两个常见问题 ---
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
from src.crewai.pipeline import CrewAIResumePipeline
from src.utils.experience_parser import load_jd_files
from src.utils.logger import get_logger

load_dotenv()
logger = get_logger("crewai_optimizer")

BASE_DIR = _project_root
RAW_EXP_DIR = os.path.join(BASE_DIR, "data", "raw_experiences")
TEST_CASES_DIR = os.path.join(BASE_DIR, "data", "test_cases")
JD_DIR = os.path.join(BASE_DIR, "data", "JD")
REFERENCE_DIR = os.path.join(BASE_DIR, "data", "excellent_resumes")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def load_raw_experiences(raw_dir: str) -> list[dict]:
    """加载用户上传的原始经历文件，支持子目录"""
    experiences = []
    if not os.path.isdir(raw_dir):
        return experiences

    for fname in sorted(os.listdir(raw_dir)):
        fpath = os.path.join(raw_dir, fname)
        if fname.startswith("."):
            continue
        if os.path.isfile(fpath) and fname.endswith((".md", ".txt")):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                experiences.append({
                    "filename": fname,
                    "filepath": fpath,
                    "content": content,
                    "size": len(content),
                })
        elif os.path.isdir(fpath):
            # 子目录：合并其中所有 .md/.txt 文件
            parts = []
            for sub_fname in sorted(os.listdir(fpath)):
                sub_fpath = os.path.join(fpath, sub_fname)
                if os.path.isfile(sub_fpath) and sub_fname.endswith((".md", ".txt")):
                    with open(sub_fpath, "r", encoding="utf-8") as f:
                        parts.append(f.read())
            if parts:
                combined = "\n\n".join(parts)
                experiences.append({
                    "filename": fname,
                    "filepath": fpath,
                    "content": combined,
                    "size": len(combined),
                })
    return experiences


def find_anchor_content(exp_name: str) -> str:
    """在 test_cases 中查找匹配的锚定内容（个人职责，不可压缩）"""
    if not os.path.isdir(TEST_CASES_DIR):
        return ""
    # 尝试多种匹配方式
    name_lower = exp_name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for fname in sorted(os.listdir(TEST_CASES_DIR)):
        fpath = os.path.join(TEST_CASES_DIR, fname)
        if not os.path.isfile(fpath) or fname.startswith("."):
            continue
        fname_lower = fname.lower().replace(" ", "").replace("-", "").replace("_", "")
        # 匹配：文件名包含经历名 或 经历名包含文件名
        if name_lower in fname_lower or fname_lower in name_lower:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return content
    return ""


def save_deep_output(
    result: dict,
    raw_exp: dict,
    jd_name: str,
    output_dir: str,
) -> str:
    """保存深度优化结果到 .md 文件"""
    safe_exp = "".join(
        c for c in raw_exp["filename"][:30]
        if c.isalnum() or c in " _-（）()"
    ).strip()
    safe_jd = "".join(
        c for c in jd_name.replace(".txt", "").replace(".md", "")[:30]
        if c.isalnum() or c in " _-"
    ).strip()
    filepath = os.path.join(output_dir, f"{safe_exp}__{safe_jd}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {raw_exp['filename']} × {jd_name}\n\n")
        f.write(f"> HR评分: {result['final_score']}/100  |  ")
        f.write(f"迭代: {result['iterations']}轮  |  ")
        f.write(f"达标: {'是' if result['success'] else '否'}\n\n")
        f.write(f"---\n\n")

        f.write(f"## 最终版本（优化后）\n\n{result['final_result']}\n\n")
        f.write(f"---\n\n")

        f.write(f"## Phase 1: 智能提取 ({len(result['phase1_summary'])}字)\n\n{result['phase1_summary']}\n\n")
        f.write(f"---\n\n")

        f.write(f"## Phase 3: 编造审计\n\n{result.get('fabrication_report', 'N/A')}\n\n")
        f.write(f"---\n\n")

        f.write(f"## 原始内容\n\n{raw_exp['content'][:3000]}\n\n")
        if raw_exp['size'] > 3000:
            f.write(f"... (原始内容共{raw_exp['size']}字，仅展示前3000字)\n\n")
        f.write(f"---\n\n")

        if result.get("history"):
            f.write(f"## 迭代历史\n\n")
            f.write(f"| 轮次 | 评分 |\n")
            f.write(f"|------|------|\n")
            for h in result["history"]:
                f.write(f"| {h['iteration']} | {h['score']}/100 |\n")

    return filepath


def run_single_jd(
    pipeline_factory,
    jd_content: str,
    jd_name: str,
    selected_exp: dict,
    anchor_content: str,
    out_dir: str,
) -> dict:
    """对单个JD运行深度优化"""
    pipeline = pipeline_factory()

    result = pipeline.run(
        jd=jd_content,
        raw_experience=selected_exp["content"],
        anchor_content=anchor_content,
        target_score=93,
        max_iterations=3,
    )
    filepath = save_deep_output(result, selected_exp, jd_name, out_dir)
    result["filepath"] = filepath
    return result


def main():
    use_default = "--default" in sys.argv
    use_all_jds = "--all-jds" in sys.argv
    file_arg = None
    jd_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("--file="):
            file_arg = arg.split("=", 1)[1]
        elif arg.startswith("--jd="):
            jd_arg = arg.split("=", 1)[1]

    print("=" * 60)
    print("  深度经历优化器 (Multi-Phase + Multi-JD)")
    print("  Phase 0: JD分析(缓存) → Phase 1: 智能提取 → Phase 2: 合成+评估 → Phase 3: 编造审计")
    print("=" * 60)

    # 1. 加载原始经历
    print(f"\n[数据] 从 data/raw_experiences/ 加载经历...")
    raw_experiences = load_raw_experiences(RAW_EXP_DIR)
    if not raw_experiences:
        print(f"错误: data/raw_experiences/ 下无 .md/.txt 文件或子目录")
        print(f"请将需要优化的经历文件放入该目录后重试。")
        print(f"支持：单个 .md/.txt 文件，或包含多个 .txt 的子目录")
        sys.exit(1)
    print(f"找到 {len(raw_experiences)} 项:\n")
    for i, exp in enumerate(raw_experiences, 1):
        print(f"  [{i}] {exp['filename']} ({exp['size']}字)")

    # 2. 选择要优化的经历
    if file_arg:
        matched = [e for e in raw_experiences if e["filename"] == file_arg]
        if matched:
            selected_exp = matched[0]
            print(f"\n[--file] 已选择: {selected_exp['filename']}")
        else:
            print(f"错误: 未找到文件 '{file_arg}'")
            sys.exit(1)
    elif use_default or use_all_jds:
        selected_exp = raw_experiences[0]
        tag = "--all-jds" if use_all_jds else "--default"
        print(f"\n[{tag}] 自动选择: {selected_exp['filename']}")
    else:
        choice = input(f"\n选择文件 (1-{len(raw_experiences)}, 默认1): ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(raw_experiences) else 0
        selected_exp = raw_experiences[idx]

    # 3. 加载所有JD
    jd_files = load_jd_files(JD_DIR)
    if not jd_files:
        print(f"错误: {JD_DIR} 下无JD文件")
        sys.exit(1)

    jd_groups = defaultdict(list)
    for rel_path, fname, content in jd_files:
        category = rel_path.split(os.sep)[0] if os.sep in rel_path else "通用"
        jd_groups[category].append((rel_path, fname, content))

    all_jds = []
    print(f"\n可用JD (共{len(jd_files)}个):")
    for cat in sorted(jd_groups.keys()):
        for rel_path, fname, content in jd_groups[cat]:
            all_jds.append((cat, rel_path, fname, content))
            print(f"  [{len(all_jds)}] [{cat}] {rel_path}")

    if use_all_jds:
        selected_jds = all_jds
        print(f"\n[--all-jds] 将对全部 {len(selected_jds)} 个JD生成简历")
    elif jd_arg:
        matched_jds = [jd for jd in all_jds if jd_arg in jd[2]]
        if matched_jds:
            selected_jds = matched_jds
            print(f"\n[--jd] 匹配到 {len(selected_jds)} 个JD: {', '.join(jd[2] for jd in selected_jds)}")
        else:
            print(f"错误: 未找到包含 '{jd_arg}' 的JD")
            sys.exit(1)
    elif use_default:
        selected_jds = [all_jds[0]]
        print(f"\n[--default] 自动选择: {all_jds[0][2]}")
    else:
        print(f"\n输入编号选择JD。多个用逗号分隔（如 1,3,5），输入 'all' 选择全部，回车默认选第1个：")
        choice = input(f"> ").strip()
        if choice.lower() == "all":
            selected_jds = all_jds
            print(f"已选择全部 {len(selected_jds)} 个JD")
        elif choice == "":
            selected_jds = [all_jds[0]]
            print(f"已选择: {all_jds[0][2]}")
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected_jds = [all_jds[i] for i in indices if 0 <= i < len(all_jds)]
                if not selected_jds:
                    selected_jds = [all_jds[0]]
                print(f"已选择 {len(selected_jds)} 个JD: {', '.join(jd[2] for jd in selected_jds)}")
            except (ValueError, IndexError):
                selected_jds = [all_jds[0]]
                print(f"输入无效，默认选择: {all_jds[0][2]}")

    # 4. 初始化 Pipeline 工厂
    print(f"\n初始化深度优化Pipeline (模型: {os.getenv('MODEL_ID', 'deepseek-chat')})...")

    reference_resumes = []
    if os.path.isdir(REFERENCE_DIR):
        for f in sorted(os.listdir(REFERENCE_DIR)):
            path = os.path.join(REFERENCE_DIR, f)
            if os.path.isfile(path) and not f.startswith("."):
                with open(path, "r", encoding="utf-8") as fp:
                    reference_resumes.append(fp.read())
    if reference_resumes:
        print(f"已加载 {len(reference_resumes)} 份优秀简历参考")

    # 工厂函数：为每个JD创建独立的pipeline
    ref_resumes = reference_resumes  # 闭包捕获
    class _PipelineFactory:
        def __init__(self, refs):
            self.reference_resumes = refs
        def __call__(self):
            p = CrewAIResumePipeline()
            p.reference_resumes = self.reference_resumes
            return p
    pipeline_factory = _PipelineFactory(ref_resumes)

    # 5. 查找锚定内容（test_cases 中匹配的个人职责）
    anchor_content = find_anchor_content(selected_exp["filename"])
    if anchor_content:
        print(f"\n锚定内容: 从 test_cases/ 匹配到 {len(anchor_content)}字（不可压缩，放在提取结果最前面）")
    else:
        print(f"\n锚定内容: 未在 test_cases/ 中找到匹配文件")

    # 6. 对每个JD运行
    total = len(selected_jds)
    results = []
    for idx, (cat, rel_path, jd_name, jd_content) in enumerate(selected_jds, 1):
        print(f"\n{'='*60}")
        print(f"  [{idx}/{total}] JD: {jd_name}")
        print(f"  经历: {selected_exp['filename']} ({selected_exp['size']}字)")
        print(f"{'='*60}")

        cat_slug = "".join(c for c in cat if c.isalnum() or c in " _-").strip()
        out_dir = os.path.join(OUTPUT_DIR, cat_slug, "deep_optimize")
        os.makedirs(out_dir, exist_ok=True)

        result = run_single_jd(
            pipeline_factory, jd_content, jd_name, selected_exp, anchor_content, out_dir,
        )
        has_fabric = "编造" in result.get("fabrication_report", "") and "未发现" not in result.get("fabrication_report", "")
        fabric_flag = " [!]有编造" if has_fabric else ""
        results.append({
            "jd_name": jd_name,
            "category": cat,
            "score": result["final_score"],
            "success": result["success"],
            "iterations": result["iterations"],
            "filepath": result["filepath"],
            "phase1_len": len(result["phase1_summary"]),
            "has_fabric": has_fabric,
        })
        print(f"  → 评分: {result['final_score']}/100 | Phase1: {len(result['phase1_summary'])}字 | 迭代: {result['iterations']}轮{fabric_flag} | {os.path.basename(result['filepath'])}")

    # 7. 汇总对比
    if len(results) > 1:
        results.sort(key=lambda r: r["score"], reverse=True)
        print(f"\n{'='*60}")
        print(f"  全部JD评分对比")
        print(f"{'='*60}")
        for i, r in enumerate(results, 1):
            icon = "[OK]" if r["success"] else f"[{(93 - r['score'])}分差距]"
            print(f"  {icon} #{i}: [{r['score']}/100] {r['jd_name']} ({r['category']})")

    print(f"\n{'='*60}")
    print(f"  全部完成!")
    print(f"  经历: {selected_exp['filename']}")
    print(f"  处理JD数: {total}")
    if len(results) == 1:
        print(f"  输出: {results[0]['filepath']}")
    else:
        print(f"  输出目录: {os.path.dirname(results[0]['filepath'])}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
