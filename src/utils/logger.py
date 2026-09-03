"""日志工具"""

import logging
import os
from datetime import datetime


def get_logger(
    name: str = "resume_optimizer",
    level: int = logging.INFO,
    log_to_file: bool = True,
) -> logging.Logger:
    """获取配置好的日志器

    Args:
        name: 日志器名称
        level: 日志级别
        log_to_file: 是否同时写入文件
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # 文件输出
    if log_to_file:
        os.makedirs("output/logs", exist_ok=True)
        log_file = f"output/logs/{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_fmt)
        logger.addHandler(file_handler)

    return logger
