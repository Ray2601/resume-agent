"""经历解析器 - 从 test_cases 中提取独立的项目经历

支持：.docx（zip+xml解析）、.md 文件
处理：自动分段、去重、标准化
"""

import os
import re
import zipfile
from dataclasses import dataclass, field


@dataclass
class Experience:
    """单条经历"""
    title: str          # 经历标题（项目名/公司名）
    content: str        # 经历原始内容
    source: str         # 来源文件
    category: str = ""  # 分类：项目经历/实习经历/等
    score: int = 0      # HR评分（优化后填充）
    optimized: str = "" # 优化后的内容


# 用于分割经历的关键标记
EXPERIENCE_MARKERS = [
    # 项目经历标记
    r"(?:##\s*)?(?:项目|实习|工作)\s*(?:经历|经验)?",
    # 公司名+职位
    r".{2,20}(?:科技|网络|技术|信息).{0,10}(?:有限公司|公司).{0,30}(?:\||｜|•)",
    # 时间范围模式
    r"\d{4}[\.年]\s*\d{1,2}\s*[-–—至到]\s*(?:\d{4}[\.年]\s*\d{1,2}|至今|现在)",
    # 项目名+时间
    r".{3,40}(?:\||｜)\s*(?:产品|项目|算法|技术|负责).{0,20}(?:\d{4}\.)",
]

# 强动词开头，标记新经历开始
STRONG_START_PATTERNS = [
    r"(?:主导|设计|推动|实现|搭建|负责|规划|制定|管理).{5,80}(?:提升|增长|优化|降低|提高)",
]


