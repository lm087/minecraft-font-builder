"""Build and verify work in several processes at once."""

from __future__ import annotations

import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

# a style of about 65000 glyphs needs up to 1.3 GB while it is built, plus room for the rest
MEMORY_PER_JOB = 2 * 1024 ** 3


def total_memory() -> Optional[int]:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.ullTotalPhys) if ok else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def auto_jobs(n_tasks: int, requested: int = 0, big: bool = True) -> int:
    """How many processes to use: *requested*, or as many as the cores and memory allow."""
    if n_tasks <= 1:
        return 1
    if requested:
        return max(1, min(requested, n_tasks))
    jobs = min(os.cpu_count() or 1, n_tasks)
    if big:
        mem = total_memory()
        jobs = min(jobs, max(1, (mem - MEMORY_PER_JOB) // MEMORY_PER_JOB) if mem else 2)
    return max(1, jobs)


def run(func: Callable, tasks: Iterable, jobs: int,
        on_done: Optional[Callable[[int, object], None]] = None) -> list:
    """func(task) for every task, results in order. on_done(index, result) runs as each finishes."""
    tasks = list(tasks)
    if jobs <= 1 or len(tasks) <= 1:
        results = []
        for i, task in enumerate(tasks):
            results.append(func(task))
            if on_done:
                on_done(i, results[-1])
        return results
    results = [None] * len(tasks)
    # spawn on every system: the default on macOS and Windows, and safe with threads
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(func, task): i for i, task in enumerate(tasks)}
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            if on_done:
                on_done(i, results[i])
    return results