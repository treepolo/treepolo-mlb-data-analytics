# CPBL Trackman source contract

Status: Stage 1 source-discovery contract for the CPBL dataset.

## Scope

The CPBL dataset is sourced independently from Baseball Savant and must remain operationally isolated from the MLB dataset. The public advanced-statistics site is a Next.js application backed by `https://stats.cpbl.com.tw/api/proxy/v1/...` endpoints.

Confirmed public endpoint families used by the site and existing independent reverse-engineering work:

- `GET /v1/games/schedule/<YYYY-MM-DD>` — games for a date.
- `GET /v1/games/<gameId>` — one game, including play-by-play and Trackman payloads when advanced tracking exists.
- `GET /v1/leaderboards/pr-table`
- `GET /v1/leaderboards/batted-ball`
- `GET /v1/leaderboards/exit-velocity`
- `GET /v1/leaderboards/pitch-tracking`
- `GET /v1/players/autocomplete`

The acquisition path for pitch-grain data is therefore date -> schedule -> game id -> game detail -> `LiveLog[]` -> `Trackman`.

## Game identifiers and competition kinds

Observed 2026 identifiers include:

- first-division regular season: `2026-A-<game number>`
- second-division regular season: `2026-D-<game number>`

The site also exposes separate competition kinds for first-division postseason/championship and second-division championship. Code must preserve the upstream game id and kind code rather than infer competition solely from the id string.

## Trackman availability is nullable by design

A valid scheduled or completed CPBL game may have no advanced tracking. The schedule/game payload exposes a `SkipTrackman`-style signal in known responses, and the public site can show a valid play-by-play game with an explicit 'no advanced data' state.

Consequences:

1. A game without Trackman is a successful acquisition with zero tracked pitches, not a failed sync.
2. Game metadata and the raw game payload are still archived.
3. Missing Trackman fields on individual pitches remain NULL; ingestion must never fabricate measurements.
4. Coverage reporting belongs to acceptance/data-quality tooling, not the parser.

## Known pitch-level shape

For pitch-like `LiveLog` entries, known top-level fields include:

- inning / count / outs / pitch count/order information
- pitcher account id and name
- hitter account id and name
- batting-action/result text
- ball/strike/score flags
- content/play description

Known `Trackman` subtrees and fields include:

### Play tags

`Trackman.Play.PitchTag`

- `PitchCall`
- `AutoPitchType`
- `TaggedPitchType`

The CPBL public UI currently coarsens pitch types into broad categories such as fastball/change-of-speed, but these raw fields must be retained independently. `AutoPitchType`, `TaggedPitchType`, and the canonical analysis `pitch_type` are separate concepts.

### Pitch release

`Trackman.Pitch.Release`

- `RelSpeed`
- `SpinRate`
- `Extension`
- `RelHeight`
- `RelSide`

### Plate-crossing / location

`Trackman.Pitch.Location`

- `PlateLocSide`
- `PlateLocHeight`
- `ZoneSpeed`
- `HorzApprAngle`
- `VertApprAngle`

### Trajectory

Known responses contain polynomial pitch-trajectory data below a `Trackman.Pitch.Flight.PolyFit.PitchTrajectory`-like path, with X/Y/Z coefficient vectors. These arrays must be preserved losslessly in the normalized store as JSON text unless/until a typed trajectory table is introduced.

### Batted-ball data

`Trackman.Hit` includes known launch/contact/landing values such as:

- exit speed
- launch angle
- direction
- hit spin rate
- contact X/Y/Z
- landing bearing
- landing distance
- hang time

## Canonical analysis contract

The CPBL database owns its own `pitches` table. The following canonical fields are created inside the CPBL database so that the shared relational analysis engine can operate without knowing the source provider:

- `pitch_uid`
- `game_pk`
- `game_date`
- `game_year`
- `at_bat_number`
- `pitch_number`
- `pitcher`
- `batter`
- `pitch_type`
- `description`
- `inning`
- `balls`
- `strikes`
- `outs_when_up`
- `release_speed`
- `release_spin_rate`
- `release_extension`
- `release_pos_x`
- `release_pos_z`
- `plate_x`
- `plate_z`

Provider-native CPBL/Trackman fields are retained alongside the canonical fields, including at least `auto_pitch_type`, `tagged_pitch_type`, `pitch_call`, `zone_speed_kph`, `horz_appr_angle`, `vert_appr_angle`, trajectory coefficients, and available batted-ball measurements.

Canonical fields are aliases/normalizations for shared analysis only. They do not replace or erase the raw provider fields.

## Stable pitch identity and plate appearances

Preferred natural identity is game id + stable plate-appearance sequence + stable pitch sequence. CPBL acquisition must derive deterministic per-game sequence numbers when the upstream response does not expose identifiers with the same semantics as Statcast `at_bat_number` / `pitch_number`.

Rules:

1. Preserve any upstream stable sequence/id fields when present.
2. Derive PA sequence deterministically from ordered `LiveLog` when necessary.
3. Derive pitch sequence within PA deterministically when necessary.
4. Build `pitch_uid` from the resulting canonical natural key.
5. Store source ordering fields as separate native columns for auditability.
6. Reprocessing the same raw game payload must reproduce the same canonical identifiers.

## Units

Source-native units must be retained/documented. Canonical field names do not imply unit conversion unless explicitly implemented. Current known CPBL presentation uses km/h for pitch/exit speed, while existing MLB Statcast fields use mph. The first CPBL implementation therefore keeps explicit provider-native `*_kph` columns and only populates generic canonical speed aliases when the dataset profile declares their unit.

Cross-league queries are out of scope: MLB and CPBL data are never unioned implicitly.

## Raw archive requirements

Every successfully fetched schedule/game response is archived before normalization. Raw game JSON is the rebuild source of truth for CPBL and must support:

- idempotent re-ingestion
- parser/schema upgrades without refetching
- provenance/audit of canonical values
- recovery of newly discovered Trackman fields

## Stage 1 acceptance decisions

- CPBL and MLB acquisition paths are separate.
- A game without Trackman is valid and is not a sync error.
- The full game JSON is the pitch-grain source of truth.
- `AutoPitchType` and `TaggedPitchType` remain independently queryable.
- Canonical analysis fields are additive aliases, not destructive conversion.
- Provider-native Trackman fields are preserved.
- Deterministic canonical PA/pitch identifiers are mandatory before sequence/follow-event modes are enabled.
- Stage 7 will perform exhaustive full-season value enumeration, field coverage, and performance acceptance; Stages 2-6 may proceed using this source contract.
