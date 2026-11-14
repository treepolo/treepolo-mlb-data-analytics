"""The settings every outcome model shares: the bias controls (what to remove from the model so it does not learn a confounded picture) and the hyper-parameters.

A control says which part of the data a model must NOT be allowed to blame on the pitch type or the situation: a pitcher who is simply good, a pitcher whose slider is
simply strong, a batter who simply chases, a pitcher having a good day. All models use the same names; a model that cannot do something says so and the setting is refused
with a clear message instead of being ignored.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..config_schema import ConfigError, ConfigField

# (name, choices, default, label_zh, label_en, help_zh, help_en)
_CONTROL_SPECS = (
    ("control_pitcher", ("none", "descriptors", "effect", "within"), "none", "控制：投手整體好壞", "Control: pitcher quality",
     "none：不控制；descriptors：用投手描述特徵（歷史模式）；effect：每位投手一個效應（資料少的往聯盟平均收縮）；within：只在同一位投手自己的球之間比較（效應不收縮）。",
     "none; descriptors (history-mode descriptors); effect (one shrunken effect per pitcher); within (compare only inside the same pitcher, effects not shrunk)."),
    ("control_pitcher_pitch", ("none", "effect", "within"), "none", "控制：投手的每一種球各自的好壞", "Control: each pitcher's quality on each pitch type",
     "擋「敢連投的人本來那顆球就強」：每位投手的每一種球各一個效應，資料少的組合往該投手整體、再往聯盟平均收縮；within＝只在同一投手同一球種之間比較（連投效應就是這樣估的）。",
     "Each pitcher-and-pitch-type pair gets its own effect, shrunk towards the pitcher's overall effect and then the league."),
    ("control_batter_pitch", ("none", "effect"), "none", "控制：打者對每一種球的反應", "Control: each batter's response to each pitch type",
     "每位打者對每一種球各一個效應（他是不是特別怕滑球）。", "Each batter-and-pitch-type pair gets its own effect."),
    ("control_day", ("none", "pitcher_game_effect"), "none", "控制：投手當天狀態", "Control: the pitcher's form of the day",
     "每位投手每場比賽一個效應；預測時用平常狀態（效應 0）。", "One effect per pitcher and game; predictions use the usual form (effect 0)."),
    ("control_batter", ("none", "descriptors", "effect"), "none", "控制：打者整體好壞", "Control: batter quality",
     "同投手：none／descriptors／effect。", "As for the pitcher: none, descriptors or effect."),
    ("control_survival", ("none", "batter_and_count"), "none", "控制：存活偏誤（球數＋打者）", "Control: survivorship (count and batter)",
     "讓比較發生在同一位打者、同一個球數之間；需要 control_batter＝effect。", "Compares inside the same batter and count; needs control_batter = effect."),
    ("control_location", ("exclude", "include_and_average"), "exclude", "控制：球的位置", "Control: pitch location",
     "exclude：不放進位置；include_and_average：訓練時放入 plate_x、plate_z，預測時對該球種在該球數的歷史位置分布抽樣平均。",
     "exclude, or train with plate_x / plate_z and average the prediction over the historical locations of that pitch type and count."),
    ("control_day_pitch", ("none", "effect"), "none", "控制：投手當天某一種球的狀態", "Control: the pitcher's form of the day on one pitch type",
     "投手×比賽×球種一個效應。資料很少，只能強收縮，效果有限。", "One effect per pitcher, game and pitch type. Very thin data: strongly shrunk, limited effect."),
)

CONTROL_NAMES = tuple(spec[0] for spec in _CONTROL_SPECS)
CONTROL_DEFAULTS = {spec[0]: spec[2] for spec in _CONTROL_SPECS}

# The penalty of an effect group is the PRECISION (1 / variance) of the normal prior of its effects, in units of the logit of the outcome: larger = stronger shrinkage towards 0.
# It is NOT a number of pseudo-pitches. One pitch carries little information about an effect (about p * (1 - p) of its logit, a tenth or less for an outcome that happens one pitch in
# five), so a penalty of 100 ignores effects that hundreds of pitches could show. With ``effect_penalty_mode = auto`` the data choose it (empirical Bayes, as in the mixed model).
EFFECT_GROUPS = ("pitcher", "batter", "pitcher_pitch", "batter_pitch", "pitcher_game", "pitcher_game_pitch", "batter_count")
_PENALTY_DEFAULTS = {"pitcher": 20.0, "batter": 20.0, "pitcher_pitch": 50.0, "batter_pitch": 50.0, "pitcher_game": 100.0, "pitcher_game_pitch": 200.0, "batter_count": 50.0}
_PENALTY_LABELS = {"pitcher": ("投手", "pitcher"), "batter": ("打者", "batter"), "pitcher_pitch": ("投手×球種", "pitcher x pitch type"), "batter_pitch": ("打者×球種", "batter x pitch type"),
                   "pitcher_game": ("投手×比賽", "pitcher x game"), "pitcher_game_pitch": ("投手×比賽×球種", "pitcher x game x pitch type"), "batter_count": ("打者×球數", "batter x count")}
WITHIN_PENALTY = 1e-4          # "within" = effects that are not shrunk: a very small penalty only keeps the problem well posed

CONTROL_FIELDS: tuple[ConfigField, ...] = tuple(
    ConfigField(name, "choice", default=default, choices=choices, label_zh=zh, label_en=en, help_zh=hzh, help_en=hen) for name, choices, default, zh, en, hzh, hen in _CONTROL_SPECS
) + (
    ConfigField("effect_penalty_mode", "choice", default="auto", choices=("auto", "manual"), label_zh="效應的收縮強度：自動或手動", label_en="Effect shrinkage: automatic or manual",
                help_zh="auto（預設）：由資料估計每一組效應的收縮強度（經驗貝氏，和混合效應模型同一個估法）；manual：用下面各組填的數值。",
                help_en="auto (default): the data choose the shrinkage of every effect group (empirical Bayes, as in the mixed model); manual: use the values below."),
) + tuple(
    ConfigField(f"effect_penalty_{g}", "float", default=_PENALTY_DEFAULTS[g], minimum=0.0, label_zh=f"收縮強度（手動）：{_PENALTY_LABELS[g][0]}效應", label_en=f"Shrinkage (manual): {_PENALTY_LABELS[g][1]} effect",
                help_zh="只在「手動」時使用。這是效應先驗的精確度（變異數的倒數，單位是結果的對數機率），不是假想球數：一球對效應的資訊量約只有 p(1−p)，所以 100 就是很強的收縮。愈大愈往 0（聯盟平均）收縮。",
                help_en="Used only in manual mode. The precision (1 / variance) of the prior of the effects, in logits: not a number of pseudo-pitches. One pitch carries about p(1-p) of information, so 100 is strong shrinkage.") for g in EFFECT_GROUPS
) + (
    ConfigField("location_samples", "int", default=50, minimum=1, maximum=1000, label_zh="位置抽樣數", label_en="Location samples", help_zh="control_location＝include_and_average 時，每個預測抽幾個歷史位置平均。",
                help_en="Historical locations averaged per prediction when control_location = include_and_average."),
)

# hyper-parameters that more than one model uses (each model reads the ones it needs and ignores the rest; the full list is shown in the full settings mode)
PARAM_FIELDS: tuple[ConfigField, ...] = (
    ConfigField("backend", "choice", default="xgboost", choices=("xgboost", "hgb"), label_zh="梯度提升樹後端", label_en="Boosted-tree backend", help_zh="只有 gbm 用；hgb（scikit-learn）不支援偏移量，所以不能搭配球員效應。",
                help_en="Only gbm uses it; hgb has no base margin, so it cannot be combined with player effects."),
    ConfigField("device", "choice", default="auto", choices=("auto", "cpu", "cuda"), label_zh="裝置", label_en="Device", help_zh="auto：有 GPU 就用。", help_en="auto uses a GPU when present."),
    ConfigField("max_depth", "int", default=6, minimum=1, maximum=30, label_zh="樹深", label_en="Tree depth"),
    ConfigField("min_leaf", "int", default=200, minimum=1, label_zh="葉節點最小樣本", label_en="Minimum samples per leaf"),
    ConfigField("learning_rate", "float", default=0.1, minimum=1e-4, maximum=1.0, label_zh="學習率", label_en="Learning rate"),
    ConfigField("l2", "float", default=1.0, minimum=0.0, label_zh="L2 正則化（一般欄位）", label_en="L2 regularisation (ordinary columns)"),
    ConfigField("max_iter", "int", default=1000, minimum=1, label_zh="最多棵數／迭代數", label_en="Maximum rounds / iterations",
                help_zh="邏輯迴歸與混合效應是最佳化的迭代數，樹是棵數。撞到上限會在結果的 hit_cap 欄位標出來。", help_en="Optimiser iterations (logistic, mixed) or trees (boosting). Reaching it is flagged in the hit_cap column."),
    ConfigField("early_stopping_rounds", "int", default=20, minimum=1, label_zh="早停輪數（xgboost）", label_en="Early-stopping rounds (xgboost)"),
    ConfigField("backfit_rounds", "int", default=1, minimum=1, maximum=10, label_zh="回頭重估效應的輪數", label_en="Back-fitting rounds",
                help_zh="gbm 搭配球員效應時：>1 會用樹的輸出當偏移，回頭重估效應，再重訓練樹。", help_en="gbm with player effects: with more than 1, the effects are re-estimated given the trees."),
    ConfigField("bagging_reps", "int", default=1, minimum=1, maximum=200, label_zh="袋裝成員數（比賽層級 Poisson 重抽樣）", label_en="Bagging members (game-level Poisson bootstrap)"),
    # the settings of the models that learn with PyTorch (nn_embed, sequence) and of the mixed-effects model
    ConfigField("nn_learning_rate", "float", default=0.003, minimum=1e-6, maximum=1.0, label_zh="神經網路學習率", label_en="Neural-network learning rate"),
    ConfigField("batch_size", "int", default=4096, minimum=16, label_zh="批次大小（顯存不足會自動減半）", label_en="Batch size (halved automatically when the GPU memory runs out)"),
    ConfigField("epochs", "int", default=10, minimum=1, label_zh="輪數（神經網路）", label_en="Epochs (neural networks)"),
    ConfigField("hidden_size", "int", default=64, minimum=2, label_zh="隱藏層大小", label_en="Hidden layer size"),
    ConfigField("n_layers", "int", default=2, minimum=1, maximum=8, label_zh="全連接層數", label_en="Fully connected layers"),
    ConfigField("embedding_dim", "int", default=8, minimum=1, label_zh="嵌入維度", label_en="Embedding dimension"),
    ConfigField("dropout", "float", default=0.1, minimum=0.0, maximum=0.9, label_zh="丟棄率", label_en="Dropout"),
    ConfigField("weight_decay", "float", default=1e-5, minimum=0.0, label_zh="權重衰減（一般權重）", label_en="Weight decay (ordinary weights)"),
    ConfigField("nn_patience", "int", default=3, minimum=1, label_zh="早停：幾輪沒進步就停", label_en="Early stopping: epochs without improvement"),
    ConfigField("em_rounds", "int", default=8, minimum=1, maximum=50, label_zh="收縮強度自動估計的輪數（混合效應、邏輯迴歸 auto）", label_en="Rounds of the automatic shrinkage estimate (mixed, logistic auto)"),
    ConfigField("sequence_cell", "choice", default="gru", choices=("gru", "transformer"), label_zh="序列模型的編碼器", label_en="Sequence encoder"),
    ConfigField("sequence_length", "int", default=6, minimum=1, maximum=30, label_zh="序列模型看前面幾球", label_en="Previous pitches the sequence model looks at"),
)

MODEL_KINDS = ("logistic", "gbm", "mixed", "nn_embed", "sequence")


def default_controls() -> dict[str, Any]:
    return dict(CONTROL_DEFAULTS)


def penalties_from(settings: Mapping[str, Any]) -> dict[str, float]:
    return {g: float(settings[f"effect_penalty_{g}"]) for g in EFFECT_GROUPS}


def effect_groups(controls: Mapping[str, Any]) -> dict[str, bool]:
    """The effect groups a set of controls needs, mapped to whether they are 'within' (not shrunk). Hierarchy: a pitcher-and-pitch effect is shrunk towards the pitcher's
    overall effect, so the overall effect is included whenever the finer one is."""

    groups: dict[str, bool] = {}

    def add(name: str, within: bool = False) -> None:
        groups[name] = groups.get(name, False) or within

    if controls.get("control_pitcher") in ("effect", "within"):
        add("pitcher", controls["control_pitcher"] == "within")
    if controls.get("control_pitcher_pitch", "none") != "none":
        add("pitcher"); add("pitcher_pitch", controls["control_pitcher_pitch"] == "within")
    if controls.get("control_batter") == "effect":
        add("batter")
    if controls.get("control_batter_pitch", "none") == "effect":
        add("batter"); add("batter_pitch")
    if controls.get("control_day", "none") == "pitcher_game_effect":
        add("pitcher"); add("pitcher_game")
    if controls.get("control_day_pitch", "none") == "effect":
        add("pitcher"); add("pitcher_game"); add("pitcher_game_pitch")
    if controls.get("control_survival", "none") == "batter_and_count":
        add("batter"); add("batter_count")
    return groups


def validate_controls(controls: Mapping[str, Any], *, model: str, backend: str = "xgboost") -> None:
    """Refuse combinations that make no sense or that the model cannot do; the message says why and what to change."""

    if controls.get("control_survival") == "batter_and_count" and controls.get("control_batter") != "effect":
        raise ConfigError("control_survival = batter_and_count needs control_batter = effect (the comparison is made inside the same batter) / "
                          "control_survival＝batter_and_count 需要 control_batter＝effect（在同一位打者之內比較）")
    if model == "gbm" and backend == "hgb":
        needs = [name for name in ("control_pitcher", "control_pitcher_pitch", "control_batter_pitch", "control_day", "control_batter", "control_survival", "control_day_pitch")
                 if controls.get(name) in ("effect", "within", "pitcher_game_effect", "batter_and_count")]
        if needs:
            raise ConfigError(f"backend hgb (scikit-learn) has no base margin, so it cannot do {needs}; use backend xgboost or model logistic / "
                              f"backend hgb（scikit-learn）沒有偏移量，無法做 {needs}；請改用 backend xgboost 或 model logistic")


def describe_controls(controls: Mapping[str, Any], *, model: str, backend: str) -> list[dict[str, Any]]:
    """What each control actually does in this model (shown in the fit summary)."""

    how = {
        "none": "不控制", "descriptors": "加入描述特徵（歷史模式：只用更早的比賽）", "effect": "效應（向聯盟平均收縮）", "within": "同組內比較（效應不收縮）",
        "pitcher_game_effect": "每位投手每場一個效應；預測用平常狀態（0）", "batter_and_count": "打者×球數效應，需要打者效應", "exclude": "不放位置",
        "include_and_average": "訓練時放位置；預測時對歷史位置抽樣平均",
    }
    via = "樹用偏移量（base_margin）吸收效應，樹學剩下的部分" if model == "gbm" else "效應直接放進多類別邏輯迴歸（稀疏欄位，各自的懲罰）"
    rows = []
    for name in CONTROL_NAMES:
        value = controls.get(name, CONTROL_DEFAULTS[name])
        rows.append({"control": name, "setting": value, "how": how.get(value, value) + (f"；{via}" if value in ("effect", "within", "pitcher_game_effect", "batter_and_count") else ""),
                     "model": model, "backend": backend if model == "gbm" else ""})
    return rows
