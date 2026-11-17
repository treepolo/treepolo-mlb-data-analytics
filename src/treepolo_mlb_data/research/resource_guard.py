"""Protection of the user's computer while research runs.

The rule: a run never uses up what is free at that moment (memory, CPU, graphics memory), and it never keeps the graphics card busy so long that the screen freezes. Nothing here is a
fixed cap: every number is read from the machine when it is needed and the run adapts.

* ``Guard.job(label)`` wraps every research run (the shared entry is ``ResearchService``): the process gets a lower priority, the number of threads is set from the CPU load at that
  moment (at least two cores stay free), a monitor thread writes one line per second to a log (the process tree's memory, the memory still free, the pagefile / commit, CPU, the graphics
  card) and judges the situation: ``ok``, ``pressure`` (little is free: the run gives back what it can and goes on in smaller pieces) or ``critical`` (the run is stopped safely with a message).
* ``Guard.checkpoint()`` is called by loops (data loading, training sweeps, bootstrap members, folds). Under pressure it first gives back what the process holds (``shrink``); if the shortage
  is not the run's own doing it goes on in smaller pieces (waiting would free nothing), and if the run's own memory keeps growing it waits and then raises ``ResourceExhausted``. It raises
  at once when it is critical, so the run fails with a clear message instead of freezing the computer.
* ``Guard.chunk_rows()`` and ``Guard.gpu_chunks()`` size the pieces of work from what is free now; ``gpu_chunks`` also keeps every graphics-card call short (the target is a tenth of a
  second; Windows resets a card that does not answer for two seconds, which freezes the screen). There are no routine pauses between pieces: the work only rests when the card is hot.
* ``Guard.device()`` says whether the graphics card may be used now and limits the share of its memory this process may take, leaving the part the display needs.
"""
from __future__ import annotations

import contextlib
import ctypes
import gc
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

GB = 2 ** 30


class ResourceExhausted(RuntimeError):
    """The computer does not have enough free resources to go on safely; the run stops instead of freezing the machine."""


@dataclass
class Snapshot:
    ram_total: int = 0
    ram_available: int = 0
    commit_limit: int = 0                 # bytes the system may commit (RAM + pagefile); 0 = unknown
    commit_used: int = 0
    cpu_system: float = 0.0               # percent of all the CPU, whole machine
    cpu_ours: float = 0.0                 # percent of all the CPU, this process and its children
    rss_ours: int = 0                     # memory of this process and its children
    gpu_util: float | None = None         # percent, whole card
    gpu_used: int = 0                     # bytes in use on the card (all programs)
    gpu_total: int = 0
    gpu_temp: float | None = None
    taken: float = field(default_factory=time.time)

    @property
    def gpu_free(self) -> int:
        return max(self.gpu_total - self.gpu_used, 0) if self.gpu_total else 0

    @property
    def commit_free(self) -> int:
        return max(self.commit_limit - self.commit_used, 0) if self.commit_limit else 1 << 60


@dataclass
class Limits:
    """What 'enough is left for the user' means on this machine, from its size."""

    ram_soft: int          # below this much free memory: pressure (smaller chunks, wait)
    ram_hard: int          # below this: stop
    commit_soft: int
    commit_hard: int
    gpu_reserve: int = int(1.0 * GB)         # graphics memory kept for the display
    gpu_hard: int = int(0.35 * GB)

    @classmethod
    def for_total(cls, ram_total: int) -> "Limits":
        return cls(ram_soft=max(int(3.0 * GB), int(0.20 * ram_total)), ram_hard=max(int(1.5 * GB), int(0.10 * ram_total)), commit_soft=int(6 * GB), commit_hard=int(3 * GB))


