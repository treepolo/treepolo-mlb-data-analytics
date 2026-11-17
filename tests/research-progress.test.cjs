const assert = require("node:assert/strict");
const path = require("node:path");
const progress = require(path.join(__dirname, "..", "src", "treepolo_mlb_data", "web_static", "research-progress.js"));

assert.equal(progress.minutes(5), "5 秒");
assert.equal(progress.minutes(60), "1 分");
assert.equal(progress.minutes(83), "1 分 23 秒");
assert.equal(progress.minutes(null), "—");

const running = progress.describe({ mode: "research:pitch_policy", status: "running", percentage: 42.4, elapsed_seconds: 100, eta_seconds: 136, detail: "group LvL: league engine by pitcher 100/262" });
assert.equal(running.title, "決策引擎");
assert.equal(running.width, 42.4);
assert.equal(running.percentText, "42%");
assert.equal(running.elapsedText, "已用 1 分 40 秒");
assert.equal(running.etaText, "預估再 2 分 16 秒");
assert.equal(running.state, "running");

const early = progress.describe({ mode: "research:policy_score", status: "running", percentage: 1.2, elapsed_seconds: 3, eta_seconds: null });
assert.equal(early.etaText, "預估時間計算中");                         // too early to say how long it will take
assert.equal(early.percentText, "1.2%");

const estimated = progress.describe({ mode: "research:outcome_model_train", status: "running", percentage: 50, elapsed_seconds: 40, eta_seconds: 40, estimated: true });
assert.ok(estimated.etaText.includes("依先前同類研究估計"));          // a bar that follows earlier runs says so

const unknown = progress.describe({ mode: "research:policy_check", status: "running", percentage: null, elapsed_seconds: 2 });
assert.equal(unknown.indeterminate, true);
assert.equal(unknown.percentText, "進度計算中");

assert.equal(progress.describe({ mode: "research:policy_check", status: "success", percentage: 100, elapsed_seconds: 7 }).etaText, "完成");
assert.equal(progress.describe({ mode: "research:policy_check", status: "failed", percentage: 30, elapsed_seconds: 7 }).state, "failed");
console.log("research-progress tests passed");
