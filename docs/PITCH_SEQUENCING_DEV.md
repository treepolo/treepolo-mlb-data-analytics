# 配球序列研究系統 — 開發文件

**狀態：P1、P2 已寫到可照做的程度；P3–P6 只有概要，到該階段前再補寫。** 日期：2026-10-06。

本文件是**施工說明**。「做什麼、為什麼」以 [`PITCH_SEQUENCING_PLAN.md`](PITCH_SEQUENCING_PLAN.md)（下稱規劃文件）為準；本文件只寫「怎麼做」。兩份不一致、或規劃文件沒涵蓋的情況，**停下來問使用者**，不要自行決定。

目錄：0 使用規則 · 1 P1 研究紀錄與設定化 · 2 P2 資料前處理 · 3 P3–P6 概要與文件撰寫時機 · 附錄 A 已驗證的參考實作 · 附錄 B 測試用假資料與預期答案 · 附錄 C 範例 JSON · 附錄 D 現有程式索引

---

## 0. 使用規則

### 0.1 工作方式

- 分支：`claude/hello-rc9nc6`。不要開新分支、不要推到別的分支。
- 每完成一個任務（T1.1、T1.2…）：跑測試 → 通過後 commit → push。push 失敗（網路）時依序等 2、4、8、16 秒重試，最多 4 次。
- **不要開 PR**，除非使用者明說。
- commit 訊息結尾加上該次 session 指示的署名行。
- 一次只做一個任務。做完才開始下一個。
- 寫完程式一定要連同測試一起 commit；沒有測試的功能不算完成。
- 遇到第 0.5 節列出的情況就停下來問。

### 0.2 環境與指令

- 不要 `pip install -e .`（會在 `src/` 產生未被 `.gitignore` 忽略的 `egg-info`）。用虛擬環境加 `PYTHONPATH=src`：

```bash
python3 -m venv .venv            # .venv/ 已在 .gitignore
.venv/bin/pip install 'requests>=2.31,<3' 'duckdb>=1.4,<2' 'numpy>=2,<3' 'scipy>=1.14,<2' 'scikit-learn>=1.6,<2' 'reportlab>=4.2,<5' 'pytest>=8,<10'
PYTHONPATH=src .venv/bin/pytest -q -m 'not integration'
```

- **基準（2026-10-06 實測）：`226 passed, 2 deselected`，約 20 秒。** 動手前先跑一次確認基準；每個任務結束後都不得低於這個通過數。
- Node 22 可用，新增的前端檔案要跑 `node --check <檔案>`，並加進 `.github/workflows/ci.yml`（照現有 `node --check` 行的寫法）。
- 執行 CLI：`PYTHONPATH=src .venv/bin/python -c "import sys; from treepolo_mlb_data.cli import main; sys.exit(main(sys.argv[1:]))" <參數>`（沒有安裝套件，所以不能用 `treepolo-mlb` 指令；文件中的 `treepolo-mlb ...` 都用這個寫法取代）。
- `data/`、`*.sqlite3`、`*.sqlite3-wal`、`*.sqlite3-shm` 已在 `.gitignore`。**絕不 commit 資料檔。**

### 0.3 程式慣例（照現有程式碼）

- 每個模組第一行 `from __future__ import annotations`。
- 資料類別用 `@dataclass(frozen=True, slots=True)`。
- 時間戳：`datetime.now(timezone.utc).isoformat()`。
- 正規化 JSON：`from treepolo_mlb_data.analysis_state import canonical_json`（`sort_keys=True, separators=(",", ":"), ensure_ascii=False`）。**所有雜湊都對 `canonical_json` 的 UTF-8 位元組做 sha256。**
- SQLite 連線：`check_same_thread=False`、`threading.RLock`、`PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`（照 `AnalysisStateStore`）。
- 錯誤：設定錯誤用 `ConfigError`（`ValueError` 子類）；網頁層 `ValueError` 會自動回 HTTP 400。
- 使用者看得到的文字一律「中文 English」雙語。
- **不得默默截斷**結果；有上限就明確報錯或在結果中標示。
- 分析引擎的限制要記住：`Window` 的 `lag` 參數可給 1–3 個（運算式、位移、預設值）；`Binary` 的運算子白名單在 `analysis/compiler.py` 的 `_BINARY_OPS`；欄位名稱開頭為 `__ta_` 的欄位會在結果輸出時自動隱藏。

### 0.4 已驗證的事實（不用再查）

| 事實 | 驗證方式 |
|---|---|
| 用多層 `Window` 可算「同球種連續第幾顆」，SQLite 與 DuckDB 結果完全一致 | 2026-10-06 以假資料在兩個後端執行並比對（附錄 A、B） |
| `AnalysisEngine(backend="duckdb")` 在鏡像建立失敗時會**靜默退回 SQLite** | 實測；所以 DuckDB 測試必須斷言 `result.backend == "duckdb"` |
| DuckDB 鏡像要求測試資料庫的 `pitches` 表有 `_ingested_at` 欄位 | 實測（缺少時鏡像建立失敗並退回 SQLite） |
| `pitch_number` 在每個打席內是 1..n 連續編號，**自動好壞球也佔一個編號** | 2025-06-14 與 2026-08-15 兩天樣本，2,254 個打席 |
| `pitch_type` 為空的列，在兩個樣本日全部都是 `automatic_ball` / `automatic_strike` | 同上（32 與 22 列） |
| `des`（打席描述）只出現在打席最後一球 | 樣本觀察；短打判斷靠 `des` 時以打席為單位 |
| 同步請求 `hfGT = R\|PO\|S`：資料庫含**例行賽、季後賽、春訓** | `savant.py` 的 `params()` |
| 5 天回補（19,847 球）約 35 秒、23 MB | 2026-10-06 實測 |
| 2026 年每位打者的 `sz_top`、`sz_bot` 為固定值；2025 逐球變動 | 兩個樣本日 |
| `_ingested_at` 在新增與更新的列都會被設為當下時間；內容不變的列不動 | `storage.py` 的 `ingest_csv` |

### 0.5 通用「停下來問使用者」清單

1. 規劃文件沒寫、而你得做選擇的任何事（尤其是統計定義、類別分法、排除規則）。
2. 要修改**既有**的資料表結構、既有 API 的回傳格式、既有分析模式的行為。
3. 既有測試因你的修改而失敗，而你不確定是測試過時還是你的錯。
4. 資料量或耗時遠超本文件的估計（例如下載超過 3 小時、硬碟可用空間低於 8 GB）。
5. 需要新增 Python 套件依賴。
6. 任何會把資料或結果送出本機/本容器的動作（除了 push 到指定分支）。

---

## 1. P1：研究紀錄與設定化基礎建設

### 1.1 目標

- 所有「研究方法」都以**明確設定**執行：假設不得藏在程式裡，每個假設是設定欄位，且有預設值。
- 每次執行**自動**保存「設定、結果、資料範圍、程式版本」，同樣設定且資料沒變就**直接讀取，不重算**。
- 研究紀錄可歸入「研究專案」，可打包成**研究檔**匯出，在另一台機器匯入後直接顯示。
- 提供 CLI（雲端研究時使用）、HTTP API、一個基本的網頁檢視頁。

**非目標（不要做）**：具體的研究方法（那是 P3 之後）、研究方法的設定表單 UI、圖表、取代或修改既有的分析歷史／已存分析／結果快取（它們原樣保留）。

### 1.2 已定案的設計決定

| 決定 | 內容 | 理由 |
|---|---|---|
| 獨立資料庫 | 新檔 `data/research_runs.sqlite3`，大型結果存在 `data/research_runs/blobs/` | 不受 `result_cache` 的 200 筆 LRU 與 8 MB 上限影響；匯出入簡單；不與 Stage 4D 的猴子補丁耦合 |
| 永不自動刪除 | 沒有任何淘汰機制；刪除只能手動 | 使用者要求「跑過的研究都記下來」 |
| 執行紀錄全域唯一 | `research_runs.run_key` 唯一；研究專案與執行紀錄為多對多（`study_runs`） | 同設定不重算，不同研究專案可共用同一次執行 |
| 重用條件 | `run_key = sha256(canonical_json({format, kind, method_version, config, scope_fingerprint}))` | 設定、方法版本、**研究範圍內的資料**任一改變就視為不同研究 |
| 資料指紋看範圍 | 用 `game_year`／`game_type`／日期範圍內的「筆數、日期範圍、最大 `_ingested_at`」，**不用**全域 `data_revision` | 每日更新只會動當季資料；已完賽球季的研究不應因此失效 |
| 同步執行 | `POST /api/research/run` 在請求執行緒內跑完才回應；進度沿用 `analysis_jobs` | 與 `/api/analyze` 一致；雲端用 CLI |
| 預設只用例行賽 | 範圍的 `game_types` 預設 `["R"]` | 資料庫含春訓與季後賽，春訓不具代表性；可由設定改 |
| 結果格式 | `{"version":"research-result-v1","kind":…,"sections":[…],"extras":{…}}`，`sections` 的每一節與既有分析結果一致（`title, columns, rows, grain{keys,label}, row_count, backend`） | 可重用既有的結果顯示與匯出邏輯 |

> 春訓與季後賽預設排除（範圍 `game_types` 預設 `["R"]`）已由使用者確認（2026-10-06）。

### 1.3 檔案清單

**新增**

```
src/treepolo_mlb_data/research/__init__.py
src/treepolo_mlb_data/research/config_schema.py   # ConfigField, ConfigError, normalize_config
src/treepolo_mlb_data/research/scope.py           # normalize_scope, scope_filter_expr, compute_scope_fingerprint
src/treepolo_mlb_data/research/store.py           # ResearchStore（SQLite + blob）
src/treepolo_mlb_data/research/methods.py         # ResearchMethod, ResearchResult, ResearchContext, registry
src/treepolo_mlb_data/research/builtin_methods.py # analysis_payload（P1）；data_profile（P2 加）
src/treepolo_mlb_data/research/service.py         # ResearchService
src/treepolo_mlb_data/research/bundle.py          # export_bundle / import_bundle
src/treepolo_mlb_data/research/api.py             # HTTP 路由處理函式
src/treepolo_mlb_data/research/cli.py             # `research` 子指令
src/treepolo_mlb_data/web_static/research-runs-page.js
tests/research_fixtures.py                        # 測試用資料庫建構函式
tests/test_research_config_schema.py
tests/test_research_scope.py
tests/test_research_store.py
tests/test_research_service.py
tests/test_research_bundle.py
tests/test_research_api.py
tests/test_research_cli.py
tests/test_research_ui_wiring.py
```

**修改**