def _commit_bytes() -> tuple[int, int]:
    """(limit, used) of the commit charge on Windows (RAM + pagefile); (0, 0) elsewhere."""

    if os.name != "nt":
        return 0, 0
    try:
        class PERFORMANCE_INFORMATION(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                        ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                        ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t), ("HandleCount", ctypes.c_ulong), ("ProcessCount", ctypes.c_ulong), ("ThreadCount", ctypes.c_ulong)]

        info = PERFORMANCE_INFORMATION(); info.cb = ctypes.sizeof(info)
        if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
            return int(info.CommitLimit * info.PageSize), int(info.CommitTotal * info.PageSize)
    except Exception:
        pass
    return 0, 0


class SystemSampler:
    """Reads the machine. A test replaces it with a function that returns ``Snapshot`` objects."""

    def __init__(self) -> None:
        import psutil

        self.psutil = psutil
        self.me = psutil.Process()
        self._procs: dict[int, Any] = {}
        self._gpu: tuple[float, dict[str, Any]] = (0.0, {})
        self.psutil.cpu_percent(interval=None)

    def _tree(self):
        procs = [self.me]
        try:
            procs += self.me.children(recursive=True)
        except Exception:
            pass
        return procs

    def _gpu_poll(self) -> dict[str, Any]:
        now = time.time()
        if now - self._gpu[0] < 2.0:
            return self._gpu[1]
        out: dict[str, Any] = {}
        try:
            flags = 0x08000000 if os.name == "nt" else 0                         # no console window
            text = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                  timeout=5, creationflags=flags).stdout.strip().splitlines()[0]
            util, used, total, temp = [float(x) for x in text.split(",")]
            out = {"gpu_util": util, "gpu_used": int(used * 2 ** 20), "gpu_total": int(total * 2 ** 20), "gpu_temp": temp}
        except Exception:
            out = {}
        self._gpu = (now, out)
        return out

    def __call__(self) -> Snapshot:
        vm = self.psutil.virtual_memory()
        rss, cpu = 0, 0.0
        for p in self._tree():
            try:
                rss += p.memory_info().rss
                cached = self._procs.setdefault(p.pid, p)
                cpu += cached.cpu_percent(interval=None)
            except Exception:
                continue
        limit, used = _commit_bytes()
        n = max(os.cpu_count() or 1, 1)
        return Snapshot(ram_total=int(vm.total), ram_available=int(vm.available), commit_limit=limit, commit_used=used, cpu_system=float(self.psutil.cpu_percent(interval=None)),
                        cpu_ours=min(cpu / n, 100.0), rss_ours=rss, **self._gpu_poll())


