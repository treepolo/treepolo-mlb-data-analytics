"""How a research method says how far it is.

A method reports ``(stage, fraction, detail)`` with the fraction between 0 and 1 (``None`` when it cannot say). ``Progress`` turns the counters of the loops a method already has (pitchers solved,
batches scored, groups done ...) into one overall fraction for the whole run: the run is cut into phases with a share of the total each (rough shares, from the times the phases took before), and
inside a phase the fraction is ``done / total``. Updating is only a counter and a clock reading: a report goes out at most every ``min_interval`` seconds (and always when a phase ends), so it
adds nothing noticeable to the work. The fraction never goes backwards."""
from __future__ import annotations

import time
from typing import Callable, Sequence

Report = Callable[[str, "float | None", "str | None"], None]


class Progress:
    def __init__(self, report: "Report | None", stage: str, phases: Sequence[tuple[str, float]], *, min_interval: float = 0.25, clock: Callable[[], float] = time.monotonic):
        self.report, self.stage, self._clock, self.min_interval = report, stage, clock, float(min_interval)
        total = float(sum(w for _, w in phases)) or 1.0
        self._start: dict[str, float] = {}; self._share: dict[str, float] = {}
        acc = 0.0
        for name, weight in phases:
            self._start[name] = acc / total; self._share[name] = float(weight) / total; acc += float(weight)
        self._phase = phases[0][0] if phases else ""
        self._last_time = -1e9
        self._last_fraction = 0.0

    def _emit(self, fraction: float, detail: "str | None", force: bool = False) -> None:
        fraction = max(self._last_fraction, min(1.0, fraction))                                        # never backwards
        now = self._clock()
        if self.report is None or (not force and now - self._last_time < self.min_interval):
            self._last_fraction = fraction
            return
        self._last_time = now; self._last_fraction = fraction
        self.report(self.stage, fraction, detail)

    def phase(self, name: str, detail: "str | None" = None) -> None:
        """Start a phase: the fraction jumps to where the phase begins."""

        self._phase = name
        self._emit(self._start[name], detail, force=True)

    def step(self, done: float, total: float, detail: "str | None" = None, phase: "str | None" = None) -> None:
        """``done`` of ``total`` units of the current (or the named) phase are finished."""

        name = phase or self._phase
        frac = self._start[name] + self._share[name] * (min(max(float(done), 0.0), float(total)) / float(total) if total else 1.0)
        self._emit(frac, detail, force=bool(total) and done >= total)
