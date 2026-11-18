"""SHA-256 流式哈希与资产导入拷贝。"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256（大文件不整读内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def copy_into_project(source: Path, project_root: Path, *, subdir: str = "assets") -> Path:
    """把源文件拷入工程目录（源文件保持只读、绝不改写）。

    返回工程内的目标路径；同名冲突时自动加序号。
    """
    target_dir = project_root / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    counter = 1
    while target.exists():
        target = target_dir / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    shutil.copy2(source, target)
    return target
