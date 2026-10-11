"""Disk lifecycle. cleanup: copy small reproduction files to keep/, delete assets/ and build/.
Left behind: job.json, final.mp4, meta.json, sources.md, keep/. restore_keep puts keep/ files back (--force rerun)."""
from __future__ import annotations

import os
import shutil
import stat
import sys

from common import KEEP_FILES, PIPE, Job, PipelineError, log


def _size(p) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)


def _onexc(func, path, exc):
    os.chmod(path, stat.S_IWRITE)  # Windows read-only files
    func(path)


def guard(job: Job) -> None:
    """Only a real job folder: has job.json, is not a filesystem root, not the repo or pipeline folder."""
    jd = job.dir
    if not job.json_path.is_file():
        raise PipelineError("not a job folder (no job.json)")
    if jd.parent == jd or jd in (PIPE, PIPE.parent) or PIPE.is_relative_to(jd) or len(jd.parts) < 3:
        raise PipelineError(f"refusing to clean {jd}")
    for d in (job.assets, job.build, job.keep):
        if d.parent != jd or d.is_symlink():
            raise PipelineError(f"unexpected path {d}")


def save_keep(job: Job) -> list[str]:
    kept = []
    for where, name in KEEP_FILES:
        src = (job.assets if where == "assets" else job.build) / name
        if src.exists():
            job.keep.mkdir(exist_ok=True)
            shutil.copy2(src, job.keep / name)  # mtime kept so signatures still match after restore
            kept.append(name)
    return kept


def restore_keep(job: Job) -> list[str]:
    restored = []
    for where, name in KEEP_FILES:
        src, dst = job.keep / name, (job.assets if where == "assets" else job.build) / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(f"{where}/{name}")
    return restored


def run_cleanup(job: Job) -> int:
    guard(job)
    final = job.dir / "final.mp4"
    if not (final.exists() and final.stat().st_size > 10_000):
        raise PipelineError("final.mp4 missing or empty: cleanup cancelled")
    if not (job.dir / "meta.json").exists():
        raise PipelineError("meta.json missing: run meta before cleanup")
    kept = save_keep(job)
    freed = 0
    for d in (job.assets, job.build):
        if d.exists():
            freed += _size(d)
            shutil.rmtree(d, **{"onexc" if sys.version_info >= (3, 12) else "onerror": _onexc})
    log.info(f"cleanup: {freed / 1e6:.1f} MB freed, keep/: {', '.join(kept) or '-'}")
    return freed