```
src/treepolo_mlb_data/config.py        # 兩個新欄位 + 兩個屬性
src/treepolo_mlb_data/webapp.py        # AppServices.research、路由、二進位回應、serve() 收尾
src/treepolo_mlb_data/cli.py           # 掛上 research 子指令
src/treepolo_mlb_data/web_static/index.html   # 加一行 <script src="/research-runs-page.js">
.github/workflows/ci.yml               # 新增 node --check
docs/PITCH_SEQUENCING_PLAN.md          # 完成時在第 10 節 P1 列註記完成日期
```

`pyproject.toml` 不用改（`packages.find where=["src"]` 會自動收進有 `__init__.py` 的新套件）。

### 1.4 任務

#### T1.1 設定欄位

`AppConfig` 新增（放在 `analysis_state_database_name` 之後）：

```python
research_state_database_name: str = "research_runs.sqlite3"
research_blob_dir_name: str = "research_runs"
```

新增屬性 `research_state_database_path`（`root / research_state_database_name`）與 `research_blob_dir`（`root / research_blob_dir_name`）。

測試（加入 `tests/test_config.py` 或新檔 `tests/test_config_research.py`）：
- 預設值與路徑；
- 不含新欄位的舊 `config.json` 仍可 `load_config`；
- 未知欄位仍被拒絕。

#### T1.2 設定正規化 `config_schema.py`

```python
class ConfigError(ValueError): ...

@dataclass(frozen=True, slots=True)
class ConfigField:
    name: str
    type: Literal["int", "float", "bool", "str", "choice", "str_list", "int_list", "json"]
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unique_sorted: bool = False      # str_list / int_list：去重並排序（順序無意義的集合用）
    label_zh: str = ""; label_en: str = ""
    help_zh: str = ""; help_en: str = ""
    def to_dict(self) -> dict[str, Any]: ...   # 全部欄位，choices 轉 list

def normalize_config(fields: Sequence[ConfigField], raw: Mapping[str, Any]) -> dict[str, Any]: ...
```

規則（逐條都要有測試）：

1. `raw` 不是 `dict` → `ConfigError`。
2. 出現未宣告的鍵 → `ConfigError`，訊息列出所有未知鍵。**不得靜默忽略。**
3. `required=True` 而缺少 → `ConfigError`。
4. 缺少的選填欄位填入 `default`（`copy.deepcopy`）。
5. 型別：
   - `int`：接受 `int`，或 `float` 且 `.is_integer()`；**拒絕 `bool`**；拒絕字串。
   - `float`：接受 `int`/`float`（非 `bool`），轉成 `float`；拒絕 `nan`、`inf`。
   - `bool`：只接受 `bool`。
   - `str`：只接受 `str`。
   - `choice`：`str` 且在 `choices` 內。
   - `str_list`／`int_list`：接受 `list` 或 `tuple`，每個元素符合型別，輸出為 `list`；`unique_sorted=True` 時去重並排序。
   - `json`：任何可被 `json.dumps(..., allow_nan=False)` 序列化的值。
6. `minimum`／`maximum` 對 `int`/`float` 生效，超出範圍 → `ConfigError`。
7. **輸出一定包含每個已宣告的欄位**（含全部預設值），鍵的順序不重要。這保證儲存的設定是「完整、明確的假設清單」。
8. 錯誤訊息含欄位名稱，並盡量雙語。

#### T1.3 研究範圍 `scope.py`

範圍是每個「需要資料範圍」的研究方法的必要設定（`requires_scope=True`）。

```python
def normalize_scope(raw: Mapping[str, Any] | None, *, required: bool) -> dict[str, Any]: ...
def scope_filter_expr(scope: Mapping[str, Any]):                 # 回傳 analysis.model 的 Expr；scope 為空時回傳 None
def compute_scope_fingerprint(database_path: Path, scope: Mapping[str, Any]) -> dict[str, Any]: ...
```

**範圍格式**（正規化後）：

```json
{"game_years": [2023, 2024], "game_types": ["R"]}
{"date_from": "2023-03-30", "date_to": "2024-10-01", "game_types": ["R"]}
```

- 兩種形式擇一：`game_years`（非空 `int` 列表，去重排序，每個介於 2015 與 2100）**或**同時給 `date_from`、`date_to`（`YYYY-MM-DD`，`date_from <= date_to`，用 `date.fromisoformat` 驗證）。兩種同時給或都不給 → `ConfigError`。
- `game_types`：非空 `str` 列表，去重排序，每個為 1–3 個大寫英文字母；缺少時預設 `["R"]`。
- `required=False` 且 `raw is None` → 回傳 `{}`。`required=True` 且缺少 → `ConfigError`。
- 其他鍵 → `ConfigError`。

**`scope_filter_expr`**：回傳 `Boolean("and", (...))`，包含：
- `game_years` 形式：`InList(Column("game_year"), (Literal(y), ...))`；
- 日期形式：`Binary(Column("game_date"), ">=", Literal(date_from))` 與 `"<="` `date_to`（`game_date` 是 `YYYY-MM-DD` 文字，字串比較正確）；
- `InList(Column("game_type"), (Literal(t), ...))`。

**`compute_scope_fingerprint`**（直接用 `sqlite3` 查 `database_path`，不經分析引擎）：

```sql
SELECT game_year, game_type, COUNT(*), MIN(game_date), MAX(game_date), MAX(_ingested_at)
FROM pitches WHERE <範圍條件，用 ? 參數>
GROUP BY game_year, game_type ORDER BY game_year, game_type
```

- 缺少 `game_year`、`game_type`、`game_date`、`_ingested_at` 任一欄位 → `ConfigError`（用 `PRAGMA table_info(pitches)` 檢查）。
- 回傳：

```python
{
  "data_revision": read_data_revision(database_path),   # 僅供參考，不進 run_key
  "seasons": [{"game_year": 2023, "game_type": "R", "rows": 123, "min_date": "...", "max_date": "...", "max_ingested_at": "..."}],
  "total_rows": 456,
  "scope_fingerprint": sha256(canonical_json(seasons)).hexdigest(),
}
```

- `scope` 為空 `{}`：回傳 `{"data_revision": rev, "seasons": [], "total_rows": None, "scope_fingerprint": "revision:" + rev}`。
- 快取：模組層級 `dict`，鍵為 `(解析後的路徑字串, data_revision, canonical_json(scope))`，用 `threading.Lock` 保護。資料更新（`data_revision` 改變）時自然失效。
- `read_data_revision` 從 `treepolo_mlb_data.analysis_state` 匯入。

測試：
- 範圍正規化的全部錯誤分支與排序去重；
- 以測試資料庫驗證 `scope_filter_expr` 產生的 SQL 結果（用 `AnalysisEngine` 執行 `Filter(Source("pitches", PITCH_GRAIN), expr)` 後比對 `pitch_uid`）；
- 指紋：重複呼叫相同；新增一列、或更新一列的 `_ingested_at` 後改變；改動「範圍外」的列不改變；
- 缺少欄位時報錯。

`tests/research_fixtures.py` 提供 `make_research_db(path, rows=None)`：建立含 `settings(data_revision)` 與 `pitches` 的 SQLite（`pitches` 欄位至少：`pitch_uid TEXT PRIMARY KEY, game_pk, at_bat_number, pitch_number, game_year, game_type, game_date, pitcher, batter, pitch_type, description, events, des, release_speed, plate_x, plate_z, p_throws, stand, _ingested_at`），預設塞入跨 2023、2024、2025 年且含 `R` 與 `S` 的約 12 列。**DuckDB 相關測試要求有 `_ingested_at` 與 `settings` 表。**

#### T1.4 儲存 `store.py`

`ResearchStore(path: Path, blob_dir: Path)`。建構時建立資料夾、連線、建表、寫入 `schema_info('format_version','research-v1')`；若已存在且版本不同 → `RuntimeError`。啟動時呼叫 `recover_interrupted()`：把 `status='running'` 的列改為 `failed`、`error='interrupted (process ended before completion)'`。

**DDL（逐字使用）：**

```sql
CREATE TABLE IF NOT EXISTS schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    insight_markdown TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_studies_origin_name ON studies(origin, name);

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    method_version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    data_fingerprint_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    result_hash TEXT,
    result_bytes INTEGER,
    duration_seconds REAL,
    code_version TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'local',
    imported_bundle_uid TEXT,
    parent_run_id INTEGER REFERENCES research_runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_kind_created ON research_runs(kind, created_at DESC);

CREATE TABLE IF NOT EXISTS study_runs (
    study_id INTEGER NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY (study_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    columns_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    blob_hash TEXT NOT NULL,
    blob_bytes INTEGER NOT NULL,
    UNIQUE (run_id, name)
);

CREATE TABLE IF NOT EXISTS blobs (
    blob_hash TEXT PRIMARY KEY,
    relpath TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
```

連線後執行 `PRAGMA foreign_keys=ON`。

**Blob 儲存**：
- `write_json_blob(obj) -> (hash, raw_bytes)`：`raw = canonical_json(obj).encode("utf-8")`，`hash = sha256(raw)`；路徑 `blob_dir/blobs/<hash[:2]>/<hash>.json.gz`；已存在就不重寫；寫入先寫暫存檔（`.tmp` + `uuid`）再 `replace`；`gzip.open(..., "wt", encoding="utf-8")` 寫 `raw.decode()`；`blobs` 表 `INSERT OR IGNORE`。
- `read_json_blob(hash) -> Any`：讀 gz → `json.loads`。檔案不存在 → `FileNotFoundError`。
- 結果的 `result_hash` 就是結果 JSON 的 blob hash。**NaN／Infinity** 允許（用預設的 `allow_nan=True`，與既有快照一致）。

**方法**（皆 thread-safe）：

| 方法 | 說明 |
|---|---|
| `create_study(name, purpose="", insight_markdown="", origin="local", study_uid=None)` | `study_uid` 缺少時 `uuid.uuid4().hex`；名稱去頭尾空白，空字串 → `ConfigError` |
| `get_or_create_study(name)` | 以 `(origin='local', name)` 查詢 |
| `update_study(study_id, *, name=None, purpose=None, insight_markdown=None)` | 回傳更新後的 dict；不存在回 `None` |
| `list_studies()` | 每筆含 `run_count` |
| `delete_study(study_id, delete_runs=False)` | `delete_runs=True` 時，僅刪除「只屬於這個研究專案」的執行紀錄 |
| `find_run_by_key(run_key)` | 回傳 dict 或 `None` |
| `insert_run(...)` / `restart_run(run_id, ...)` | 建立或重置為 `running` |
| `finish_run(run_id, result, artifacts, summary, duration_seconds)` | 寫 blob、更新列（`status='success'`、`result_hash`、`result_bytes`、`finished_at`） |
| `fail_run(run_id, error, duration_seconds)` | `status='failed'` |
| `link_run(study_id, run_id)` / `unlink_run(study_id, run_id)` | `INSERT OR IGNORE` |
| `list_runs(study_id=None, kind=None, status=None, limit=200, offset=0)` | 新到舊；`limit` 上限 1000，超過 → `ConfigError` |
| `get_run(run_id)` | 含 `studies`（`[{id,name}]`）、`artifacts`（不含資料列） |
| `load_result(run_id)` / `load_artifact(run_id, name)` | 讀 blob |
| `delete_run(run_id)` | 刪紀錄後做 blob 垃圾回收：沒有任何 `research_runs.result_hash` 或 `run_artifacts.blob_hash` 參照的 blob 才刪檔與 `blobs` 列 |
| `close()` | |

