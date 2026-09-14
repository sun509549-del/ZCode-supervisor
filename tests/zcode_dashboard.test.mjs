import test from "node:test";
import assert from "node:assert/strict";

import { normalizeStatus, parseAllowedFiles, summarize } from "../tools/zcode_dashboard/server.mjs";

test("dashboard summary keeps unavailable token usage distinct from zero", () => {
  const tasks = [
    { status: "success", tokens: { available: true, total: 120 }, duration_ms: 1000 },
    { status: "failed", tokens: { available: false, total: null }, duration_ms: 3000 },
    { status: "running", tokens: { available: false, total: null }, duration_ms: 500 },
  ];

  assert.deepEqual(summarize(tasks), {
    total_tasks: 3,
    running_tasks: 1,
    successful_tasks: 1,
    success_rate: 0.5,
    measured_token_tasks: 1,
    total_tokens: 120,
    total_duration_ms: 4500,
    average_duration_ms: 1500,
  });
  assert.equal(summarize([{ status: "running", tokens: { available: false }, duration_ms: null }]).total_tokens, null);
});

test("dashboard allowed-file parser normalizes paths and rejects sensitive or escaping paths", () => {
  assert.deepEqual(parseAllowedFiles("src\\app.js\ntests/app.test.js\nsrc/app.js"), ["src/app.js", "tests/app.test.js"]);
  assert.throws(() => parseAllowedFiles("../outside.js"), /相对路径/);
  assert.throws(() => parseAllowedFiles(".env.production"), /敏感路径/);
  assert.throws(() => parseAllowedFiles([]), /1–20/);
});

test("dashboard normalizes terminal and active task states", () => {
  assert.equal(normalizeStatus({ status: "success", ok: true }), "success");
  assert.equal(normalizeStatus({ status: "run_timeout", timed_out: true }), "timeout");
  assert.equal(normalizeStatus({ ok: false }), "failed");
});

