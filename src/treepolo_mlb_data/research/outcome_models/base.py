"""The interface every outcome model implements, and what they share.

An outcome model predicts the eight-way result of a pitch (ball, called strike, foul, foul tip, hit by pitch, home run, other ball in play, whiff) from the situation, the
pitch type and, depending on the bias controls, who is pitching and batting. The decision engine and the situation query only know this interface.

    fit(train, valid, settings, progress)           train on columns (a dict of arrays as ``fetch_columns`` gives them)
    predict_proba(rows, effects="population")       [n, 8] probabilities, in the order of ``self.classes``
    predict_members(rows, effects=...)              [members, n, 8], one slice per bagged member (what the intervals come from)
    to_bytes() / OutcomeModel.from_bytes(b)         storage

``effects="population"`` is the league-average player (every player effect 0, descriptors at the league mean); ``effects="specific"`` uses the effects of the pitcher and the
batter in ``rows`` (a player the model has not seen counts as the league average).
"""
from __future__ import annotations

import pickle
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy import sparse

from .. import gbm
from .. import league_core as lc
from .. import modeling as md
from ..config_schema import ConfigError
from ..profiles import PITCH_TYPES, ProfileModel
from . import controls as ctl
from . import design as dz
from .shrinkage import class_info, info_per_pitch

Progress = Callable[[str, "float | None", "str | None"], None]
EFFECT_MODES = ("population", "specific")