**執行紀錄 dict 的形狀**（所有對外回傳都用這個）：`id, run_key, kind, method_version, config, scope, data_fingerprint, status, error, summary, result_hash, result_bytes, duration_seconds, code_version, origin, imported_bundle_uid, parent_run_id, created_at, finished_at, studies, artifacts`（JSON 欄位都已 `json.loads`）。

測試：建表冪等、版本不符報錯、blob 寫入去重與讀回、`delete_run` 後共用 blob 仍在而獨佔 blob 被刪、`recover_interrupted`、`delete_study(delete_runs=True)` 只刪獨佔的執行紀錄、多執行緒同時 `link_run` 不出錯。

#### T1.5 研究方法登錄 `methods.py`、`builtin_methods.py`

```python
@dataclass(frozen=True, slots=True)
class ArtifactData:
    name: str
    description: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

@dataclass(frozen=True, slots=True)
class ResearchResult:
    sections: tuple[dict[str, Any], ...]      # 每節：title, columns, rows, grain, row_count, backend
    extras: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactData, ...] = ()

@dataclass(slots=True)
class ResearchContext:
    config: AppConfig
    facade: Any                  # AnalysisFacade
    scope: dict[str, Any]        # 已正規化
    progress: Callable[[str, float | None, str | None], None] | None
    def source_node(self): ...   # Filter(Source("pitches", PITCH_GRAIN), scope_filter_expr(scope))；scope 為空則只回 Source
    def engine(self) -> AnalysisEngine: ...   # 用 config 的路徑與 analysis_backend

class ResearchMethod:
    kind: str; version: int
    label_zh: str; label_en: str
    requires_scope: bool
    fields: tuple[ConfigField, ...]
    def validate(self, config: dict, ctx: ResearchContext) -> None: ...   # 跨欄位檢查，錯誤 raise ConfigError；預設不做事
    def run(self, ctx: ResearchContext, config: dict) -> ResearchResult: ...
    def describe(self) -> dict: ...  # kind, version, labels, requires_scope, fields=[f.to_dict()]
```

登錄：`register_method(method)`（同一個 `kind` 重複登錄不同物件 → `ValueError`）、`get_method(kind)`（未知 → `ConfigError`）、`list_methods()`。

**`analysis_payload`**（`version=1`、`requires_scope=False`、欄位只有 `payload: json required`）：
- `validate`：`payload` 必須是 `dict` 且有 `mode`；**含 `result_limit` 鍵 → `ConfigError`**（避免結果被靜默截斷）。
- `run`：`result = ctx.facade.analyze(payload, progress=ctx.progress)`。有 `sections` 就直接用；否則包成單一節 `{"title": payload["mode"], "columns", "rows", "grain", "row_count", "backend"}`。`extras` 放 `{"payload_mode": mode}`。

測試：重複登錄、未知 `kind`、`describe()` 形狀、`analysis_payload` 在測試資料庫上跑 `basic` 分組統計並核對列。

#### T1.6 服務 `service.py`

```python
class ResearchService:
    def __init__(self, config: AppConfig, facade, store: ResearchStore | None = None): ...
    def methods(self) -> list[dict]
    def run(self, kind, config, *, study_id=None, study_name=None, force=False, parent_run_id=None, progress=None) -> dict   # {"run": {...}, "reused": bool, "reproduced": bool | None}
    def rerun(self, run_id, *, study_id=None) -> dict
    def result_page(self, run_id, section_index, offset=0, limit=200) -> dict
    # 其餘為對 ResearchStore 的薄包裝：studies / runs 的列出、取得、更新、刪除
    def run_detail(self, run_id) -> dict   # get_run + local_data_match
    def close(self)
```

**`run` 的步驟（照順序，每步都要有測試）：**

1. `method = get_method(kind)`。
2. 設定：`raw = dict(config)`；`raw_scope = raw.pop("scope", None)`；`resolved = normalize_config(method.fields, raw)`；`scope = normalize_scope(raw_scope, required=method.requires_scope)`；`resolved["scope"] = scope`。
3. `method.validate(resolved, ctx)`（`ctx` 此時 `progress=None`）。
4. `fp = compute_scope_fingerprint(config.database_path, scope)`。若 `scope` 非空而 `total_rows == 0` → `ConfigError("Scope contains no pitches / 研究範圍內沒有資料")`。
5. `run_key = sha256(canonical_json({"format":"research-run-v1","kind":kind,"method_version":method.version,"config":resolved,"scope_fingerprint":fp["scope_fingerprint"]}))`。
6. 研究專案：`study_id` 優先；否則 `study_name`（`get_or_create_study`）；都沒有 → 名稱 `未分類 Unsorted`。
7. 查 `find_run_by_key`：
   - 存在、`status='success'`、`force=False` → `link_run`、回傳 `reused=True`。
   - 存在且 `status='running'`（本行程的 `_inflight` 集合中）→ `ConfigError("The same run is already in progress / 相同的研究正在執行")`。
   - 存在且 `failed`，或 `force=True` → 重新執行並**重用同一列**（`restart_run`）。
8. 不存在 → `insert_run(status='running', ...)`。
9. `job_id = start_analysis_job(f"research:{kind}")`；`progress` 轉接到 `update_analysis_job`（同時呼叫呼叫者給的 `progress`）。
10. 執行 `method.run`；計時；組成結果 dict `{"version":"research-result-v1","kind":kind,"sections":[...],"extras":{...}}`；`summary = {"section_titles":[...], "section_row_counts":[...], "total_rows":n, "artifact_names":[...]}`。
11. `finish_run`；`finish_analysis_job`。
12. 例外：`fail_run`、`finish_analysis_job(error=...)`、再 raise。步驟 1–4 的設定錯誤**不建立紀錄**，直接 raise。
13. **force 且原本成功**：新 `result_hash` 等於舊的 → `reproduced=True`（只更新時間與耗時）；不同 → 覆蓋為新結果，並在 `summary` 記 `previous_result_hash` 與 `reproduced=False`。
14. `parent_run_id` 寫入新建的列。
15. 執行緒安全：用 `threading.Lock` 與 `_inflight: set[str]` 防止相同 `run_key` 同時執行。

`code_version`：`f"{__version__}"`，若在 git 倉庫內且 `git rev-parse --short=7 HEAD` 在 2 秒內成功則為 `f"{__version__}+g{sha}"`；整個流程只在第一次呼叫時查，之後快取；失敗時不含 `+g…`。**不進 `run_key`。**

`run_detail`：`get_run` 加上 `local_data_match`：`scope` 為空 → `None`；否則重算本機的 `scope_fingerprint`，與紀錄中的相同 → `True`，不同 → `False`；本機算不出來（缺資料表）→ `None`。

`rerun(run_id)`：取出該紀錄，用其 `kind`、`config`（含 `scope`）呼叫 `run(..., force=True, parent_run_id=run_id)`；若該 `run_key` 在本機已存在就依 `force` 重跑。方法版本與紀錄不同 → 仍執行（新 `run_key`）。

`result_page`：`limit` 1–1000（超出 → `ConfigError`）；回傳 `{"section": {...欄位、grain、row_count、title, backend}, "offset", "limit", "rows": [...], "total": n}`。

測試（`tests/test_research_service.py`，用 `analysis_payload` 與一個測試用的假方法）：
- 設定錯誤不建立紀錄；
- 第一次 `reused=False`，第二次 `reused=True` 且 `id` 相同、方法的 `run` 只被呼叫一次；
- 修改範圍內的一列後再跑 → 新 `run_key`；修改範圍外的列 → 仍重用；
- 方法拋例外 → `failed` 紀錄、錯誤文字保存、再跑一次可成功並重用同一列；
- `force=True` 且結果相同 → `reproduced=True`；假方法回傳不同結果 → `reproduced=False` 且 `summary.previous_result_hash`；
- 同時啟動兩個相同 `run_key` → 第二個 `ConfigError`；
- 研究專案連結：不同 `study_name` 共用同一次執行；
- `local_data_match` 三種情況；
- `result_page` 分頁與上限。

#### T1.7 研究檔 `bundle.py`

```python
def export_bundle(store, *, study_ids=None, run_ids=None, include_artifacts=True, notes="", app_version="") -> bytes
def import_bundle(store, data: bytes) -> dict
```

**ZIP 內容**（只有這四種路徑，其他一律拒絕）：

```
manifest.json
studies/<study_uid>.json
runs/<run_key>.json
blobs/<hash>.json.gz
```

- `blobs/*` 用 `ZIP_STORED`（本來就是 gzip）；其他用 `ZIP_DEFLATED`。
- `manifest.json`：`{"format":"treepolo-research-bundle-v1","bundle_uid":uuid4hex,"created_at":...,"app_version":...,"code_versions":[去重的 code_version],"notes":...,"studies":[{"study_uid","name","run_keys":[...]}],"runs":[{"run_key","kind","result_hash","artifact_hashes":[...]}],"blobs":[{"hash","bytes"}]}`。
- `runs/<run_key>.json`：該執行紀錄除本機 `id`、`parent_run_id`、`studies` 以外的全部欄位，加 `artifacts: [{name, description, columns, row_count, blob_hash, blob_bytes}]`。
- `studies/<study_uid>.json`：`study_uid, name, purpose, insight_markdown, created_at, updated_at`。
- 選取：給 `study_ids` → 這些研究專案及其所有成功的執行紀錄；給 `run_ids` → 這些紀錄；兩者都給 → 聯集；都不給 → `ConfigError`。只匯出 `status='success'` 的紀錄。`include_artifacts=False` 時不含 artifacts（紀錄中的 `artifacts` 為空陣列）。
- 研究專案的 `insight_markdown` 就是「研究者的見解」，一併帶出。

**`import_bundle` 步驟**：

