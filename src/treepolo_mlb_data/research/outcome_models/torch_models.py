"""Neural-network outcome models (PyTorch): ``nn_embed`` (embeddings of the players) and ``sequence`` (the pitches earlier in the plate appearance).

Both follow the same interface as every other outcome model and the same bias-control settings:

* every player-effect group (pitcher, batter, pitcher x pitch type, pitcher x game ...) is an embedding table of ``embedding_dim`` numbers per unit. Index 0 is a fixed zero vector:
  a unit the model has not seen, and every unit when the league-average player is asked for (``effects="population"``), use it. Each table has its own L2 penalty (in
  pseudo-rows, like the other models), so the embeddings are shrunk towards the league average; ``within`` groups get a tiny penalty.
* the descriptors and the location are columns of the input, as in the other models.
* the training is mini-batch Adam with early stopping on held-out games. If the GPU runs out of memory the batch size is halved and the epoch is repeated (it is said in the fit
  summary). The data stays in the computer's memory; only a batch at a time goes to the GPU.

``sequence`` needs the order of the pitches of a plate appearance (``at_bat_number``, ``pitch_number``): the encoder (a GRU or a small Transformer) reads the type, the result and
the count of the previous pitches of the same plate appearance. The decision engine's situation grid has no such history, so this model can be trained, compared and evaluated but
not used as the model behind the decision engine.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import torch
from torch import nn

from .. import gbm
from .. import league_core as lc
from ..config_schema import ConfigError
from . import controls as ctl
from . import design as dz
from . import register
from .base import OutcomeModel


def torch_device(setting: str) -> torch.device:
    cuda = torch.cuda.is_available()
    if setting == "cuda" and not cuda:
        raise ConfigError("device 'cuda' was requested but PyTorch cannot use a GPU here / 要求 cuda 但這裡的 PyTorch 無法使用 GPU")
    return torch.device("cuda" if (setting in ("auto", "cuda") and cuda) else "cpu")


class Net(nn.Module):
    def __init__(self, d_dense: int, n_classes: int, units: dict[str, int], s: dict[str, Any], seq_dim: int = 0, seq_cell: str = "gru"):
        super().__init__()
        e, h = int(s["embedding_dim"]), int(s["hidden_size"])
        self.groups = list(units)
        self.emb = nn.ModuleDict({g: nn.Embedding(n + 1, e, padding_idx=0) for g, n in units.items()})        # row 0 stays the zero vector: the league average
        for table in self.emb.values():
            nn.init.normal_(table.weight, 0.0, 0.01)
            with torch.no_grad():
                table.weight[0].zero_()
        self.seq_cell = seq_cell
        self.seq = None
        enc = 0
        if seq_dim:
            enc = h
            if seq_cell == "gru":
                self.seq = nn.GRU(seq_dim, h, batch_first=True)
            else:
                self.proj = nn.Linear(seq_dim, h)
                self.seq = nn.TransformerEncoder(nn.TransformerEncoderLayer(h, nhead=2 if h % 2 == 0 else 1, dim_feedforward=2 * h, dropout=float(s["dropout"]), batch_first=True), num_layers=1)
        d_in = d_dense + e * len(units) + enc
        layers: list[nn.Module] = []
        for _ in range(int(s["n_layers"])):
            layers += [nn.Linear(d_in, h), nn.ReLU(), nn.Dropout(float(s["dropout"]))]
            d_in = h
        layers.append(nn.Linear(d_in, n_classes))
        self.mlp = nn.Sequential(*layers)

    def forward(self, x, idx, seq=None, mask=None):
        parts = [x] + [self.emb[g](idx[g]) for g in self.groups]
        if self.seq is not None:
            if self.seq_cell == "gru":
                _, hidden = self.seq(seq)                                                    # the last hidden state of the (left-padded) history
                parts.append(hidden[-1])
            else:
                z = self.seq(self.proj(seq), src_key_padding_mask=(mask == 0))
                m = mask.unsqueeze(-1)
                parts.append((z * m).sum(1) / m.sum(1).clamp(min=1.0))
        return self.mlp(torch.cat(parts, dim=1))


class _TorchModel(OutcomeModel):
    uses_design = False
    cost_rule = "epochs"
    probe_settings = {"epochs": 1, "bagging_reps": 1}
    probe_unit = 1
    seq_dim = 0

    # ------------------------------------------------------------------------------------------------ features
    def _dense(self, rows, *, training: bool, effects: str = "specific") -> np.ndarray:
        x, _ = lc.situation_features(rows, with_action=True)
        blocks = [x]
        if self.profile is not None:
            if training:
                d = self._descriptors(rows, history=True); self.desc_std = dz.Standardizer(d)
                blocks.append(self.desc_std(d).astype(np.float32))
            elif effects == "population":
                blocks.append(np.zeros((len(rows["game_pk"]), len(self.desc_std.mean)), dtype=np.float32))
            else:
                blocks.append(self.desc_std(self._descriptors(rows, history=False)).astype(np.float32))
        if self.loc_std is not None:
            blocks.append(self.loc_std(np.column_stack([np.asarray(rows["plate_x"], dtype=float), np.asarray(rows["plate_z"], dtype=float)])).astype(np.float32))
        return np.hstack(blocks) if len(blocks) > 1 else x

    def _indexes(self, rows, effects: str) -> dict[str, np.ndarray]:
        n = len(rows["game_pk"])
        if effects != "specific":
            return {g: np.zeros(n, dtype=np.int64) for g in self.groups}
        return {g: self.indexes[g].lookup(dz.effect_keys(rows, g)) + 1 for g in self.groups}            # unseen -> -1 + 1 = 0: the zero vector

    def _sequence(self, rows):
        return None, None

    def _training_design(self, sub, s):
        _, E, spans = super()._training_design(sub, s)
        self._x_dense = self._dense(sub, training=True)
        self._x_idx = self._indexes(sub, "specific")
        self._x_seq = self._sequence_inputs(sub, len(self.classes) if self.classes else 0)
        return None, E, spans

    def _sequence_inputs(self, rows, n_classes):
        return None

    # ------------------------------------------------------------------------------------------------ training
    def _make_net(self, d_dense: int, s) -> Net:
        units = {g: len(self.indexes[g]) for g in self.groups}
        return Net(d_dense, len(self.classes), units, s, seq_dim=self.seq_dim, seq_cell=s["sequence_cell"])

    def _fit_member(self, sub, y, w, vsub, vy, s, seed, progress, design):
        started = time.monotonic()
        device = torch_device(s["device"])
        torch.manual_seed(int(seed))
        X = torch.from_numpy(np.ascontiguousarray(self._x_dense)); Y = torch.from_numpy(y.astype(np.int64))
        IDX = {g: torch.from_numpy(v) for g, v in self._x_idx.items()}
        SEQ = self._x_seq
        weights = torch.ones(len(y)) if w is None else torch.from_numpy(w.astype(np.float32))
        games = np.asarray(sub["game_pk"])
        if vsub is not None:
            raise ConfigError("an explicit validation set is not used by the neural-network models: they hold out games themselves / 神經網路模型自己留出比賽驗證")
        fit_idx, val_idx = lc.game_split(games, 0.1, seed)
        if len(val_idx) == 0:
            fit_idx, val_idx = np.arange(len(y)), np.arange(min(len(y), 1000))
        net = self._make_net(X.shape[1], s).to(device)
        embeds = [p for g in self.groups for p in self.emb_params(net, g)]
        others = [p for n_, p in net.named_parameters() if not n_.startswith("emb.")]
        opt = torch.optim.Adam([{"params": others, "weight_decay": float(s["weight_decay"])}, {"params": embeds, "weight_decay": 0.0}], lr=float(s["nn_learning_rate"]))
        lam = {g: (ctl.WITHIN_PENALTY if self.groups[g] else float(s[f"effect_penalty_{g}"])) for g in self.groups}
        total_w = float(weights[fit_idx].sum())
        batch = int(s["batch_size"]); notes = []
        best, best_state, bad, history = float("inf"), None, 0, []
        loss_fn = nn.CrossEntropyLoss(reduction="none")

        def batch_inputs(ids):
            b = torch.from_numpy(ids)
            seq, mask = (None, None) if SEQ is None else SEQ(ids)
            return (X[b].to(device), {g: IDX[g][b].to(device) for g in self.groups}, None if seq is None else seq.to(device), None if mask is None else mask.to(device), Y[b].to(device), weights[b].to(device))

        def run_epoch(order, train: bool):
            nonlocal batch
            total, wsum = 0.0, 0.0
            net.train(train)
            pos = 0
            while pos < len(order):
                ids = order[pos:pos + batch]
                try:
                    xb, ib, sb, mb, yb, wb = batch_inputs(ids)
                    with torch.set_grad_enabled(train):
                        logits = net(xb, ib, sb, mb)
                        per_row = loss_fn(logits, yb) * wb
                        loss = per_row.sum() / wb.sum().clamp(min=1e-9)
                        if train:
                            # sum of NLL + 0.5 * penalty * ||effects||^2, divided by the total weight: the same objective as the sparse solver, applied batch by batch
                            pen = sum(0.5 * lam[g] * (net.emb[g].weight ** 2).sum() for g in self.groups) / max(total_w, 1e-9) if self.groups else 0.0
                            opt.zero_grad(); (loss + pen).backward(); opt.step()
                    total += float(per_row.sum().item()); wsum += float(wb.sum().item())
                    pos += len(ids)
                except torch.cuda.OutOfMemoryError:
                    if device.type != "cuda" or batch <= 32:
                        raise
                    torch.cuda.empty_cache(); batch = max(32, batch // 2)                       # the GPU is full: smaller batches, the same epoch again from this point
                    notes.append(f"batch size halved to {batch}")
            return total / max(wsum, 1e-9)

        rng = np.random.default_rng(int(seed))
        for epoch in range(int(s["epochs"])):
            order = rng.permutation(fit_idx)
            train_loss = run_epoch(order, True)
            with torch.no_grad():
                valid_loss = run_epoch(val_idx, False)
            history.append((epoch + 1, train_loss, valid_loss))
            if progress:
                progress("outcome_model", (epoch + 1) / int(s["epochs"]), f"epoch {epoch + 1}/{int(s['epochs'])}")
            if valid_loss < best - 1e-6:
                best, bad = valid_loss, 0
                best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
            else:
                bad += 1
                if bad >= int(s["nn_patience"]):
                    break
        member = {"state": best_state or {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}, "d_dense": int(X.shape[1])}
        info = {"n_iter": float(len(history)), "hit_cap": len(history) >= int(s["epochs"]) and bad < int(s["nn_patience"]), "seconds": time.monotonic() - started, "trace": history,
                "device": str(device), "batch_size_used": batch, "notes": sorted(set(notes))}
        return member, info

    @staticmethod
    def emb_params(net: Net, g: str):
        return list(net.emb[g].parameters())

    # ------------------------------------------------------------------------------------------------ prediction
    def _pack(self, rows, effects):
        pack = super()._pack(rows, effects)
        pack["x"] = self._dense(rows, training=False, effects=effects)
        pack["idx"] = self._indexes(rows, effects)
        pack["seq"] = self._sequence_for(rows)
        return pack

    def _sequence_for(self, rows):
        return None

    def _member_proba(self, member, pack, effects):
        device = torch_device(self.settings["device"])
        net = self._make_net(member["d_dense"], self.settings).to(device)
        net.load_state_dict(member["state"]); net.eval()
        x = torch.from_numpy(np.ascontiguousarray(pack["x"])); idx = {g: torch.from_numpy(v) for g, v in pack["idx"].items()}
        seq = pack["seq"]
        n = pack["n"]; out = np.zeros((n, len(self.classes)))
        step = max(256, int(self.settings["batch_size"]) * 8)
        with torch.no_grad():
            for a in range(0, n, step):
                ids = np.arange(a, min(n, a + step)); b = torch.from_numpy(ids)
                s_, m_ = (None, None) if seq is None else seq(ids)
                logits = net(x[b].to(device), {g: idx[g][b].to(device) for g in self.groups}, None if s_ is None else s_.to(device), None if m_ is None else m_.to(device))
                out[a:a + len(ids)] = torch.softmax(logits, dim=1).cpu().numpy().astype(np.float64)
        return out

    def _summarize(self, infos, s):
        out = super()._summarize(infos, s)
        out["device"] = infos[0].get("device"); out["batch_size_used"] = infos[0].get("batch_size_used")
        out["notes"] = sorted({n for i in infos for n in i.get("notes", [])})
        return out


class NnEmbedModel(_TorchModel):
    name = "nn_embed"
    label_zh = "神經網路＋球員嵌入"
    label_en = "Neural network with player embeddings"
    supports = {name: "嵌入向量（各自的 L2 收縮；沒見過或聯盟平均＝0 向量）" for name in ctl.CONTROL_NAMES}


class SequenceModel(_TorchModel):
    name = "sequence"
    label_zh = "序列模型（打席內前序投球）"
    label_en = "Sequence model (earlier pitches of the plate appearance)"
    supports = {name: "嵌入向量加上前序投球的編碼；不能用在決策引擎的情境格（沒有前序投球）" for name in ctl.CONTROL_NAMES}
    probe_settings = {"epochs": 1, "bagging_reps": 1}

    @property
    def seq_dim(self) -> int:
        return dz.N_TYPES + len(self.classes) + 2

    def _history(self, rows):
        """For every row the indexes (into ``rows``) of the previous pitches of the same plate appearance, oldest first, left-padded with -1."""

        for c in ("at_bat_number", "pitch_number"):
            if c not in rows:
                raise ConfigError("the sequence model needs the order of the pitches of a plate appearance (at_bat_number, pitch_number); the decision engine's situation grid has none / "
                                  "序列模型需要打席內的投球順序（at_bat_number、pitch_number）；決策引擎的情境格沒有，所以不能用在決策引擎")
        L = int(self.settings["sequence_length"])
        n = len(rows["game_pk"])
        game = np.asarray(rows["game_pk"]).astype(np.int64); ab = np.asarray(rows["at_bat_number"]).astype(np.int64); pn = np.asarray(rows["pitch_number"]).astype(np.int64)
        order = np.lexsort((pn, ab, game))
        g, a = game[order], ab[order]
        prev = np.full((n, L), -1, dtype=np.int64)
        for k in range(1, L + 1):
            ok = np.zeros(n, dtype=bool)
            ok[k:] = (g[k:] == g[:-k]) & (a[k:] == a[:-k])
            src = np.full(n, -1, dtype=np.int64)
            src[k:] = order[:-k]
            col = L - k                                                                        # k = 1 is the pitch just before: the last column
            prev[order[ok], col] = src[ok]
        return prev

    def _pitch_features(self, rows) -> np.ndarray:
        """What a later pitch of the plate appearance can know about this pitch: its type, its result and the count it was thrown at."""

        n = len(rows["game_pk"]); K = len(self.classes)
        feat = np.zeros((n, self.seq_dim), dtype=np.float32)
        typ = np.maximum(dz.type_index(rows["pitch_type"]), 0)
        feat[np.arange(n), typ] = 1.0
        lookup = {c: i for i, c in enumerate(self.classes)}
        merge = self.settings["class_merge"]
        res = np.array([lookup.get(merge.get(str(o), ""), -1) for o in np.asarray(rows["outcome"])], dtype=np.int64) if "outcome" in rows else np.full(n, -1)
        ok = res >= 0
        feat[np.nonzero(ok)[0], dz.N_TYPES + res[ok]] = 1.0
        feat[:, -2] = np.asarray(rows["balls"], dtype=np.float32) / 3.0
        feat[:, -1] = np.asarray(rows["strikes"], dtype=np.float32) / 2.0
        return feat

    def _make_sequence(self, rows):
        prev = self._history(rows)
        feat = torch.from_numpy(self._pitch_features(rows))
        prev_t = torch.from_numpy(prev)

        def get(ids):
            p = prev_t[torch.from_numpy(ids)]
            mask = (p >= 0).float()
            seq = feat[p.clamp(min=0)] * mask.unsqueeze(-1)
            mask[:, -1] = 1.0                                   # a first pitch has no history: keep one zero step so that the attention never sees an all-masked row
            return seq, mask
        return get

    def _sequence_inputs(self, rows, n_classes):
        return self._make_sequence(rows)

    def _sequence_for(self, rows):
        return self._make_sequence(rows)

    def _pack(self, rows, effects):
        if "at_bat_number" not in rows:
            raise ConfigError("the sequence model cannot answer for situations without the earlier pitches of the plate appearance, such as the decision engine's grid / "
                              "序列模型無法回答沒有前序投球的情境（例如決策引擎的情境格）")
        return super()._pack(rows, effects)


register(NnEmbedModel)
register(SequenceModel)