def fill_defaults(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Every setting a model reads, from ``settings`` or its default (a caller that is not the research method may pass only a few)."""

    s: dict[str, Any] = {f.name: f.default for f in ctl.CONTROL_FIELDS + ctl.PARAM_FIELDS}
    s.update({"class_merge": None, "seed": 0})
    s.update(dict(settings))
    if s["class_merge"] is None:
        s["class_merge"] = dict(md.DEFAULT_CLASS_MERGE)
    return s


class OutcomeModel:
    name = ""
    label_zh = ""
    label_en = ""
    uses_design = True          # the sparse design matrix of ``_pack`` is needed (a model that builds its own features says False)
    supports: dict[str, str] = {}
    force_auto = False          # the model always lets the data choose the shrinkage of the effects (the mixed model)
    cost_rule = "linear"        # how the time grows (see ``estimate``): linear (rows), rounds (rows x rounds), epochs (rows x epochs), iterations (rows x iterations)
    probe_settings: dict[str, Any] = {}            # the settings of the short trial run that measures the speed (the rest of the settings stay as the user wrote them)
    probe_unit = 1                                 # the number of rounds / epochs / iterations the trial run does (0 = the full run: the time does not grow with a count)

    def __init__(self) -> None:
        self.settings: dict[str, Any] = {}
        self.classes: list[str] = []
        self.members: list[Any] = []
        self.groups: dict[str, bool] = {}              # effect group -> within (not shrunk)
        self.indexes: dict[str, dz.EffectIndex] = {}
        self.profile: ProfileModel | None = None
        self.desc_std: dz.Standardizer | None = None
        self.loc_std: dz.Standardizer | None = None
        self.pool: dz.LocationPool | None = None
        self.info: dict[str, Any] = {}
        self.group: str | None = None                  # the handedness group this model was trained for (set by the research method)
        self.roster: dict[str, dict[int, dict[str, Any]]] = {"pitcher": {}, "batter": {}}
        self.pitch_info: float = 0.08                  # what one pitch tells about the logit of an outcome, on average over the classes (see ``shrinkage.info_per_pitch``)
        self.class_info: np.ndarray | None = None      # the same per outcome class: p * (1 - p)

    # ------------------------------------------------------------------------------------------------ who the model has seen
    def players(self, side: str) -> tuple[np.ndarray, np.ndarray]:
        """(ids, pitches) of the players of ``side`` the model was trained on, most pitches first."""

        items = sorted(self.roster[side].items(), key=lambda kv: -kv[1]["n"])
        return np.array([k for k, _ in items], dtype=np.int64), np.array([v["n"] for _, v in items], dtype=np.int64)

    def player_info(self, side: str, ident: Any) -> dict[str, Any]:
        """Pitches seen, the pitch-type counts of a pitcher, and how much of his own data the effect uses (information / (information + penalty), where the information of n pitches is
        n * ``pitch_info`` and the penalty is the precision of the prior of the effects: the estimated one in the automatic mode; None without effects). ``known`` is False for a player
        the model has not seen: his answers equal the league average."""

        try:
            key = int(ident)
        except (TypeError, ValueError):
            return {"id": ident, "side": side, "pitches": 0, "known": False, "arsenal": {}, "shrinkage": None}
        entry = self.roster[side].get(key)
        n = 0 if entry is None else int(entry["n"])
        shrink = None
        if side in self.groups and n:
            by_class = (self.info.get("penalties_by_class") or {}).get(side)
            v = self.class_info if self.class_info is not None else np.full(max(len(self.classes), 1), float(getattr(self, "pitch_info", 0.08)))
            if self.groups[side]:
                pen = np.full(len(v), ctl.WITHIN_PENALTY)
            elif by_class is not None:
                pen = np.asarray(by_class, dtype=float)                                       # what the data chose, per outcome class
            else:
                pen = np.full(len(v), float((self.info.get("penalties") or {}).get(side, self.settings.get(f"effect_penalty_{side}", 0.0))))
            information = n * v
            with np.errstate(divide="ignore", invalid="ignore"):
                share = np.where(information + pen > 0, information / (information + pen), np.nan)
            shrink = float(np.nanmean(share)) if np.isfinite(share).any() else None
        return {"id": key, "side": side, "pitches": n, "known": entry is not None, "arsenal": {} if entry is None else dict(entry.get("arsenal", {})), "shrinkage": shrink}

    def _build_roster(self, sub: Mapping[str, np.ndarray]) -> None:
        typ = dz.type_index(sub["pitch_type"])
        pit = np.asarray(sub["pitcher"]).astype(np.int64)
        roster: dict[str, dict[int, dict[str, Any]]] = {"pitcher": {}, "batter": {}}
        keys, cnt = np.unique(pit * 16 + typ, return_counts=True)
        for k, c in zip(keys.tolist(), cnt.tolist()):
            p, t = k // 16, k % 16
            entry = roster["pitcher"].setdefault(int(p), {"n": 0, "arsenal": {}})
            entry["n"] += int(c); entry["arsenal"][PITCH_TYPES[t]] = int(c)
        ids, n = np.unique(np.asarray(sub["batter"]).astype(np.int64), return_counts=True)
        roster["batter"] = {int(i): {"n": int(c)} for i, c in zip(ids.tolist(), n.tolist())}
        self.roster = roster

    # ------------------------------------------------------------------------------------------------ training
    def fit(self, train: Mapping[str, np.ndarray], valid: Mapping[str, np.ndarray] | None = None, settings: Mapping[str, Any] | None = None,
            progress: Progress | None = None, classes: Sequence[str] | None = None) -> "OutcomeModel":
        """``classes``: the class names and their order, when several fits must line up (cross-validation folds, years); otherwise taken from the data."""

        s = fill_defaults(settings or {})
        self.settings = s
        ctl.validate_controls(s, model=self.name, backend=s["backend"])
        if classes is not None:
            self.classes = list(classes)
        sub, y = self._encode(train, s, fixed_classes=classes is not None)
        vsub, vy = (None, None) if valid is None else self._encode(valid, s, fixed_classes=True)
        self._prepare(sub, s)
        shares = np.bincount(y, minlength=len(self.classes)) / max(len(y), 1)
        self.pitch_info = info_per_pitch(shares); self.class_info = class_info(shares)
        design = self._training_design(sub, s)
        games = np.asarray(sub["game_pk"])
        reps = int(s["bagging_reps"]); rng = np.random.default_rng(int(s["seed"]))
        self.members, infos = [], []
        for r in range(reps):
            w = None if reps == 1 else gbm.poisson_game_weights(games, rng)
            member, info = self._fit_member(sub, y, w, vsub, vy, s, int(s["seed"]) + 1000 * (r + 1), progress, design)
            self.members.append(member); infos.append(info)
            if progress:
                progress("outcome_model", (r + 1) / reps, f"member {r + 1}/{reps}")
        self.info = self._summarize(infos, s)
        for key in [k for k in self.__dict__ if k.startswith("_x_")]:           # training-only matrices must not be stored with the model
            delattr(self, key)
        return self

    def _encode(self, cols: Mapping[str, np.ndarray], s: Mapping[str, Any], fixed_classes: bool = False):
        merge = s["class_merge"]
        outcome = np.asarray(cols["outcome"]).astype(str)
        if fixed_classes:
            lookup = {c: i for i, c in enumerate(self.classes)}
            y_all = np.array([lookup.get(merge.get(o, ""), -1) for o in outcome], dtype=np.int64)
            keep = y_all >= 0
        else:
            y_all, classes, keep = lc.encode_outcome(outcome, merge)
            self.classes = list(classes)
        ok = keep & (dz.type_index(cols["pitch_type"]) >= 0)
        idx = np.nonzero(ok)[0]
        return lc.take(cols, idx), y_all[ok]

    def _prepare(self, sub: Mapping[str, np.ndarray], s: Mapping[str, Any]) -> None:
        self.groups = ctl.effect_groups(s)
        for g in self.groups:
            need = {"pitcher": ("pitcher",), "batter": ("batter",), "pitcher_pitch": ("pitcher",), "batter_pitch": ("batter",), "pitcher_game": ("pitcher", "game_pk"),
                    "pitcher_game_pitch": ("pitcher", "game_pk"), "batter_count": ("batter",)}[g]
            missing = [c for c in need if c not in sub]
            if missing:
                raise ConfigError(f"the data lacks the columns {missing} that the effect {g} needs / 資料缺少效應 {g} 需要的欄位 {missing}")
        self.indexes = dz.fit_effect_indexes(sub, list(self.groups))
        if "pitcher" in sub and "batter" in sub:
            self._build_roster(sub)
        self.profile = ProfileModel().fit(sub) if "descriptors" in (s["control_pitcher"], s["control_batter"]) else None
        self.desc_std = None
        self.loc_std = self.pool = None
        if s["control_location"] == "include_and_average":
            x = np.asarray(sub["plate_x"], dtype=float); z = np.asarray(sub["plate_z"], dtype=float)
            ok = np.isfinite(x) & np.isfinite(z)
            self.loc_std = dz.Standardizer(np.column_stack([x[ok], z[ok]]))
            self.pool = dz.LocationPool(sub, seed=int(s["seed"]))

    def _descriptors(self, rows: Mapping[str, np.ndarray], *, history: bool) -> np.ndarray:
        parts = []
        if self.settings["control_pitcher"] == "descriptors":
            parts.append(self.profile.transform(rows, "pitcher", history=history))
        if self.settings["control_batter"] == "descriptors":
            parts.append(self.profile.transform(rows, "batter", history=history))
        return np.hstack(parts).astype(np.float64) if parts else np.zeros((len(rows["game_pk"]), 0))

    def _dense_design(self, rows: Mapping[str, np.ndarray], *, training: bool, effects: str = "specific") -> sparse.csr_matrix:
        """Situation block, descriptors (zeros = the league mean for the population) and the location terms."""

        sit, _ = dz.situation_design(rows)
        blocks = [sit]
        if self.profile is not None:
            if training:
                d = self._descriptors(rows, history=True); self.desc_std = dz.Standardizer(d)
                blocks.append(sparse.csr_matrix(self.desc_std(d).astype(np.float32)))
            elif effects == "population":
                blocks.append(sparse.csr_matrix((len(rows["game_pk"]), len(self.desc_std.mean)), dtype=np.float32))
            else:
                blocks.append(sparse.csr_matrix(self.desc_std(self._descriptors(rows, history=False)).astype(np.float32)))
        if self.loc_std is not None:
            blocks.append(dz.location_terms(rows["plate_x"], rows["plate_z"], dz.type_index(rows["pitch_type"]), self.loc_std))
        return sparse.hstack(blocks, format="csr")

    def _training_design(self, sub: Mapping[str, np.ndarray], s: Mapping[str, Any]):
        D = self._dense_design(sub, training=True) if self.uses_design else None
        E, spans = dz.effect_matrix(sub, self.indexes, list(self.groups))
        return D, E, spans

    def _penalties(self, n_dense: int, s: Mapping[str, Any]) -> np.ndarray:
        parts = [np.full(n_dense, float(s["l2"]))]
        for g, within in self.groups.items():
            parts.append(np.full(len(self.indexes[g]), ctl.WITHIN_PENALTY if within else float(s[f"effect_penalty_{g}"])))
        return np.concatenate(parts)

    def _use_auto(self, s: Mapping[str, Any], E) -> bool:
        """Whether the data choose the shrinkage of the effects: the automatic mode (or a model that always does) with at least one effect group that is not 'within'."""

        return bool(self.groups) and E.shape[1] > 0 and any(not w for w in self.groups.values()) and (self.force_auto or s.get("effect_penalty_mode", "auto") == "auto")

    def _fit_member(self, sub, y, w, vsub, vy, s, seed, progress, design):
        raise NotImplementedError

    def _summarize(self, infos: list[dict[str, Any]], s: Mapping[str, Any]) -> dict[str, Any]:
        estimated = infos[0].get("estimated_penalties") if infos else None
        by_class = {g: [float(x) for x in np.atleast_1d(v)] for g, v in (estimated or {}).items()}
        return {"model": self.name, "backend": s["backend"] if self.name == "gbm" else "", "members": len(infos), "effects": {g: len(self.indexes[g]) for g in self.groups},
                "within": [g for g, w in self.groups.items() if w], "penalties_estimated": bool(estimated), "pitch_info": float(self.pitch_info),
                "penalties_by_class": by_class, "classes_order": list(self.classes),
                "penalties": {g: float(np.median(v)) for g, v in by_class.items()} if estimated else {g: (ctl.WITHIN_PENALTY if w else float(s[f"effect_penalty_{g}"])) for g, w in self.groups.items()},
                "n_iter": float(np.mean([i.get("n_iter", np.nan) for i in infos])), "hit_cap": any(bool(i.get("hit_cap")) for i in infos), "seconds": float(sum(i.get("seconds", 0.0) for i in infos)),
                "trace": infos[0].get("trace", []), "descriptors": self.profile is not None, "location": self.pool is not None, "classes": list(self.classes)}

    # ------------------------------------------------------------------------------------------------ prediction
    def _pack(self, rows: Mapping[str, np.ndarray], effects: str) -> dict[str, Any]:
        pack: dict[str, Any] = {"n": len(rows["game_pk"]), "rows": rows}
        if self.uses_design:
            pack["D"] = self._dense_design(rows, training=False, effects=effects)
        if effects == "specific" and self.groups:
            missing = [c for c in ("pitcher", "batter", "game_pk") if c not in rows]
            if missing and any(g in self.groups for g in ("pitcher", "batter")):
                raise ConfigError(f"effects='specific' needs the columns {missing} / effects='specific' 需要欄位 {missing}")
            pack["E"], _ = dz.effect_matrix(rows, self.indexes, list(self.groups))
        return pack

    def _member_proba(self, member: Any, pack: dict[str, Any], effects: str) -> np.ndarray:
        raise NotImplementedError

    def _predict_chunk(self, rows: Mapping[str, np.ndarray], effects: str) -> np.ndarray:
        pack = self._pack(rows, effects)
        return np.stack([self._member_proba(m, pack, effects) for m in self.members])

    def predict_members(self, rows: Mapping[str, np.ndarray], *, effects: str = "population", seed: int = 0) -> np.ndarray:
        """[members, n, classes]. With ``control_location = include_and_average`` every row is predicted at several historical locations and the probabilities are averaged."""

        if effects not in EFFECT_MODES:
            raise ConfigError(f"effects must be one of {list(EFFECT_MODES)} / effects 必須是 {list(EFFECT_MODES)} 其中之一")
        if not self.members:
            raise ConfigError("the model is not fitted / 模型還沒有訓練")
        if self.pool is None:
            return self._predict_chunk(rows, effects)
        n = len(rows["game_pk"]); S = int(self.settings["location_samples"])
        typ = dz.type_index(rows["pitch_type"]); cell = dz.count_cell(rows)
        pitcher = np.asarray(rows["pitcher"]) if effects == "specific" and "pitcher" in rows else None
        xs, zs = self.pool.sample(np.maximum(typ, 0), cell, pitcher, S, np.random.default_rng(seed))
        out = np.zeros((len(self.members), n, len(self.classes)))
        step = max(1, 400_000 // S)
        for a in range(0, n, step):
            sl = slice(a, min(n, a + step)); m = sl.stop - sl.start
            rep = {k: np.repeat(np.asarray(v)[sl], S, axis=0) for k, v in rows.items() if isinstance(v, np.ndarray) and len(v) == n}
            rep["plate_x"] = xs[sl].reshape(-1); rep["plate_z"] = zs[sl].reshape(-1)
            out[:, sl] = self._predict_chunk(rep, effects).reshape(len(self.members), m, S, len(self.classes)).mean(axis=2)
        return out

    def predict_proba(self, rows: Mapping[str, np.ndarray], *, effects: str = "population", seed: int = 0) -> np.ndarray:
        return self.predict_members(rows, effects=effects, seed=seed).mean(axis=0)

    # ------------------------------------------------------------------------------------------------ storage
    def to_bytes(self) -> bytes:
        return pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def from_bytes(data: bytes) -> "OutcomeModel":
        model = pickle.loads(data)
        if not isinstance(model, OutcomeModel):
            raise ConfigError("the stored model is not an outcome model / 儲存的內容不是結果模型")
        return model

    def describe(self) -> dict[str, Any]:
        return {"model": self.name, "controls": {k: self.settings.get(k) for k in ctl.CONTROL_NAMES}, "info": {k: v for k, v in self.info.items() if k != "trace"}}