1. `zipfile.ZipFile(io.BytesIO(data))`；失敗 → `ConfigError("Not a valid research bundle")`。
2. 檢查所有成員名稱符合 `^(manifest\.json|studies/[0-9a-f]{32}\.json|runs/[0-9a-f]{64}\.json|blobs/[0-9a-f]{64}\.json\.gz)$`，否則 `ConfigError`（防 zip slip）。解壓總大小上限 `1_000_000_000` 位元組，超過 → `ConfigError`。
3. 讀 `manifest.json`，`format` 不等於 `treepolo-research-bundle-v1` → `ConfigError`。
4. **驗證每個 blob**：gunzip 後的位元組做 sha256，必須等於檔名中的 hash，否則 `ConfigError("Blob corrupted")`。manifest 列出的 blob 必須都存在。
5. 寫入 blob（內容定址，已存在就略過）。
6. 在**單一資料庫交易**內：
   - 研究專案：`study_uid` 已存在 → 合併（保留本機的名稱與見解，只加連結）；否則建立，`origin='imported'`（名稱衝突時加後綴 ` (imported)`）。
   - 執行紀錄：`run_key` 已存在 → 略過（`runs_skipped_duplicate += 1`），若 `result_hash` 不同則在 `warnings` 記一筆，但仍把它連到研究專案；否則新增，`origin='imported'`、`imported_bundle_uid`、`status='success'`。
7. 回傳 `{"bundle_uid","studies_added","studies_merged","runs_added","runs_skipped_duplicate","warnings":[...]}`。
8. 任何錯誤 → 交易回滾，已寫入的 blob 保留（無害）。

測試：匯出→匯入到全新的資料庫，結果 `result_hash`、`config`、`data_fingerprint`、見解皆相同；同一份匯入兩次（第二次全部略過）；竄改 blob 內容 → 報錯且資料庫無新增；非法成員名稱；只匯出指定的 `run_ids`；`include_artifacts=False`；匯入後的紀錄 `origin='imported'`；`rerun` 匯入的紀錄會產生 `parent_run_id` 指向它的新紀錄。

#### T1.8 HTTP API（`research/api.py` 與 `webapp.py`）

`webapp.AppServices.__init__` 新增 `self.research = ResearchService(config, self.analysis)`；`serve()` 的 `finally` 呼叫 `services.research.close()`。（Stage 4D 的 `install()` 會包住 `AppServices.__init__`，不受影響。）

**`_Handler` 需要的新輔助方法**：
- `_bytes(self, status, body: bytes, content_type: str, filename: str | None = None)`：送出二進位回應（含 `Content-Disposition: attachment; filename="..."`，檔名只留英數與 `-_.`）。
- `_read_body_bytes(self, max_bytes: int) -> bytes`：讀取請求本文，超過上限 → `RequestError`。

**路由**（全部在 `/api/research/` 下；`do_GET`／`do_POST`／`do_DELETE` 各加一段，放在原有判斷之前）：

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/research/methods` | `{"methods":[describe()...]}` |
| GET | `/api/research/studies` | `{"studies":[...]}` |
| POST | `/api/research/studies` | 本文 `{name,purpose,insight_markdown}`，建立 |
| POST | `/api/research/studies/{id}` | 更新（本文含要改的欄位） |
| DELETE | `/api/research/studies/{id}?delete_runs=0\|1` | |
| GET | `/api/research/runs?study_id=&kind=&status=&limit=&offset=` | `{"runs":[...]}`（不含結果資料列） |
| GET | `/api/research/runs/{id}` | `{"item": run_detail}` |
| GET | `/api/research/runs/{id}/result?section=0&offset=0&limit=200` | 分頁資料列 |
| POST | `/api/research/run` | 本文 `{kind, config, study_id?, study_name?, force?}`；同步執行 |
| POST | `/api/research/runs/{id}/rerun` | 以本機資料重跑 |
| POST | `/api/research/runs/{id}/studies` | 本文 `{study_id, action:"add"\|"remove"}` |
| DELETE | `/api/research/runs/{id}` | |
| POST | `/api/research/export` | 本文 `{study_ids?, run_ids?, include_artifacts?, notes?}`；回傳 ZIP（`application/zip`，檔名 `research-YYYYMMDD-HHMMSS.treepolo-research.zip`） |
| POST | `/api/research/import` | 請求本文就是 ZIP 位元組（上限 1,000,000,000）；回傳匯入報告 JSON |

**重要**：現有 `do_POST` 一開頭就 `payload = self._read_json()`（2 MB 上限）。`/api/research/import` 必須在 `_read_json()` **之前**處理。其餘研究 POST 路由可在 `_read_json()` 之後處理。找不到的 `/api/research/...` 路徑回 `404 {"error":"Unknown API endpoint"}`。

測試（`tests/test_research_api.py`）：不啟動真正的 HTTP 伺服器；比照 `tests/test_analysis_state.py` 直接建立 `AppServices(config)` 測服務層，另外用 `http.server` 在 `127.0.0.1:0` 啟動 `ThreadingHTTPServer((host, 0), _Handler)`（`server.services = services`），以 `urllib.request` 打 `methods`、`run`（第二次 `reused`）、`runs/{id}/result`、`export`、`import`（匯入到另一個服務）。

#### T1.9 CLI

`research/cli.py`：`add_arguments(parser)` 與 `run_command(args, config) -> int`。`cli.py` 的 `build_parser` 加 `research = sub.add_parser("research", help=...)` 並呼叫 `add_arguments`；`main` 在 `analytics-sync` 區塊之後、`_engine(config)` 之前加：

```python
if args.command == "research":
    from .research.cli import run_command
    return run_command(args, config)
```

子指令（輸出一律 `json.dumps(..., ensure_ascii=False, indent=2)` 到 stdout；設定錯誤輸出到 stderr 並回傳 2）：

```
research methods
research run --kind K --config-file PATH [--study NAME] [--force]
research list [--study NAME] [--kind K] [--limit N]
research show RUN_ID [--section N] [--limit N]
research rerun RUN_ID
research study NAME [--purpose-file PATH] [--insight-file PATH]     # 建立或更新研究專案的目的與見解
research export --out PATH (--study NAME ... | --run-id N ...) [--no-artifacts] [--notes TEXT]
research import PATH
```

`research run` 的輸出含 `run`（摘要，不含結果資料列）與 `reused`。測試（`tests/test_research_cli.py`）：以 `main([...])` 直接呼叫，用 `capsys` 檢查輸出，涵蓋：`run` 兩次（第二次 `reused: true`）、`list`、`show`、`export`→`import`（到另一個 `data_dir`）、錯誤回傳碼。

#### T1.10 前端檢視頁

- 檔案 `web_static/research-runs-page.js`，寫法比照 `cluster-comparison-page.js`（IIFE、`injectStyles`、`inject`、`DOMContentLoaded` 守衛）。
- 在 `index.html` 的 `<script src="/app.js"></script>` 之後加 `<script src="/research-runs-page.js"></script>`。（`webapp._static` 與 `stage4d_static` 都是把額外的 `<script>` 加在 `</body>` 前，不需要動；兩條路徑都會送出含這行的 `index.html`。）
- 導覽：在 `.navigation-pane` 新增一個 `task-group`，標題「研究 Research」，內含按鈕 `data-panel="research-runs-panel"`、文字「研究紀錄 Research Runs」；呼叫 `window.treepoloPanels.register("research-runs-panel", "research-runs")`。
- 面板（插在 `#result-window` 之前）：
  - 左：研究專案清單（名稱、執行數、「新增研究專案」）；選取後顯示目的與見解的文字框與「儲存」。
  - 右：執行紀錄表（#、方法、狀態、範圍摘要、建立時間、來源徽章 `匯入 Imported`、資料相符徽章 `資料相符 Matches local data`／`資料不同 Differs from local data`）。
  - 點選一列：顯示設定（`<pre>` 格式化 JSON）、資料指紋、摘要、各節結果（分頁，每頁 200 列，呼叫 `/result`）。
  - 按鈕：`匯出研究檔 Export`（POST export → 下載）、`匯入研究檔 Import`（`<input type="file">` → POST 原始位元組）、`以本機資料重跑 Re-run Locally`、`刪除 Delete`（`confirm()`）。
- 所有插入 HTML 的文字都要跳脫（照 `escapeHtml`）。
- 不使用外部函式庫、不使用 `localStorage`。
- 測試（`tests/test_research_ui_wiring.py`）：讀檔斷言 `research-runs-panel`、`/api/research/`、`register(` 路由、`index.html` 含 `research-runs-page.js`、檔案沒有 `localStorage`；另外 CI 會跑 `node --check`。

#### T1.11 收尾

- `ci.yml` 加 `- run: node --check src/treepolo_mlb_data/web_static/research-runs-page.js`。
- 規劃文件第 10 節 P1 列註記完成日期。
- 在 `README.md` 的功能清單加一條：「Research runs：自動記錄、可匯出匯入的研究紀錄（設定、結果、資料範圍）」。

### 1.5 P1 驗收（全部要做）

1. `PYTHONPATH=src .venv/bin/pytest -q -m 'not integration'` 全部通過（基準 226 + 新增）。
2. `node --check` 對新增的 JS 通過。
3. 手動流程（用一個小資料庫，例如回補 2025-06-10 至 2025-06-14）：
   - `research run --kind analysis_payload --config-file cfg.json --study demo` → `reused: false`；
   - 同一指令再跑 → `reused: true`、同一個 `run.id`；
   - `research export --study demo --out /tmp/demo.zip` → 新的 `data_dir` 下 `research import /tmp/demo.zip` → `research list` 顯示該紀錄且 `origin: imported`；
   - 兩邊的 `result_hash` 相同。
4. 啟動 `ui --no-browser`，用 `curl` 打 `/api/research/methods`、`/api/research/studies`，回傳 JSON；若環境有 Chromium，開啟 `/?page=research-runs` 截圖確認頁面可見。
5. 對使用者的回報要包含：春訓與季後賽預設排除（1.2 節）、所有新檔案與新指令、測試通過數。

### 1.6 P1 停下來問使用者的情況（除了 0.5 節）

- `research_runs.sqlite3` 與既有 `analysis_state.sqlite3` 的關係想改成同一個檔案。
- 需要修改既有 `/api/analyze` 的行為才能整合。
- 研究檔格式需要改動（已寫死 `treepolo-research-bundle-v1`）。

---

## 2. P2：資料前處理（逐球序列欄位、排除規則、結果類別、左右手鏡像、資料剖析）

### 2.1 目標

1. 在分析引擎上提供四組**分析樹建構函式**：非投球列與短打排除、逐球序列欄位、單球結果類別、左右手鏡像（同側／異側檢視）。
2. 新增 `data_profile` 研究方法，量測真實資料的值域、欄位覆蓋率、短打比例、好球帶定義、鏡像規則所需的數據。
3. 在雲端下載 2023–2026 資料並執行剖析，把結果寫回規劃文件。

**非目標**：任何網頁介面、任何統計方法、價值表（P3）。

### 2.2 已驗證的參考實作

**附錄 A 是在 SQLite 與 DuckDB 兩個後端驗證過的程式碼**，直接放進對應模組，不要重寫邏輯。附錄 B 是配套的測試資料與預期答案。要特別小心的地方（已在參考實作中處理，修改時不得破壞）：

