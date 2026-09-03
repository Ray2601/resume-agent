"""文件解析工具 - 支持多种格式的简历和JD文件解析"""

import os
import json


def parse_resume_file(filepath: str) -> str:
    """解析简历文件

    支持格式：.txt, .md, .json, .docx (需要python-docx)
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".txt", ".md"):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    if ext == ".json":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 尝试从常见字段提取文本
        if isinstance(data, dict):
            return data.get("content", data.get("text", json.dumps(data, ensure_ascii=False)))
        return str(data)

    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            raise ImportError("解析.docx需要安装python-docx: pip install python-docx")

    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(filepath)
            return "\n".join(page.get_text() for page in doc)
        except ImportError:
            raise ImportError("解析.pdf需要安装PyMuPDF: pip install PyMuPDF")

    raise ValueError(f"不支持的文件格式: {ext}")


def parse_jd_file(filepath: str) -> str:
    """解析JD文件（与简历文件解析逻辑相同）"""
    return parse_resume_file(filepath)