class Guard:
    def __init__(self, sampler: Callable[[], Snapshot] | None = None, *, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self._sampler = sampler
        self._sleep, self._clock = sleep, clock
        self._lock = threading.RLock()
        self._depth = 0
        self._label = ""
        self._snap: Snapshot | None = None
        self._state, self._reason = "ok", ""
        self._critical_since: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner: int | None = None
        self._log: Any = None
        self.log_path: Path | None = None
        self.threads = 1
        self.duty = 0.0                       # share of the working time spent resting: none while all is well; the monitor raises it only when the card is hot (80 C or more)
        self.cpu_rest = 0.0
        self._orig_priority: Any = None
        self._gpu_ready = False
        self._last_gpu_sync = self._clock()
        self.peaks: dict[str, float] = {}
        self.history: list[dict[str, Any]] = []
        self.rest_after = 0.15               # seconds of work on the card after which it rests (a sleep is at best 15 ms long on Windows: resting after every tiny call would only waste time)
        self._busy_acc = 0.0
        self.interval = 1.0                  # seconds between two looks at the machine
        self.critical_grace = 8.0            # seconds in the critical state before a run that does not reach a checkpoint is interrupted
        self.grow_allowance = int(0.25 * GB)  # how much this process may grow while the memory is short before the shortage counts as its own doing
        self.shrink_every = 10.0             # seconds between two clean-ups while the memory is short
        self.require_margin = int(0.5 * GB)  # what a step must leave free above the 'stop' line (the 'slow down' line does not forbid starting something)
        self._pressure_rss: int | None = None
        self._waiting = False                # True while a checkpoint waits for memory: the monitor must not interrupt a run that is only waiting
        self._last_shrink = -1e9

    # ------------------------------------------------------------------------------------------------ the machine
    @property
    def sampler(self) -> Callable[[], Snapshot]:
        if self._sampler is None:
            try:
                self._sampler = SystemSampler()
            except ImportError:                                              # psutil is a dependency of the package; without it nothing can be read, so nothing is limited
                self._sampler = lambda: Snapshot(ram_total=16 * GB, ram_available=16 * GB)
        return self._sampler

    def snapshot(self, max_age: float = 1.5) -> Snapshot:
        with self._lock:
            if self._snap is not None and time.time() - self._snap.taken < max_age:
                return self._snap
        snap = self.sampler()
        with self._lock:
            self._snap = snap
        return snap

    def limits(self, snap: Snapshot | None = None) -> Limits:
        return Limits.for_total((snap or self.snapshot()).ram_total or 16 * GB)

    def judge(self, snap: Snapshot) -> tuple[str, str]:
        """('ok' | 'pressure' | 'critical', why) from one snapshot."""

        lim = self.limits(snap)
        if snap.ram_available < lim.ram_hard:
            return "critical", f"only {snap.ram_available / GB:.1f} GB of memory is free (the run needs at least {lim.ram_hard / GB:.1f} GB left for you) / 可用記憶體只剩 {snap.ram_available / GB:.1f} GB"
        if snap.commit_limit and snap.commit_free < lim.commit_hard:
            return "critical", f"the virtual memory (RAM + pagefile) is nearly used up: {snap.commit_free / GB:.1f} GB left / 虛擬記憶體（含分頁檔）快用完，只剩 {snap.commit_free / GB:.1f} GB"
        if snap.gpu_total and snap.gpu_free < lim.gpu_hard:
            return "critical", f"only {snap.gpu_free / GB:.2f} GB of graphics memory is free / 顯示記憶體只剩 {snap.gpu_free / GB:.2f} GB"
        if snap.ram_available < lim.ram_soft:
            return "pressure", f"{snap.ram_available / GB:.1f} GB of memory is free (keeping {lim.ram_soft / GB:.1f} GB for you) / 可用記憶體 {snap.ram_available / GB:.1f} GB"
        if snap.commit_limit and snap.commit_free < lim.commit_soft:
            return "pressure", f"the virtual memory is getting low: {snap.commit_free / GB:.1f} GB left / 虛擬記憶體偏低"
        if snap.gpu_total and snap.gpu_free < lim.gpu_reserve:
            return "pressure", f"{snap.gpu_free / GB:.2f} GB of graphics memory is free / 顯示記憶體偏低"
        return "ok", ""

    # ------------------------------------------------------------------------------------------------ threads and priority
    def pick_threads(self, snap: Snapshot | None = None) -> int:
        """Threads for this run: what the others leave free, and at least two cores stay free."""

        snap = snap or self.snapshot()
        cores = max(os.cpu_count() or 1, 1)
        others = max(snap.cpu_system - snap.cpu_ours, 0.0)
        free_cores = cores * (1.0 - min(others, 100.0) / 100.0)
        return int(max(1, min(cores - 2, int(free_cores * 0.7)))) if cores > 2 else 1

    def _apply_threads(self, n: int) -> None:
        self.threads = n
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS", "RAYON_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            os.environ[name] = str(n)
        try:
            import sys

            if "torch" in sys.modules:
                sys.modules["torch"].set_num_threads(n)
            if "numba" in sys.modules:
                sys.modules["numba"].set_num_threads(min(n, sys.modules["numba"].config.NUMBA_NUM_THREADS))
            if True:
                import threadpoolctl

                threadpoolctl.threadpool_limits(limits=n)
        except Exception:
            pass

    def _lower_priority(self) -> None:
        try:
            import psutil

            me = psutil.Process()
            self._orig_priority = me.nice()
            me.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
        except Exception:
            self._orig_priority = None

    def _restore_priority(self) -> None:
        try:
            if self._orig_priority is not None:
                import psutil

                psutil.Process().nice(self._orig_priority)
        except Exception:
            pass
        self._orig_priority = None

    # ------------------------------------------------------------------------------------------------ one run
    @contextlib.contextmanager
    def job(self, label: str, log_path: str | Path | None = None) -> Iterator["Guard"]:
        with self._lock:
            self._depth += 1
            first = self._depth == 1
            if first:
                self._label, self._owner = label, threading.get_ident()
                self.peaks = {"rss_ours_gb": 0.0, "ram_available_min_gb": 1e9, "cpu_system_max": 0.0, "cpu_ours_max": 0.0, "gpu_util_max": 0.0, "gpu_used_max_gb": 0.0, "commit_free_min_gb": 1e9}
                self.history = []
                self._state, self._reason, self._critical_since = "ok", "", None
                self._pressure_rss = None
        if first:
            self._start(label, log_path)
        try:
            yield self
        finally:
            with self._lock:
                self._depth -= 1
                last = self._depth == 0
            if last:
                self._finish()

    def _start(self, label: str, log_path) -> None:
        self._lower_priority()
        snap = self.snapshot(max_age=0)
        self._apply_threads(self.pick_threads(snap))
        if log_path is None and self.log_path is None:
            log_path = Path(os.environ.get("TEMP", ".")) / "treepolo_resource_monitor.log"
        if log_path is not None:
            self.log_path = Path(log_path)
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                if self.log_path.exists() and self.log_path.stat().st_size > 8 * 2 ** 20:
                    self.log_path.replace(self.log_path.with_suffix(".old"))
                self._log = open(self.log_path, "a", encoding="utf-8", buffering=1)
                self._log.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} start {label}: threads {self.threads}, ram total {snap.ram_total / GB:.1f} GB, available {snap.ram_available / GB:.1f} GB\n")
            except Exception:
                self._log = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor, name="resource-monitor", daemon=True)
        self._thread.start()

    def _finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        if self._log is not None:
            try:
                self._log.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} end {self._label}: peaks {self.peaks}\n")
                self._log.close()
            except Exception:
                pass
            self._log = None
        self._restore_priority()

    def _monitor(self) -> None:
        last_threads = self._clock()
        while not self._stop.is_set():
            try:
                snap = self.sampler()
                with self._lock:
                    self._snap = snap
                state, why = self.judge(snap)
                now = self._clock()
                with self._lock:
                    self._state, self._reason = state, why
                    self._critical_since = (self._critical_since or now) if state == "critical" else None
                    others = max(snap.cpu_system - snap.cpu_ours, 0.0)
                    self.cpu_rest = 0.0 if others < 60 else min(0.25, (others - 60) / 160.0)       # the others need the CPU: rest more
                    self.duty = 0.0 if (snap.gpu_temp or 0) < 80 else 0.5
                    p = self.peaks
                    p["rss_ours_gb"] = max(p["rss_ours_gb"], snap.rss_ours / GB); p["ram_available_min_gb"] = min(p["ram_available_min_gb"], snap.ram_available / GB)
                    p["cpu_system_max"] = max(p["cpu_system_max"], snap.cpu_system); p["cpu_ours_max"] = max(p["cpu_ours_max"], snap.cpu_ours)
                    p["gpu_util_max"] = max(p["gpu_util_max"], snap.gpu_util or 0.0); p["gpu_used_max_gb"] = max(p["gpu_used_max_gb"], snap.gpu_used / GB)
                    if snap.commit_limit:
                        p["commit_free_min_gb"] = min(p["commit_free_min_gb"], snap.commit_free / GB)
                    self.history.append({"t": snap.taken, "rss_gb": snap.rss_ours / GB, "ram_free_gb": snap.ram_available / GB, "cpu_system": snap.cpu_system, "cpu_ours": snap.cpu_ours,
                                         "gpu_util": snap.gpu_util, "gpu_used_gb": snap.gpu_used / GB, "state": state})
                    if len(self.history) > 20000:
                        del self.history[:5000]
                if self._log is not None:
                    self._log.write(f"{time.strftime('%H:%M:%S')} {state:8s} rss={snap.rss_ours / GB:.2f}GB ram_free={snap.ram_available / GB:.2f}GB commit_free={snap.commit_free / GB if snap.commit_limit else -1:.1f}GB "
                                    f"cpu_sys={snap.cpu_system:.0f}% cpu_ours={snap.cpu_ours:.0f}% gpu={snap.gpu_util if snap.gpu_util is not None else -1:.0f}% gpu_used={snap.gpu_used / GB:.2f}GB "
                                    f"gpu_temp={snap.gpu_temp if snap.gpu_temp is not None else -1:.0f} threads={self.threads} {why}\n")
                if now - last_threads > 10:
                    last_threads = now
                    want = self.pick_threads(snap)
                    if want != self.threads:
                        self._apply_threads(want)
                # last resort: the run does not reach a checkpoint (it is inside one long call) while the machine is in trouble
                if self._critical_since is not None and now - self._critical_since > self.critical_grace and self._owner is not None and not self._waiting:
                    self._interrupt_owner()
                    self._critical_since = now
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _interrupt_owner(self) -> None:
        try:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self._owner), ctypes.py_object(ResourceExhausted))
        except Exception:
            pass

    # ------------------------------------------------------------------------------------------------ checkpoints
    @property
    def state(self) -> str:
        return self._state

    def checkpoint(self, label: str = "", *, wait: float = 120.0) -> str:
        """Called by loops. Rests when the others need the CPU, waits while resources are scarce, raises ``ResourceExhausted`` when they stay scarce or are nearly gone."""

        if self._depth == 0:                                                # no run is being watched (a script that did not ask for it): sample directly
            snap = self.snapshot()
            state, why = self.judge(snap)
        else:
            state, why = self._state, self._reason
            if self._thread is None or not self._thread.is_alive():
                state, why = self.judge(self.snapshot())
        if self.cpu_rest > 0:
            self._sleep(self.cpu_rest)
        if state == "critical":                                             # nearly out of memory: wait (pause) until it is not, never give up
            state, why = self._pause(label, why, until="not_critical")
        if state == "pressure":
            return self._under_pressure(label, wait, why)
        self._pressure_rss = None
        return state

    def _pause(self, label: str, why: str, *, until: str) -> tuple[str, str]:
        """Wait, doing nothing, until the memory is back (``until='ok'``) or at least not nearly gone (``'not_critical'``). A run that has to wait is paused, not stopped: it goes on from
        where it was as soon as the computer has the memory again. The monitor does not interrupt it while it waits."""

        self._waiting = True
        last = self._clock()
        try:
            while True:
                self._sleep(1.0)
                state, why = (self._state, self._reason) if self._depth else self.judge(self.snapshot(max_age=0))
                if state == "ok" or (until == "not_critical" and state != "critical"):
                    return state, why
                if self._log is not None and self._clock() - last >= 60.0:
                    last = self._clock()
                    try:
                        self._log.write(f"# {time.strftime('%H:%M:%S')} paused at {label or self._label}: {why}\n")
                    except Exception:
                        pass
        finally:
            self._waiting = False

    def shrink(self) -> None:
        """Give back what this process holds without needing: garbage, and the graphics-memory cache of torch. Cheap, and it never changes a result."""

        gc.collect()
        torch = sys.modules.get("torch")
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        with self._lock:
            self._snap = None                                                            # the next look at the machine sees what was given back
        self._last_shrink = self._clock()

    def _under_pressure(self, label: str, wait: float, why: str) -> str:
        """The memory is short but not nearly gone. First give back what this process can. If that does not end the shortage: when it is not this process's doing (its own memory has not grown
        since the shortage began) go on in smaller pieces, because waiting would free nothing; when this process is the one that grows, wait for the shortage to end and stop safely if it does not."""

        if self._clock() - self._last_shrink >= self.shrink_every:
            self.shrink()
            state, why = self.judge(self.snapshot(max_age=0))
            if state == "ok":
                self._pressure_rss = None
                return "ok"
            if state == "critical":
                raise ResourceExhausted(f"stopped safely at {label or self._label}: {why}")
        snap = self.snapshot()
        if self._pressure_rss is None:
            self._pressure_rss = snap.rss_ours
        if snap.rss_ours - self._pressure_rss <= self.grow_allowance:
            return "pressure"
        self._pause(label, why, until="ok")                                   # this run is the one that grows: pause until the shortage is over
        self._pressure_rss = None
        return "ok"

    def require(self, nbytes: int, what: str = "") -> None:
        """Refuse (safely) to start something that needs ``nbytes`` of memory when that would leave less free than the 'stop' line plus a margin. Below the 'slow down' line a step may still start:
        it runs in smaller pieces and gives back what it can; only what would leave the computer nearly out of memory is refused."""

        snap = self.snapshot(max_age=0.5)
        floor = self.limits(snap).ram_hard + self.require_margin
        if snap.ram_available - nbytes < floor:
            self.shrink()
            if nbytes + floor > snap.ram_total:                                # it could never fit, however much the others give back: that is an error, not something to wait for
                raise ResourceExhausted(f"{what or 'this step'} needs about {nbytes / GB:.1f} GB, more than the computer can spare ({snap.ram_total / GB:.1f} GB in all) / {what or '這一步'}需要約 {nbytes / GB:.1f} GB，超過這台電腦能給的")
            self._waiting = True
            try:
                while self.snapshot(max_age=0).ram_available - nbytes < floor:        # pause until the memory is there
                    self._sleep(1.0)
            finally:
                self._waiting = False
            snap = self.snapshot(max_age=0)
            if False:
                raise ResourceExhausted(f"{what or 'this step'} needs about {nbytes / GB:.1f} GB but only {snap.ram_available / GB:.1f} GB is free and {floor / GB:.1f} GB must stay free for you / "
                                        f"{what or '這一步'}需要約 {nbytes / GB:.1f} GB，目前可用只有 {snap.ram_available / GB:.1f} GB，要留 {floor / GB:.1f} GB 給你")

    # ------------------------------------------------------------------------------------------------ pieces sized from what is free now
    def thread_count(self) -> int:
        """Threads to use now: the number chosen for the run that is being watched, else what the CPU can spare at this moment."""

        return self.threads if self._depth else self.pick_threads()

    def ram_budget(self) -> int:
        snap = self.snapshot()
        return max(snap.ram_available - self.limits(snap).ram_soft, 0)

    def chunk_rows(self, bytes_per_row: float, *, share: float = 0.25, floor: int = 2000, ceiling: int = 5_000_000) -> int:
        """How many rows to process at once so that the piece takes at most ``share`` of the memory that may be used now (a quarter of that while under pressure)."""

        share = share / 4 if self._state != "ok" else share
        return int(max(floor, min(ceiling, share * self.ram_budget() / max(bytes_per_row, 1.0))))

    # ------------------------------------------------------------------------------------------------ the graphics card
    def device(self, requested: str = "auto") -> str:
        """'cuda' when the card may be used now (it is wanted, present and has more than the reserve for the display free), else 'cpu'. Also limits this process's share of its memory."""

        if requested == "cpu":
            return "cpu"
        try:
            import torch

            if not torch.cuda.is_available():
                return "cpu"
            free, total = torch.cuda.mem_get_info()
        except Exception:
            return "cpu"
        lim = Limits.for_total(16 * GB)
        if free - lim.gpu_reserve < int(0.5 * GB):
            return "cpu"
        if not self._gpu_ready:
            try:
                torch.cuda.set_per_process_memory_fraction(float(min(0.6, max(0.1, (free - lim.gpu_reserve) / total))))
                self._gpu_ready = True
            except Exception:
                pass
        return "cuda"

    def batch_items(self, bytes_per_item: float, device: str = "cpu", *, ceiling: int = 512) -> int:
        """How many items (pitchers) to put in one batch so that the batch takes at most a fifth of what may be used now: the graphics memory above what the display needs when the card is used,
        else the memory still free above the limit; a quarter of that while the machine is under pressure. Always at least one item. Looked up at the moment of the call, so it follows the free memory."""

        if device == "cuda":
            usable = max(self.gpu_free_bytes() - Limits.for_total(16 * GB).gpu_reserve, 0)
            share = 0.2 if self._state == "ok" else 0.05
            return int(max(1, min(ceiling, share * usable / max(bytes_per_item, 1.0))))
        return int(max(1, self.chunk_rows(bytes_per_item, share=0.2, floor=1, ceiling=ceiling)))

    def gpu_free_bytes(self) -> int:
        """Graphics memory free now (looked up at most twice a second: the call goes to the driver)."""

        now = self._clock()
        cached = getattr(self, "_gpu_free_cache", None)
        if cached is not None and now - cached[0] < 0.5:
            return cached[1]
        try:
            import torch

            value = int(torch.cuda.mem_get_info()[0]) if torch.cuda.is_available() else 0
        except Exception:
            value = 0
        self._gpu_free_cache = (now, value)
        return value

    def _rest(self, busy: float) -> None:
        """Count the time the card was busy and, once it adds up to ``rest_after``, rest for a share of it (``duty``) so the display always gets its turn."""

        if self.duty <= 0:
            self._busy_acc = 0.0
            return
        self._busy_acc += busy
        if self._busy_acc >= self.rest_after:
            self._sleep(min(0.25, self._busy_acc * self.duty))
            self._busy_acc = 0.0

    def gpu_pause(self, torch_module=None, busy: float | None = None) -> float:
        """After work on the card: wait until it is finished (so a long queue of calls never builds up) and rest when enough work has piled up. Returns the seconds the work took."""

        try:
            if torch_module is not None:
                torch_module.cuda.synchronize()
        except Exception:
            pass
        ran = (self._clock() - self._last_gpu_sync) if busy is None else busy
        self._rest(ran)
        self._last_gpu_sync = self._clock()
        return ran

    def gpu_chunks(self, n: int, bytes_per_row: float, *, torch_module=None, target: float = 0.1, cap: int = 262_144) -> Iterator[slice]:
        """Slices of ``range(n)`` for work on the card: sized from the graphics memory free now (a fifth of what is above the reserve per piece) and adapted so that one call takes about
        ``target`` seconds. After every piece the card is synchronised and rested (``gpu_pause``), and a checkpoint is made."""

        free = self.gpu_free_bytes() if torch_module is not None else 0
        by_memory = int(max(0.2 * max(free - Limits.for_total(16 * GB).gpu_reserve, 0.2 * GB) / max(bytes_per_row, 1.0), 1024)) if torch_module is not None else cap
        size = int(max(1024, min(cap, by_memory)))
        i = 0
        self._last_gpu_sync = self._clock()
        while i < n:
            j = min(n, i + size)
            t0 = self._clock()
            yield slice(i, j)
            if torch_module is not None:
                try:
                    torch_module.cuda.synchronize()                     # the piece must be finished before the next one is queued
                except Exception:
                    pass
                ran = self._clock() - t0
                self._rest(ran)
            else:
                ran = self._clock() - t0
            i = j
            if ran > target:
                size = max(1024, size // 2)
            elif ran < target / 3:
                size = int(min(cap, by_memory, size * 2))
            self.checkpoint("gpu work")


guard = Guard()