1. **NULL 陷阱**：`description NOT IN (...)` 遇到 NULL 會得到 NULL，整列被濾掉。排除用的條件必須寫成「`IS NULL` 或 `NOT IN`」。
2. **短打旗標必須 NULL 安全**：用 `Case(((Boolean(or, ...), 1),), 0)`，NULL 與 false 都得到 0。
3. **LIKE 大小寫**：SQLite 的 `LIKE` 不分大小寫，DuckDB 分；所以 `%bunt%` 與 `%Bunt%` 兩個都要寫。
4. **自動好壞球要在計算前一球／連投之前排除**，使「前一球」指前一個**真正的投球**。
5. **壞資料不丟棄**：沒有對應到的結果值進入類別 `unclassified`，並在剖析中列出次數。

### 2.3 任務

#### T2.1 引擎支援 `LIKE` 與字串連接

`analysis/compiler.py` 的 `_BINARY_OPS` 加入 `"LIKE"` 與 `"||"`（兩個後端的語法相同）。`codec.py` 對運算子是通用字串，不用改。

測試 `tests/test_analysis_string_ops.py`：在測試資料庫上
- `Binary(Column("des"), "LIKE", Literal("%bunt%"))` 篩選；
- `Binary(Binary(Column("a"), "||", Literal(">")), "||", Column("b"))` 連接；
- 兩個後端（`backend="sqlite"` 與 `"duckdb"`）結果相同，且 DuckDB 的 `result.backend == "duckdb"`（見 0.4 節）；
- 序列化往返：`node_from_dict(node_to_dict(node))` 後編譯結果相同。

#### T2.2 `analysis/exclusions.py`

放入附錄 A 的 `NON_PITCH_DESCRIPTIONS`、`NON_PITCH_TYPES`、`exclude_non_pitch_rows`、`bunt_pitch_flag`、`exclude_bunt_plate_appearances`，再加入：

```python
def apply_exclusions(source, *, bunt_policy: str = "exclude_plate_appearance",
                     non_pitch_descriptions=NON_PITCH_DESCRIPTIONS, non_pitch_types=NON_PITCH_TYPES):
    """順序固定：先短打（需要完整的打席）、再非投球列。bunt_policy: exclude_plate_appearance | exclude_pitch | keep"""
```

- `exclude_pitch`：只濾掉 `bunt_pitch_flag() = 1` 的那一球（不用 `Window`）。
- `keep`：不處理短打。
- 其他值 → `ValueError`。

匯出到 `analysis/__init__.py` 的 `__all__`。

#### T2.3 `analysis/sequence.py`

放入附錄 A 的 `sequence_features(source, carry_fields, *, memory=2, type_field="pitch_type")`，並匯出。

**輸出欄位的語意（固定）：**

| 欄位 | 型別 | 語意 |
|---|---|---|
| `pitch_index_in_pa` | 整數 | 該球在打席內（來源列中）的第幾球，從 1 開始。**排除自動好壞球後重新編號**，與 `pitch_number` 不同 |
| `prev1_pitch_type` … `prev{memory}_pitch_type` | 文字 | 往前第 k 球的球種；沒有則 NULL |
| `history_key` | 文字 | 往前 `memory` 球的球種，**從舊到新**以 `>` 連接；打席開始之前以 `^` 補；未知球種以 `?` 表示。例：`memory=2`、打席第 1 球 `^>^`、第 2 球（前一球 FF）`^>FF`、第 3 球（FF、SL）`FF>SL` |
| `prev1_description` | 文字 | 前一球的 `description` |
| `speed_diff_prev1` | 浮點 | `release_speed − 前一球 release_speed`；任一為 NULL 則 NULL |
| `dx_prev1`、`dz_prev1` | 浮點 | `plate_x`、`plate_z` 與前一球的差 |
| `streak_pos` | 整數 | 同球種連續第幾顆（含當前這球，第一顆為 1）；當前球種為 NULL 則 NULL |
| `history_complete` | 0/1 | 0 表示當前球或打席中更早的球有未知（NULL）球種 |

限制與規則：
- `memory` 範圍 1–5，超出 → `ValueError`。
- 呼叫端必須先做排除（`apply_exclusions`）再呼叫 `sequence_features`，**且 `source` 必須含欄位** `game_pk, at_bat_number, pitch_number, pitch_type, release_speed, plate_x, plate_z, description`。
- `carry_fields` 是要保留的原欄位（不要全帶，只帶需要的）；不得與上表欄位同名。
- 打席的鍵固定為 `(game_pk, at_bat_number)`，排序鍵為 `pitch_number`。
- 輸出的 grain 為 `PITCH_GRAIN`（以 `pitch_uid` 為鍵）；因此 `carry_fields` **必須包含 `pitch_uid`**（`NumericalTable` 與結果輸出都要求保留 grain 的鍵；在函式內檢查，缺少 → `ValueError`）。

#### T2.4 `analysis/outcomes.py`

放入附錄 A 的 `IN_PLAY_OUTS`、`IN_PLAY_OTHER`、`outcome_category_expr()`，再加：

```python
OUTCOME_CATEGORIES = ("ball","called_strike","whiff","foul_tip","foul","hit_by_pitch",
                      "in_play_home_run","in_play_triple","in_play_double","in_play_single",
                      "in_play_out","in_play_error_other","bunt","unclassified")
def outcome_category_expr(merge: Mapping[str, str] | None = None): ...
```

`merge`（選填）把類別改名／合併，例如 `{"in_play_single": "in_play_hit", "in_play_double": "in_play_hit"}`；鍵必須是 `OUTCOME_CATEGORIES` 的成員，否則 `ValueError`。實作方式：在建構 `Case` 時把值字串先過 `merge`。附錄 A 的版本是無 `merge` 的基礎；加入 `merge` 後原本的測試必須仍通過。

規則：這個運算式**只負責分類**，不負責排除；`bunt` 類別只在 `bunt_policy="keep"` 時才會出現在資料中。

#### T2.5 `analysis/handedness.py`

放入附錄 A 的 `ZONE_MIRROR`、`NEGATE_FIELDS`、`mirror_to_right_handed_pitcher(...)`。

- 這是**衍生的合併檢視**（規劃文件 2.6）；基礎四組結果永遠不鏡像。
- 鏡像規則表（P2 初始版）：

| 欄位 | 規則 | 狀態 |
|---|---|---|
| `plate_x`、`pfx_x`、`release_pos_x`、`vx0`、`ax` | 取負 | 已決定 |
| `spin_axis` | `360 − x`（0 保持 0） | 已決定 |
| `zone` | `ZONE_MIRROR` 對照 | 已決定，但**必須用 T2.8 的資料檢查通過** |
| `hc_x`、`attack_direction`、`intercept_ball_minus_batter_pos_x_inches`、`api_break_x_arm`、`api_break_x_batter_in` | 待資料決定 | 預設**不鏡像**；T2.8 依第 2.6 節的準則決定，並把結果寫入 `MIRROR_RULES` 常數與規劃文件 |

- 新增模組常數 `MIRROR_RULES: dict[str, str]`，值為 `"negate" | "axis_360" | "zone_map" | "none"`，並讓 `mirror_to_right_handed_pitcher` 依 `MIRROR_RULES` 決定要處理的欄位（呼叫端傳入的 `carry_fields` 中屬於規則表且規則非 `"none"` 的欄位才會被鏡像；不在規則表的欄位原樣保留）。初始 `MIRROR_RULES` 只含「已決定」那幾列，其餘列在 T2.8 補上。

#### T2.6 測試

在 `tests/seq_fixtures.py` 實作附錄 B 的假資料建構函式 `make_seq_db(path)`（含 `settings` 與 `_ingested_at`），並寫：

- `tests/test_exclusions.py`：附錄 B 的預期保留列、`exclude_pitch` 與 `keep` 兩種 `bunt_policy`、NULL `description` 與 NULL `pitch_type` 的列在排除後仍保留。
- `tests/test_sequence_features.py`：附錄 B 的完整預期表；`memory=1` 與 `memory=3` 的 `history_key` 長度；`memory=0`、`6` 報錯；缺少 `pitch_uid` 報錯；**SQLite 與 DuckDB 結果相同**（用 `pytest.approx` 比浮點數，並斷言 `result.backend`）。
- `tests/test_outcomes.py`：附錄 B 的類別表；`merge`；未知 `events` 進入 `unclassified`。
- `tests/test_handedness.py`：附錄 B 的鏡像資料；對 `zone`、`spin_axis`、取負欄位逐一斷言；NULL 的 `p_throws` 得到 `frame_group IS NULL`；兩個後端一致；「同側／異側」分組正確；`MIRROR_RULES` 之外的欄位原樣。

#### T2.7 `data_profile` 研究方法

放在 `research/builtin_methods.py`（實作的 SQL 放 `research/profile.py`）。`kind="data_profile"`、`version=1`、`requires_scope=True`。設定欄位：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `sections` | `str_list`（unique_sorted） | 全部 11 個名稱 | 要計算的節 |
| `tracked_fields` | `str_list` | `["launch_speed","launch_angle","hc_x","hc_y","hit_distance_sc","bat_speed","swing_length","attack_angle","attack_direction","swing_path_tilt","intercept_ball_minus_batter_pos_x_inches","intercept_ball_minus_batter_pos_y_inches","miss_distance","plate_x","plate_z","sz_top","sz_bot","release_speed","release_spin_rate","spin_axis","vx0","ax"]` | 覆蓋率要量測的欄位；每個必須是 `pitches` 現有欄位（SQL 識別字用 `schema.quote_ident`，不得字串拼接未驗證的名稱） |

輸出的節（`title` 為英文名稱加中文；欄位固定，**所有查詢都限定在研究範圍內**）：

