const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

(async () => {
  global.window = globalThis;
  const calls = [];
  window.fetch = (input, init = {}) => new Promise((resolve, reject) => {
    const call = {input, init, resolve, reject};
    calls.push(call);
    init.signal?.addEventListener("abort", () => {
      reject(new DOMException("aborted", "AbortError"));
    }, {once:true});
  });

  const modulePath = path.join(
    __dirname,
    "..",
    "src",
    "treepolo_mlb_data",
    "web_static",
    "stage4d-latest-request.js",
  );
  vm.runInThisContext(fs.readFileSync(modulePath, "utf8"), {filename: modulePath});

  const request = marker => window.fetch("/api/visualization/data", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({marker}),
  });

  const a = request("A");
  const b = request("B");
  const c = request("C");

  assert.equal(calls.length, 3);
  assert.equal(calls[0].init.signal.aborted, true, "A must be aborted by B");
  assert.equal(calls[1].init.signal.aborted, true, "B must be aborted by C");
  assert.equal(calls[2].init.signal.aborted, false, "C must remain active");

  calls[2].resolve(new Response(JSON.stringify({marker: "C"}), {
    status: 200,
    headers: {"Content-Type": "application/json"},
  }));

  const responses = await Promise.all([a, b, c]);
  const bodies = await Promise.all(responses.map(response => response.json()));
  assert.deepEqual(bodies.map(body => body.marker), ["C", "C", "C"]);

  console.log("Stage 4D latest-request-wins race test passed");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
