from __future__ import annotations

import re
import sys
import threading
import time
from typing import TextIO

_INDEX_RE = re.compile(r"^CREATE\s+INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+([^\s(]+)", re.IGNORECASE)
_SPINNER = "|/-\\"


class OptimizeProgressDisplay:
    """Render truthful step-based progress for the long-running SQLite optimize command.

    SQLite does not expose a reliable percentage for CREATE INDEX or ANALYZE, so
    this display reports the current phase, current SQL operation, and elapsed
    time.  The three-phase bar is phase-based rather than a fabricated estimate
    of rows processed.
    """

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stdout
        self.is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.phase = 1
        self.total_phases = 3
        self.phase_label = "建立／確認分析索引 Build/check analysis indexes"
        self.detail = "準備中 Preparing"
        self.started = time.monotonic()
        self.phase_started = self.started
        self._seen_analyze = False
        self._activity = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_width = 0

    def start(self) -> None:
        self.started = self.phase_started = time.monotonic()
        if self.is_tty:
            self._thread = threading.Thread(target=self._spin, name="treepolo-optimize-progress", daemon=True)
            self._thread.start()
        else:
            self._print_line(self._snapshot("..."))

    def trace_sql(self, sql: str) -> None:
        text = " ".join(str(sql).strip().split())
        upper = text.upper()
        match = _INDEX_RE.match(text)
        if match:
            name = match.group(1).strip('"`[]')
            self._activity = True
            self._set_detail(f"索引 Index: {name}")
            return
        if upper.startswith("ANALYZE"):
            self._activity = True
            self._seen_analyze = True
            self._transition(2, "分析查詢規劃統計 Analyze planner statistics", "ANALYZE")
            return
        if upper.startswith("PRAGMA OPTIMIZE"):
            self._activity = True
            if self._seen_analyze:
                self._transition(3, "完成 SQLite 規劃器最佳化 Finalize SQLite optimizer", "PRAGMA optimize")
            else:
                self._set_detail("PRAGMA optimize（索引階段 index phase）")

    def finish(self) -> None:
        self._stop_spinner()
        elapsed = time.monotonic() - self.started
        if self.is_tty:
            self._clear_tty_line()
        if self._activity:
            self._print_line(self._bar(self.total_phases) + f" 完成 Completed · 總耗時 Total: {self._format_elapsed(elapsed)}")
        else:
            self._print_line("SQLite Optimize：沒有需要處理的逐球資料。 No pitch table to optimize.")

    def fail(self, label: str = "失敗 Failed") -> None:
        self._stop_spinner()
        if self.is_tty:
            self._clear_tty_line()
        elapsed = time.monotonic() - self.started
        self._print_line(self._bar(max(0, self.phase - 1)) + f" {label} · 已耗時 Elapsed: {self._format_elapsed(elapsed)}")

    def _set_detail(self, detail: str) -> None:
        with self._lock:
            changed = detail != self.detail
            self.detail = detail
        if changed and not self.is_tty:
            self._print_line(self._snapshot("..."))

    def _transition(self, phase: int, label: str, detail: str) -> None:
        with self._lock:
            if phase == self.phase:
                self.phase_label = label
                self.detail = detail
                return
            previous_phase = self.phase
            previous_label = self.phase_label
            previous_elapsed = time.monotonic() - self.phase_started
            self.phase = phase
            self.phase_label = label
            self.detail = detail
            self.phase_started = time.monotonic()
        if not self.is_tty:
            self._print_line(self._bar(previous_phase) + f" {previous_label} ✓ {self._format_elapsed(previous_elapsed)}")
            self._print_line(self._snapshot("..."))

    def _spin(self) -> None:
        frame = 0
        while not self._stop.wait(0.12):
            text = self._snapshot(_SPINNER[frame % len(_SPINNER)])
            frame += 1
            self._write_tty(text)

    def _snapshot(self, spinner: str) -> str:
        with self._lock:
            phase = self.phase
            label = self.phase_label
            detail = self.detail
            phase_started = self.phase_started
        elapsed = time.monotonic() - phase_started
        return f"{self._bar(max(0, phase - 1))} {phase}/{self.total_phases} {spinner} {label} · {detail} · {self._format_elapsed(elapsed)}"

    def _bar(self, completed: int) -> str:
        width = 18
        completed = max(0, min(completed, self.total_phases))
        filled = round(width * completed / self.total_phases)
        return "[" + "█" * filled + "░" * (width - filled) + "]"

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _write_tty(self, text: str) -> None:
        with self._lock:
            width = max(self._last_width, len(text))
            self._last_width = len(text)
        self.stream.write("\r" + text.ljust(width))
        self.stream.flush()

    def _clear_tty_line(self) -> None:
        width = max(self._last_width, 1)
        self.stream.write("\r" + (" " * width) + "\r")
        self.stream.flush()

    def _print_line(self, text: str) -> None:
        self.stream.write(text + "\n")
        self.stream.flush()

    def _stop_spinner(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def optimize_with_progress(store, stream: TextIO | None = None) -> None:
    """Run the store's canonical optimizer while observing its real SQL steps."""

    display = OptimizeProgressDisplay(stream)
    display.start()
    store.conn.set_trace_callback(display.trace_sql)
    try:
        store.optimize()
    except KeyboardInterrupt:
        display.fail("已中斷 Interrupted")
        raise
    except BaseException:
        display.fail()
        raise
    finally:
        store.conn.set_trace_callback(None)
    display.finish()