| 節 | 欄位 | 內容 |
|---|---|---|
| `rows_by_season` | `game_year, game_type, rows, games, min_date, max_date` | 各球季與賽事類型的筆數 |
| `description_values` | `game_year, description, rows` | `description` 值域 |
| `in_play_events` | `game_year, events, rows` | `description='hit_into_play'` 的 `events` 值域 |
| `pitch_type_values` | `game_year, pitch_type, rows` | 球種值域（NULL 顯示 `(null)`） |
| `coverage_by_outcome` | `game_year, outcome_group, field, rows, non_null, non_null_pct` | 各結果群組對 `tracked_fields` 的非空比例。`outcome_group`：`whiff`（swinging_strike、swinging_strike_blocked）、`foul_tip`、`foul`、`in_play`、`called_strike`、`ball`（ball、blocked_ball）、`other` |
| `bunt_share` | `game_year, plate_appearances, bunt_plate_appearances, bunt_pa_pct, pitches, pitches_in_bunt_pa, pitches_in_bunt_pa_pct` | 短打打席比例（SQL 用與 `bunt_pitch_flag` 相同的條件） |
| `strike_zone_definition` | `game_year, batters, pct_batters_constant_sz_top, median_distinct_sz_top, ratio_min, ratio_median, ratio_max` | 每位打者該年 `sz_top` 是否只有一個值；`avg(sz_top)/avg(sz_bot)` 的分佈 |
| `handedness_groups` | `game_year, p_throws, stand, rows` | 四組筆數 |
| `mirror_field_means` | `p_throws, stand, field, mean, rows_non_null` | 對 `plate_x, pfx_x, release_pos_x, vx0, ax, api_break_x_arm, api_break_x_batter_in, attack_direction, intercept_ball_minus_batter_pos_x_inches, hc_x` 的平均；另加一個 `field='spin_axis_share_below_180'` 的列（`spin_axis` 小於 180 的比例） |
| `zone_plate_check` | `zone, stand, rows, mean_plate_x, mean_plate_z` | 各 `zone` 與打者站位的平均進壘位置 |
| `outcome_category_counts` | `outcome_category, description, events, rows` | **用 P2 的分析樹**（`Aggregate` over `Filter(scope)`，以 `outcome_category_expr()` 分組）執行，涵蓋整個範圍 |

`extras`：`{"scope": ..., "pitch_rows_in_scope": n, "generated_by": "data_profile v1"}`。每一節的 `grain` 用 `{"keys": [...該節的分組欄位...], "label": 節名}`，`backend` 為 `"sqlite"`（`outcome_category_counts` 取引擎實際後端）。

測試（`tests/test_data_profile.py`）：以附錄 B 的假資料，核對 `rows_by_season`、`description_values`、`bunt_share`（4 個短打打席／共 8 個打席）、`coverage_by_outcome` 的一格、`outcome_category_counts`；`tracked_fields` 含不存在的欄位 → `ConfigError`；經 `ResearchService.run` 執行兩次，第二次 `reused`。CLI 煙霧測試：`research run --kind data_profile`。

#### T2.8 雲端：下載資料、執行剖析、回寫規劃文件

**這一步需要網路與較長時間，只在雲端環境做。**

1. 確認環境：`df -h .`（可用空間 < 8 GB 就停下來問）、`curl -s -o /dev/null -w '%{http_code}' 'https://baseballsavant.mlb.com/'` 應為 200。
2. 建立設定與資料夾（資料放在專案根的 `data/`，已被忽略）：`init`。
3. 回補（背景執行，並用 `tail` 檢視進度；`--resume` 可續跑）：

```bash
nohup <CLI> backfill --start 2023-03-01 --end <今天> --resume > backfill.log 2>&1 &
```

   - **估計**：以 5 天／35 秒推算，約 1–2 小時；資料庫約 3–4 GB，加上 DuckDB 鏡像約 5 GB。這是估計，不是實測。超過 3 小時或可用空間降到 8 GB 以下 → 停下來問。
   - 完成後 `<CLI> status` 與 `<CLI> analytics-sync`（建立 DuckDB 鏡像，約 2–5 分鐘）。
4. 執行剖析（設定檔 `profile.json`）：

```json
{"scope": {"game_years": [2023, 2024, 2025, 2026], "game_types": ["R"]}}
```

   `<CLI> research run --kind data_profile --config-file profile.json --study "P2 data profile"`。再另外用 `"game_types": ["S","F","D","L","W"]` 跑一次（春訓與季後賽各輪次），用於確認 `game_type` 的實際值域（不影響研究範圍預設）。
5. **決定鏡像規則**：讀 `mirror_field_means`、`zone_plate_check`，依下列準則更新 `MIRROR_RULES`：
   - 對欄位 `f`，令 `mR`、`mL` 為右投、左投的平均（各自把兩種站位合併加權平均）。若 `mR × mL < 0` 且 `|mR + mL| ≤ 0.35 × (|mR| + |mL|)` → `"negate"`。若 `mR × mL > 0` 且 `|mR − mL| ≤ 0.35 × max(|mR|, |mL|)` → `"none"`（已經是相對於投手或打者的方向）。**兩者都不符合 → 停下來問使用者。**
   - `spin_axis`：以 `spin_axis_share_below_180` 判斷。若右投的比例與左投的「1 − 比例」相差 ≤ 0.1 → `"axis_360"`；否則停下來問。
   - `zone`：每個 `stand` 都必須滿足：`zone` 1、4、7、11、13 的 `mean_plate_x` < 0；3、6、9、12、14 的 > 0；`zone` 1、2、3、11、12 的 `mean_plate_z` 大於 7、8、9、13、14。**任一不符 → 停下來問**（可能是編號假設錯誤）。
   - 確認 `hc_x` 的鏡像：本階段**不實作**（需要確認中心點）；`MIRROR_RULES["hc_x"] = "none"` 並在規劃文件註明未鏡像。
6. **回寫規劃文件**（`docs/PITCH_SEQUENCING_PLAN.md`），只改數字與已量測的事實，不改決策：
   - 2.7：把「已觀察到的 `description` 值」改成 2023–2026 的實際值域，並列出 `unclassified` 的次數與前幾名（若 `unclassified` 占比 > 0.5% → 停下來問）。
   - 2.9：用 `strike_zone_definition` 更新好球帶的描述（各年每位打者 `sz_top` 固定的比例）。
   - 2.10：以 `bunt_share` 的 2023–2026 數字更新短打比例。
   - 2.11：以 `coverage_by_outcome` 更新覆蓋率表，**特別列出 2023、2024 年球棒追蹤欄位的覆蓋率**；並在第 11 節把「球棒追蹤欄位在 2023、2024 年的覆蓋率」與「`zone` 在 2026 的定義」兩項標為已量測。
   - 2.6：寫入鏡像規則表（`MIRROR_RULES` 最終內容）。
   - 同時把 `description` 與 `pitch_type` 中**不在已知清單**的值整理成一個表，附在 2.7 後；若有看起來像「非投球」的值（如 `pitchout`、`intent_ball` 以外的） → 停下來問。
7. 把剖析結果匯出成研究檔（`research export --study "P2 data profile" --out p2-profile.zip`），告知使用者檔案位置。

#### T2.9 收尾

- 規劃文件第 10 節 P2 列註記完成日期。
- 效能量測：在真實資料上執行 `sequence_features(apply_exclusions(...))`（範圍 2023–2024，`carry_fields` 取 `pitch_uid, game_pk, at_bat_number, pitch_number, pitch_type, description, release_speed, plate_x, plate_z`），記錄 SQLite 與 DuckDB 各自的耗時與列數到規劃文件 2.9 後的備註；DuckDB 超過 120 秒 → 停下來問。

### 2.4 P2 驗收

1. 全部測試通過（含新增），且 `pytest` 通過數不低於 P1 完成時。
2. 附錄 B 的預期表在兩個後端都逐列相符（已由測試保證）。
3. 真實資料上：`data_profile` 完成並已匯出研究檔；`outcome_category_counts` 的 `unclassified` 比例已記錄；規劃文件第 2.6、2.7、2.9、2.10、2.11 節已回寫且 commit。
4. 對使用者的回報要包含：實際資料量與耗時、`unclassified` 比例、鏡像規則最終表、2023–2024 年球棒追蹤覆蓋率、任何需要使用者決定的事項。

### 2.5 P2 停下來問使用者的情況（除了 0.5 節）

- `unclassified` 占比超過 0.5%，或出現看起來像新結果類別的 `description`／`events`。
- 鏡像準則不符（2.3 的 T2.8 第 5 點）。
- 好球帶「2026 每位打者固定」的假設在完整資料中不成立。
- 效能超出 T2.9 的門檻。

---

## 3. P3–P6：概要與文件撰寫時機

這些階段**現在不寫細節**：它們依賴 P1、P2 的實測結果。按使用者安排的流程，每次換階段都由使用者先切換推理程度。

| 階段 | 內容（概要） | 細節文件何時寫 | 寫之前需要的輸入 |
|---|---|---|---|
| P3 | 直接數次數系統：左右打（及投手慣用手）分組、條件過濾、「走到同一點再比較」、各類結果機率與信賴區間、結果價值表與全壘打機率、位置模型 | P1、P2 開發完成後（使用者切換到高推理再叫我寫） | P2 的實測：值域、覆蓋率、`unclassified`、鏡像規則、序列欄位在 2023–2024 資料上的耗時 |
| P4 | 多類別迴歸、梯度提升、驗證工具（含「零衰減卻看起來衰減」的已知答案測試、留出集校準） | 與 P3 一起寫 | P3 的方法介面 |
| P5 | **雲端研究**：2023–2024 建模、2025 與 2026 檢驗、全期間附加研究、研究檔與見解 | **P3、P4 開發完成後**，另寫「研究計畫」文件（研究問題、要掃描的設定、判斷準則、停止條件、交付物） | P3、P4 實際可用的方法與設定 |
| P6 | 往後看多球的決策（動態規劃） | **P5 研究完成後**再寫，因為狀態定義、記憶長度、要看幾球都取決於 P5 的發現 | P5 的研究檔與見解 |

> 已由使用者確認（2026-10-06）：P5 的研究計畫在 P3、P4 開發完後寫；P6 的開發文件在 P5 研究完後寫，因為 P6 的設計依賴 P5 的結果。

**研究在哪裡**：P1–P4 只開發工具（用假資料與少量真實資料驗證工具本身正確）；真正的研究問題與執行是 **P5**，其計畫寫成獨立文件。

---

## 附錄 A：已驗證的參考實作

以下程式碼已在 SQLite 與 DuckDB 兩個後端以附錄 B 的資料執行，結果逐列相同。放入模組時：
- 刪掉測試用的 `sys.path`／`_compiler` 修補，改成 T2.1 修改 `compiler.py`；
- `analysis/exclusions.py`：`_flag`、`NON_PITCH_*`、`exclude_non_pitch_rows`、`bunt_pitch_flag`、`exclude_bunt_plate_appearances`；
- `analysis/sequence.py`：`PA`、`ORDER`、`sequence_features`；
- `analysis/outcomes.py`：`IN_PLAY_*`、`outcome_category_expr`；
- `analysis/handedness.py`：`ZONE_MIRROR`、`NEGATE_FIELDS`、`mirror_to_right_handed_pitcher`；
- 匯入從 `.model` 取（相對匯入），不是 `treepolo_mlb_data.analysis.model`。