def extract_text_from_docx(filepath: str) -> str:
    """从 docx 文件提取文本（不依赖 python-docx）"""
    try:
        with zipfile.ZipFile(filepath) as z:
            with z.open("word/document.xml") as f:
                text = f.read().decode("utf-8")
                text = re.sub(r"<[^>]+>", "\n", text)
                text = re.sub(r"&[a-z]+;", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = re.sub(r" {2,}", " ", text)
                return text.strip()
    except Exception as e:
        raise ValueError(f"无法解析 docx 文件 {filepath}: {e}")


def extract_text_from_md(filepath: str) -> str:
    """读取 md 文件"""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def _extract_title(text: str, source: str) -> str:
    """从文本中提取标题"""
    # 从第一个 h1 标题提取
    h1 = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    if h1:
        return h1.group(1).strip()[:70]
    # 从第一个 h2 标题提取
    h2 = re.search(r"^##\s+(.+)", text, re.MULTILINE)
    if h2:
        return h2.group(1).strip()[:70]
    # 用文件名
    return os.path.splitext(source)[0][:70]


def split_into_experiences(text: str, source: str) -> list[Experience]:
    """将文本分割为独立的经历条目

    .md 文件：整个文件视为一个项目经历
    .docx 文件：按项目段落分割为多条经历
    """
    experiences = []

    if source.endswith((".md", ".txt")):
        # md 文件 = 一个项目经历
        if len(text.strip()) > 50:
            title = _extract_title(text, source)
            experiences.append(Experience(
                title=title,
                content=text.strip(),
                source=source,
                category="项目经历",
            ))
        return experiences

    # docx 简历：按"项目名 | 职位 | 时间"模式分割
    resume_pattern = re.compile(
        r"(?:^|\n)(.{3,120}?(?:\||｜)\s*.{0,50}?(?:\d{4}[\.年]))",
        re.MULTILINE,
    )
    matches = list(resume_pattern.finditer(text))

    if len(matches) >= 2:
        for i, match in enumerate(matches):
            title = match.group(1).strip()
            # 清理标题中的乱码和残留符号
            title = re.sub(r'^[）\)\]\}》>»,，\s]+', '', title)
            title = re.sub(r'^\|', '', title).strip()
            if not title or len(title) < 5:
                continue
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            content = _clean_experience_content(content)
            if len(content) > 100:
                experiences.append(Experience(
                    title=title,
                    content=content,
                    source=source,
                    category=_classify_experience(content),
                ))
    elif len(text.strip()) > 100:
        # 分割失败，整体作为一个经历
        title = _extract_title(text, source)
        experiences.append(Experience(
            title=title,
            content=text.strip(),
            source=source,
            category="项目经历",
        ))

    return experiences


def _clean_experience_content(text: str) -> str:
    """清理经历内容，移除教育信息、个人信息等非核心部分"""
    # 移除纯个人信息行
    text = re.sub(r"(?:年龄|电话|邮箱|地址|政治面貌|民族).*?\n", "", text)
    # 移除技能列表区域
    text = re.sub(r"\n(?:技能|语言|证书|获奖|荣誉)[：:][\s\S]*?(?=\n(?:项目|实习|工作|$))", "", text)
    # 移除过多的空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _classify_experience(text: str) -> str:
    """自动分类经历类型"""
    lower = text.lower()
    if any(kw in lower for kw in ["实习", "intern"]):
        return "实习经历"
    if any(kw in lower for kw in ["项目", "project", "产品", "系统", "平台"]):
        return "项目经历"
    if any(kw in lower for kw in ["算法", "模型", "训练", "数据"]):
        return "技术经历"
    return "其他经历"


def deduplicate_experiences(experiences: list[Experience]) -> list[Experience]:
    """去重：基于内容相似度移除重复/高度重叠的经历

    对于简历来源的 docx 文件，用更宽松的相似度阈值；
    对于 md 文档来源，按标题完全匹配去重。
    """
    unique = []

    for exp in experiences:
        is_duplicate = False
        for existing in unique:
            # 来自不同源文件但标题高度相似的，进行内容重叠检测
            if exp.source != existing.source:
                # 标题相似度
                title_sim = _title_similarity(exp.title, existing.title)
                # 内容开头相似度
                content_sim = _content_overlap_ratio(exp.content[:300], existing.content[:300])

                # 标题相似且内容重叠 → 认为是同一条经历的不同版本
                if title_sim > 0.4 and content_sim > 0.3:
                    is_duplicate = True
                    break
                # 内容高度重叠 → 去重
                if content_sim > 0.6:
                    is_duplicate = True
                    break

        if not is_duplicate:
            unique.append(exp)

    return unique


def _title_similarity(t1: str, t2: str) -> float:
    """简单的标题相似度（共词比例）"""
    w1 = set(t1.lower().split())
    w2 = set(t2.lower().split())
    if not w1 or not w2:
        return 0.0
    common = w1 & w2
    return len(common) / min(len(w1), len(w2))


def _content_overlap_ratio(c1: str, c2: str) -> float:
    """内容开头重叠比例（基于字符级相似度）"""
    # 取更短的内容长度
    min_len = min(len(c1), len(c2))
    if min_len < 50:
        return 0.0
    # 统计共同字符
    common = sum(1 for a, b in zip(c1[:min_len], c2[:min_len]) if a == b)
    return common / min_len


def parse_all_experiences(test_cases_dir: str) -> list[Experience]:
    """解析 test_cases 目录下所有文件，提取独立经历

    Args:
        test_cases_dir: data/test_cases/ 目录路径

    Returns:
        去重后的 Experience 列表
    """
    all_experiences = []

    if not os.path.isdir(test_cases_dir):
        return all_experiences

    for filename in sorted(os.listdir(test_cases_dir)):
        filepath = os.path.join(test_cases_dir, filename)
        if filename.startswith(".") or filename == "test_cases.json":
            continue

        try:
            if filename.endswith(".docx"):
                text = extract_text_from_docx(filepath)
            elif filename.endswith((".md", ".txt")):
                text = extract_text_from_md(filepath)
            else:
                continue

            if not text.strip():
                continue

            exps = split_into_experiences(text, filename)
            all_experiences.extend(exps)
            print(f"  {filename}: 提取 {len(exps)} 条经历")

        except Exception as e:
            print(f"  [!] {filename}: 解析失败 - {e}")

    # 去重
    before = len(all_experiences)
    all_experiences = deduplicate_experiences(all_experiences)
    after = len(all_experiences)
    if before != after:
        print(f"  去重: {before} → {after} 条")

    return all_experiences


def load_jd_files(jd_dir: str) -> list[tuple[str, str, str]]:
    """加载所有JD文件，返回 [(显示路径, 文件名, 内容), ...]"""
    jd_files = []
    if not os.path.isdir(jd_dir):
        return jd_files
    for root, _, files in os.walk(jd_dir):
        for f in sorted(files):
            if f.endswith((".txt", ".md")):
                path = os.path.join(root, f)
                rel_path = os.path.relpath(path, jd_dir)
                with open(path, "r", encoding="utf-8") as fp:
                    jd_files.append((rel_path, f, fp.read()))
    return jd_files