```python
from __future__ import annotations

from .model import (
    Binary, Boolean, Case, Column, Filter, InList, IsNull, Literal, NamedExpr,
    OrderKey, PITCH_GRAIN, Project, Window, WindowField, WindowFrame,
)

C = Column
PA = (C("game_pk"), C("at_bat_number"))
ORDER = (OrderKey(C("pitch_number")),)


def _flag(*terms):
    """NULL-safe 1/0 flag: NULL or false -> 0."""
    return Case(((Boolean("or", tuple(terms)), Literal(1)),), Literal(0))


# --- exclusions ---------------------------------------------------------------
NON_PITCH_DESCRIPTIONS = ("automatic_ball", "automatic_strike", "pitchout", "intent_ball")
NON_PITCH_TYPES = ("PO", "IN", "AB", "AS")


def exclude_non_pitch_rows(source, descriptions=NON_PITCH_DESCRIPTIONS, pitch_types=NON_PITCH_TYPES):
    # NULL trap: NULL NOT IN (...) is NULL and would drop the row, so test IS NULL explicitly.
    return Filter(source, Boolean("and", (
        Boolean("or", (IsNull(C("description")), InList(C("description"), tuple(Literal(x) for x in descriptions), True))),
        Boolean("or", (IsNull(C("pitch_type")), InList(C("pitch_type"), tuple(Literal(x) for x in pitch_types), True))),
    )))


def bunt_pitch_flag():
    return _flag(
        InList(C("description"), (Literal("foul_bunt"), Literal("missed_bunt"))),
        InList(C("events"), (Literal("sac_bunt"), Literal("sac_bunt_double_play"))),
        Binary(C("des"), "LIKE", Literal("%bunt%")),
        Binary(C("des"), "LIKE", Literal("%Bunt%")),  # DuckDB LIKE is case-sensitive
    )


def exclude_bunt_plate_appearances(source):
    flagged = Window(source, (
        WindowField("__ta_is_bunt", "max", (bunt_pitch_flag(),), PA),
    ))
    return Filter(flagged, Binary(C("__ta_is_bunt"), "=", Literal(0)))


# --- sequence features ----------------------------------------------------------
def sequence_features(source, carry_fields, *, memory=2, type_field="pitch_type"):
    if not 1 <= memory <= 5:
        raise ValueError("memory must be between 1 and 5")
    l1 = [WindowField("pitch_index_in_pa", "row_number", (), PA, ORDER)]
    for k in range(1, memory + 1):
        l1.append(WindowField(f"prev{k}_pitch_type", "lag", (C(type_field), Literal(k)), PA, ORDER))
    for alias, col in (("prev1_release_speed", "release_speed"), ("prev1_plate_x", "plate_x"),
                       ("prev1_plate_z", "plate_z"), ("prev1_description", "description")):
        l1.append(WindowField(alias, "lag", (C(col), Literal(1)), PA, ORDER))
    n1 = Window(source, tuple(l1))

    changed = Case(((Boolean("or", (
        IsNull(C("prev1_pitch_type")), IsNull(C(type_field)),
        Binary(C("prev1_pitch_type"), "!=", C(type_field)),
    )), Literal(1)),), Literal(0))
    unknown = Case(((IsNull(C(type_field)), Literal(1)),), Literal(0))
    running = WindowFrame(None, 0)
    n2 = Window(n1, (
        WindowField("__ta_run_id", "sum", (changed,), PA, ORDER, running),
        WindowField("__ta_unknown_so_far", "sum", (unknown,), PA, ORDER, running),
    ))
    n3 = Window(n2, (WindowField("__ta_streak", "row_number", (), PA + (C("__ta_run_id"),), ORDER),))

    def element(k):
        return Case((
            (Binary(C("pitch_index_in_pa"), "<=", Literal(k)), Literal("^")),
            (IsNull(C(f"prev{k}_pitch_type")), Literal("?")),
        ), C(f"prev{k}_pitch_type"))

    key = element(memory)
    for k in range(memory - 1, 0, -1):
        key = Binary(Binary(key, "||", Literal(">")), "||", element(k))

    def diff(cur, prev):
        return Case(((Boolean("and", (IsNull(C(cur), True), IsNull(C(prev), True))), Binary(C(cur), "-", C(prev))),), None)

    fields = [NamedExpr(f, C(f)) for f in carry_fields]
    fields += [NamedExpr("pitch_index_in_pa", C("pitch_index_in_pa"))]
    fields += [NamedExpr(f"prev{k}_pitch_type", C(f"prev{k}_pitch_type")) for k in range(1, memory + 1)]
    fields += [
        NamedExpr("history_key", key),
        NamedExpr("prev1_description", C("prev1_description")),
        NamedExpr("speed_diff_prev1", diff("release_speed", "prev1_release_speed")),
        NamedExpr("dx_prev1", diff("plate_x", "prev1_plate_x")),
        NamedExpr("dz_prev1", diff("plate_z", "prev1_plate_z")),
        NamedExpr("streak_pos", Case(((IsNull(C(type_field), True), C("__ta_streak")),), None)),
        NamedExpr("history_complete", Case(((Binary(C("__ta_unknown_so_far"), "=", Literal(0)), Literal(1)),), Literal(0))),
    ]
    return Project(n3, tuple(fields), PITCH_GRAIN)


# --- outcome categories ------------------------------------------------------------
IN_PLAY_OUTS = ("field_out", "force_out", "grounded_into_double_play", "double_play", "sac_fly",
                "sac_fly_double_play", "fielders_choice_out", "triple_play")
IN_PLAY_OTHER = ("field_error", "fielders_choice", "catcher_interf")


def outcome_category_expr():
    d = C("description"); e = C("events")
    def lit_in(col, values): return InList(col, tuple(Literal(v) for v in values))
    in_play = Binary(d, "=", Literal("hit_into_play"))
    def play(event_values, name): return (Boolean("and", (in_play, lit_in(e, event_values))), Literal(name))
    return Case((
        (lit_in(d, ("foul_bunt", "missed_bunt")), Literal("bunt")),
        (Boolean("and", (in_play, Boolean("or", (lit_in(e, ("sac_bunt", "sac_bunt_double_play")),
                                                   Binary(C("des"), "LIKE", Literal("%bunt%")),
                                                   Binary(C("des"), "LIKE", Literal("%Bunt%")))))), Literal("bunt")),
        (lit_in(d, ("ball", "blocked_ball")), Literal("ball")),
        (Binary(d, "=", Literal("called_strike")), Literal("called_strike")),
        (lit_in(d, ("swinging_strike", "swinging_strike_blocked")), Literal("whiff")),
        (Binary(d, "=", Literal("foul_tip")), Literal("foul_tip")),
        (Binary(d, "=", Literal("foul")), Literal("foul")),
        (Binary(d, "=", Literal("hit_by_pitch")), Literal("hit_by_pitch")),
        play(("home_run",), "in_play_home_run"),
        play(("triple",), "in_play_triple"),
        play(("double",), "in_play_double"),
        play(("single",), "in_play_single"),
        play(IN_PLAY_OUTS, "in_play_out"),
        play(IN_PLAY_OTHER, "in_play_error_other"),
    ), Literal("unclassified"))


# --- handedness mirror (derived "same side / opposite side" view) ------------------
ZONE_MIRROR = {1: 3, 3: 1, 4: 6, 6: 4, 7: 9, 9: 7, 11: 12, 12: 11, 13: 14, 14: 13}
NEGATE_FIELDS = ("plate_x", "pfx_x", "release_pos_x", "vx0", "ax")


def mirror_to_right_handed_pitcher(source, carry_fields, *, negate_fields=NEGATE_FIELDS,
                                   axis_field="spin_axis", zone_field="zone"):
    """Mirror every row thrown by a left-handed pitcher into the right-handed-pitcher frame.

    Adds `mirrored` (1 if the row was mirrored) and `frame_group` ('same_side' / 'opposite_side').
    Fields listed in negate_fields/axis_field/zone_field are replaced under the same name.
    """
    is_left = Binary(C("p_throws"), "=", Literal("L"))
    special = set(negate_fields) | {axis_field, zone_field}
    fields = [NamedExpr(f, C(f)) for f in carry_fields if f not in special]
    for f in negate_fields:
        fields.append(NamedExpr(f, Case(((is_left, Binary(Literal(0.0), "-", C(f))),), C(f))))
    fields.append(NamedExpr(axis_field, Case(((Boolean("and", (is_left, Binary(C(axis_field), ">", Literal(0)))),
                                               Binary(Literal(360.0), "-", C(axis_field))),), C(axis_field))))
    fields.append(NamedExpr(zone_field, Case(tuple(
        (Boolean("and", (is_left, Binary(C(zone_field), "=", Literal(k)))), Literal(v)) for k, v in ZONE_MIRROR.items()
    ), C(zone_field))))
    known = Boolean("and", (IsNull(C("p_throws"), True), IsNull(C("stand"), True)))
    fields.append(NamedExpr("mirrored", Case(((is_left, Literal(1)),), Literal(0))))
    fields.append(NamedExpr("frame_group", Case((
        (Boolean("and", (known, Binary(C("p_throws"), "=", C("stand")))), Literal("same_side")),
        (Boolean("and", (known, Binary(C("p_throws"), "!=", C("stand")))), Literal("opposite_side")),
    ), None)))
    return Project(source, tuple(fields), PITCH_GRAIN)

```

---

## 附錄 B：測試用假資料與預期答案

`tests/seq_fixtures.py` 的 `make_seq_db(path)` 建立 `pitches`（欄位：`pitch_uid TEXT PRIMARY KEY, game_pk, at_bat_number, pitch_number, game_year, game_type, game_date, pitcher, batter, pitch_type, description, events, des, release_speed, plate_x, plate_z, p_throws, stand, _ingested_at`）與 `settings(key, value, updated_at)`（一列 `data_revision = 'rev-1'`）。所有列：`game_year=2024, game_type='R', game_date='2024-05-01', pitcher=10, batter=100+ab, p_throws='R', stand='R', _ingested_at='2026-01-01T00:00:00+00:00'`；`pitch_uid = "<game>:<ab>:<pitch_number>"`；`events` 與 `des` 只放在打席最後一球。

| 打席（game:ab） | 逐球內容（球種, description, 球速, plate_x, plate_z） | 最後一球 `events` / `des` |
|---|---|---|
| 1:1 | (FF,ball,92,0.10,2.50) (FF,foul,93,0.20,2.60) (SL,swinging_strike,85,−0.30,1.50) (SL,called_strike,86,−0.10,1.60) (SL,foul,84,0.00,1.40) (CH,hit_into_play,88,0.05,2.00) | `field_out` / `X grounds out to shortstop` |
| 1:2 | (SI,ball,94,0.5,2.0) (NULL,automatic_ball,NULL,NULL,NULL) (SI,foul,95,0.4,2.1) (SI,called_strike,95.5,0.3,2.2) | — |
| 1:3 | (FF,ball,93,0,3.0) (FF,foul_bunt,93,0,2.0) | — |
| 1:4 | (CU,ball,80,0,1.0) (CU,hit_into_play,81,0,2.0) | `sac_bunt` / `Smith sacrifice bunt, pitcher to first` |
| 1:5 | (FF,hit_into_play,92,0,2.0) | `single` / `Jones bunts a ground ball single to third` |
| 1:6 | (SL,hit_into_play,85,0,2.0) | `field_out` / `Lee Bunt Ground Out` |
| 1:7 | (FF,ball,92,0,2.0) (NULL,ball,NULL,0.1,2.0) (FF,called_strike,92,0,2.0) | — |
| 2:1 | (FF,hit_into_play,93,0,2.0) | `home_run` / `Doe homers` |

（`pitch_number` 依序為 1、2、3…，自動好壞球那一列佔 `pitch_number=2`。）

**管線**：`apply_exclusions(Source("pitches", PITCH_GRAIN))`（預設 `exclude_plate_appearance`）→ `sequence_features(..., carry_fields=("pitch_uid","game_pk","at_bat_number","pitch_number","pitch_type","description","release_speed","plate_x","plate_z"), memory=2)`，依 `(game_pk, at_bat_number, pitch_number)` 排序。

**預期保留 13 列**（打席 1:3、1:4、1:5、1:6 因短打被整個排除；1:2 的自動好壞球被排除）：

| 列 | pitch_index_in_pa | prev1_pitch_type | history_key | streak_pos | history_complete | speed_diff_prev1 | dx_prev1 |
|---|---|---|---|---|---|---|---|
| 1:1:1 | 1 | NULL | `^>^` | 1 | 1 | NULL | NULL |
| 1:1:2 | 2 | FF | `^>FF` | 2 | 1 | 1.0 | 0.1 |
| 1:1:3 | 3 | FF | `FF>FF` | 1 | 1 | −8.0 | −0.5 |
| 1:1:4 | 4 | SL | `FF>SL` | 2 | 1 | 1.0 | 0.2 |
| 1:1:5 | 5 | SL | `SL>SL` | 3 | 1 | −2.0 | 0.1 |
| 1:1:6 | 6 | SL | `SL>SL` | 1 | 1 | 4.0 | 0.05 |
| 1:2:1 | 1 | NULL | `^>^` | 1 | 1 | NULL | NULL |
| 1:2:3 | 2 | SI | `^>SI` | 2 | 1 | 1.0 | −0.1 |
| 1:2:4 | 3 | SI | `SI>SI` | 3 | 1 | 0.5 | −0.1 |
| 1:7:1 | 1 | NULL | `^>^` | 1 | 1 | NULL | NULL |
| 1:7:2 | 2 | FF | `^>FF` | NULL | 0 | NULL | 0.1 |
| 1:7:3 | 3 | NULL | `FF>?` | 1 | 0 | NULL | −0.1 |
| 2:1:1 | 1 | NULL | `^>^` | 1 | 1 | NULL | NULL |

浮點數用 `pytest.approx` 比較（例如 `dx_prev1` 會出現 `0.19999999999999998`）。

**結果類別**（對全部 20 列，不排除，`outcome_category_expr()`）：1:1:1 `ball`、1:1:2 `foul`、1:1:3 `whiff`、1:1:4 `called_strike`、1:1:5 `foul`、1:1:6 `in_play_out`、1:2:1 `ball`、1:2:2 `unclassified`（自動好壞球）、1:2:3 `foul`、1:2:4 `called_strike`、1:3:1 `ball`、1:3:2 `bunt`、1:4:1 `ball`、1:4:2 `bunt`、1:5:1 `bunt`、1:6:1 `bunt`、1:7:1 `ball`、1:7:2 `ball`、1:7:3 `called_strike`、2:1:1 `in_play_home_run`。

**鏡像資料**（另一個小資料庫，欄位 `pitch_uid, p_throws, stand, plate_x, pfx_x, release_pos_x, vx0, ax, spin_axis, zone, plate_z, _ingested_at`）：

| uid | p_throws | stand | plate_x | pfx_x | release_pos_x | vx0 | ax | spin_axis | zone | plate_z |
|---|---|---|---|---|---|---|---|---|---|---|
| a | R | R | 0.5 | −1.0 | −2.0 | 3.0 | −4.0 | 200 | 1 | 2.5 |
| b | L | L | −0.5 | 1.0 | 2.0 | −3.0 | 4.0 | 160 | 3 | 2.5 |
| c | R | L | 0.2 | 0.0 | −1.5 | 1.0 | 2.0 | 0 | 14 | 1.0 |
| d | L | R | −0.2 | 0.0 | 1.5 | −1.0 | −2.0 | 0 | 13 | 1.0 |
| e | NULL | R | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 | 10 | 5 | 2.0 |

鏡像後預期：`a` 與 `b` 的 `plate_x, pfx_x, release_pos_x, vx0, ax, spin_axis, zone` 完全相同（0.5、−1.0、−2.0、3.0、−4.0、200、1），`mirrored` 分別為 0、1，`frame_group` 都是 `same_side`；`c` 與 `d` 相同（0.2、0.0、−1.5、1.0、2.0、0、14），`frame_group` 都是 `opposite_side`；`e` 全部不變，`mirrored=0`、`frame_group IS NULL`。

---

## 附錄 C：範例 JSON

**研究設定（`research run --config-file`）— `analysis_payload`：**

```json
{
  "payload": {
    "mode": "basic",
    "filters": [{"field": "game_year", "op": "eq", "value": 2024}],
    "group_by": ["pitch_type"],
    "metrics": [{"function": "count"}, {"function": "avg", "field": "release_speed"}],
    "limit": 200
  }
}
```

**研究設定 — `data_profile`：**

```json
{"scope": {"game_years": [2023, 2024, 2025, 2026], "game_types": ["R"]}}
```

**正規化後儲存的 `config`（`data_profile`，節錄）：**

```json
{
  "sections": ["coverage_by_outcome", "description_values", "…"],
  "tracked_fields": ["launch_speed", "…"],
  "scope": {"game_types": ["R"], "game_years": [2023, 2024, 2025, 2026]}
}
```

**`data_fingerprint`：**

```json
{
  "data_revision": "…",
  "total_rows": 2900000,
  "scope_fingerprint": "9c1f…",
  "seasons": [{"game_year": 2023, "game_type": "R", "rows": 700000, "min_date": "2023-03-30", "max_date": "2023-10-01", "max_ingested_at": "2026-10-06T…+00:00"}]
}
```

---

## 附錄 D：現有程式索引（以函式／類別名稱查找，行號會變）

| 需要時看 | 位置 |
|---|---|
| 快取鍵、`read_data_revision`、`canonical_json`、`AnalysisStateStore`（資料庫建構模式） | `src/treepolo_mlb_data/analysis_state.py` |
| 進度工作（`start_analysis_job`、`update_analysis_job`、`finish_analysis_job`） | `src/treepolo_mlb_data/analysis_jobs.py` |
| `AppConfig` 與 `load_config` | `src/treepolo_mlb_data/config.py` |
| HTTP 路由（`_Handler.do_GET/do_POST/do_DELETE`、`_json`、`_read_json`、`_error`、`_static`、`serve`） | `src/treepolo_mlb_data/webapp.py` |
| Stage 4D 以猴子補丁擴充 `webapp` 的方式（`install(webapp_module)`、`stage4d_static`） | `src/treepolo_mlb_data/stage4d.py`（檔尾） |
| 內容定址快照的寫入／讀取 | `src/treepolo_mlb_data/stage4d_saved_v2.py` |
| 分析引擎入口、後端選擇與退回 | `src/treepolo_mlb_data/analysis/engine.py` |
| 運算子白名單、視窗函式編譯 | `src/treepolo_mlb_data/analysis/compiler.py` |
| 分析樹節點定義、`PITCH_GRAIN` | `src/treepolo_mlb_data/analysis/model.py` |
| 序列化 | `src/treepolo_mlb_data/analysis/codec.py` |
| 欄位型別（`REAL_COLUMNS`、`INTEGER_COLUMNS`） | `src/treepolo_mlb_data/schema.py` |
| 資料同步請求參數（`hfGT`） | `src/treepolo_mlb_data/savant.py` |
| `pitches` 表建構、`_row_hash`、`_ingested_at` | `src/treepolo_mlb_data/storage.py` |
| DuckDB 鏡像建立（要求 `_ingested_at`、`settings`） | `src/treepolo_mlb_data/duckdb_mirror.py` |
| CLI 子指令定義與 `main` | `src/treepolo_mlb_data/cli.py` |
| 前端新增頁面的範例 | `src/treepolo_mlb_data/web_static/cluster-comparison-page.js`、`panel-activation.js` |
| 測試寫法範例（含 DuckDB 後端） | `tests/test_analysis_state.py`、`tests/test_duckdb_backend.py`、`tests/test_analysis_engine.py` |

---

## 附錄 E：P2 完成紀錄（2026-10-06）

與本文件原先寫法不同、或實作時才發現的事：

1. **短打與非投球的描述值**：真實資料出現兩個文件沒預料到的值。`bunt_foul_tip`（短打擦棒）已加入短打規則（`BUNT_DESCRIPTIONS`）；`swinging_pitchout` 已加入非投球清單。兩者都落在使用者已決定的「排除所有短打、排除 pitchout」範圍內，未涉及新的決策。
2. **`data_profile` 方法版本升為 2**：因為短打規則改變了 `bunt_share` 的結果。版本號在 `run_key` 內，所以舊結果不會被重用——這也驗證了「方法邏輯改變就要升版本」的規則。
3. **鏡像判定準則微調**：文件 T2.8 第 5 點用「兩種慣用手合併後的平均」判定，遇到平均值接近 0 的欄位（`plate_x`）與站位比例不同的欄位（`api_break_x_batter_in`）會誤判為「要問使用者」。改用更直接的檢查：鏡像後「右投對右打 ≈ −左投對左打」「右投對左打 ≈ −左投對右打」。結果與原先已決定的規則一致，並得出其餘欄位的規則（規劃文件 2.6）。
4. **`LIKE` 在兩個後端的差異**已寫成測試（`tests/test_analysis_string_ops.py`）：SQLite 不分大小寫、DuckDB 分，所以規則同時列 `%bunt%` 與 `%Bunt%`。
5. **兩個後端的 NULL 排序位置不同**（SQLite 最前、DuckDB 最後）。測試與結果比較都用明確的排序鍵。
6. **DuckDB 測試必須斷言 `result.backend == "duckdb"`**，因為鏡像建立失敗時引擎會靜默退回 SQLite（`tests/seq_fixtures.py` 的 `run_both` 已內建）。
7. `research/scope.py` 的 `scope_where_sql` 公開（`data_profile` 使用）。
8. **未做**：`hc_x` 的鏡像（需先確認中心點）；網頁介面不提供逐球視窗操作（P3 的方法頁面直接在後端使用建構函式）。
