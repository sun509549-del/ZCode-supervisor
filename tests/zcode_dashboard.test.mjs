import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  buildCodexArgs,
  buildRoutingObjective,
  buildWorkerObjective,
  dashboardJobMatchesRun,
  isoTime,
  normalizeStatus,
  parseAllowedFiles,
  parseCodexUsage,
  renderPlanMarkdown,
  runProcessCapture,
  summarize,
  validateCodexPlan,
  validateCodexReview,
  validateValidationCommand,
  writeDeliveryArtifact,
} from "../tools/zcode_dashboard/server.mjs";

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
    measured_codex_stages: 0,
    codex_total_tokens: null,
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
  assert.equal(normalizeStatus({ status: "planning" }), "planning");
  assert.equal(normalizeStatus({ status: "reviewing" }), "reviewing");
  assert.equal(normalizeStatus({ status: "needs_fix" }), "needs_fix");
  assert.equal(normalizeStatus({ status: "run_timeout", timed_out: true }), "timeout");
  assert.equal(normalizeStatus({ ok: false }), "failed");
});

test("Codex JSONL usage remains separate and does not double-count reasoning", () => {
  const usage = parseCodexUsage([
    JSON.stringify({ type: "turn.started" }),
    JSON.stringify({ type: "turn.completed", usage: { input_tokens: 100, cached_input_tokens: 40, output_tokens: 25, reasoning_output_tokens: 10 } }),
  ].join("\n"));
  assert.deepEqual(usage, {
    available: true,
    total: 125,
    input: 100,
    cached_input: 40,
    output: 25,
    reasoning: 10,
    unavailable_reason: null,
  });
  assert.equal(parseCodexUsage("not json").available, false);
  assert.deepEqual(parseCodexUsage(JSON.stringify({ type: "turn.completed", usage: { input_tokens: 100 } })), {
    available: false,
    total: null,
    input: null,
    cached_input: null,
    output: null,
    reasoning: null,
    unavailable_reason: "codex_usage_incomplete",
  });
});

test("Codex plan validation locks worker scope and renders a handoff", () => {
  const plan = validateCodexPlan({
    title: "Fix order rounding",
    summary: "Keep the existing API and correct decimal rounding.",
    allowed_files: ["src\\order.js", "tests/order.test.js"],
    steps: ["Add a failing regression case", "Fix the rounding implementation"],
    acceptance_criteria: ["The regression test passes"],
    validation_command: "npm test -- order.test.js",
    risks: ["Currency precision"],
    exclusions: ["No API changes"],
  });
  assert.deepEqual(plan.allowed_files, ["src/order.js", "tests/order.test.js"]);
  assert.match(renderPlanMarkdown(plan), /## 实施步骤/);
  assert.match(renderPlanMarkdown(plan), /npm test -- order\.test\.js/);
  assert.throws(() => validateCodexPlan({ ...plan, allowed_files: [".ai/tasks/current/PLAN.md"] }), /不能让 ZCode 修改/);
  assert.throws(() => validateCodexPlan({ ...plan, allowed_files: ["./.codex/zcode-routing.json"] }), /不能让 ZCode 修改/);
  assert.throws(() => validateCodexPlan({ ...plan, allowed_files: [".env"] }), /敏感路径/);
  assert.throws(() => validateCodexPlan({ ...plan, acceptance_criteria: Array(12).fill("x".repeat(200)) }), /总长度/);
});

test("Codex stages are forced into read-only ephemeral structured execution", () => {
  const args = buildCodexArgs({ workspace: "C:\\repo", schemaPath: "C:\\schema.json", outputPath: "C:\\result.json" });
  assert.deepEqual(args.slice(0, 6), ["exec", "--sandbox", "read-only", "--ephemeral", "--json", "--output-schema"]);
  assert.ok(args.includes("--output-last-message"));
  assert.ok(args.includes("--skip-git-repo-check"));
  assert.equal(args.at(-1), "-");
});

test("Codex validation commands cannot smuggle shell or inline scripts", () => {
  assert.equal(validateValidationCommand("python -m unittest tests.test_feature"), "python -m unittest tests.test_feature");
  assert.throws(() => validateValidationCommand("npm test && del important.txt"), /shell 控制符/);
  assert.throws(() => validateValidationCommand("pwsh -Command Remove-Item file"), /内联脚本/);
  assert.throws(() => validateValidationCommand("pwsh -EncodedCommand AAAA"), /内联脚本/);
  assert.throws(() => validateValidationCommand("node -e process.exit(0)"), /内联脚本/);
  assert.throws(() => validateValidationCommand("node --eval process.exit(0)"), /内联脚本/);
  assert.throws(() => validateValidationCommand("node \"-e\" \"process.exit(0)\""), /内联脚本/);
  assert.throws(() => validateValidationCommand("python -cprint(1)"), /内联脚本/);
  assert.throws(() => validateValidationCommand("python \"-c\" \"print(1)\""), /内联脚本/);
  assert.throws(() => validateValidationCommand("py -c \"print(1)\""), /内联脚本/);
});

test("Codex review cannot pass with incomplete or failed acceptance evidence", () => {
  const base = {
    verdict: "PASS",
    summary: "Ready",
    findings: [],
    criteria: [{ criterion: "Tests pass", status: "pass", evidence: "node --test passed" }],
    recommended_fixes: [],
  };
  assert.equal(validateCodexReview(base, ["Tests pass"]).verdict, "PASS");
  assert.equal(validateCodexReview({ ...base, criteria: [] }, ["Tests pass"]).verdict, "NEED_FIX");
  assert.equal(validateCodexReview({ ...base, criteria: [{ ...base.criteria[0], status: "fail" }] }, ["Tests pass"]).verdict, "NEED_FIX");
  assert.equal(validateCodexReview({ ...base, criteria: [base.criteria[0], base.criteria[0]] }, ["Tests pass", "Lint passes"]).verdict, "NEED_FIX");
  assert.equal(validateCodexReview({
    ...base,
    findings: [{ severity: "high", title: "Regression", file: "app.js", line: 4, details: "Existing flow breaks" }],
  }, ["Tests pass"]).verdict, "NEED_FIX");
});

test("dashboard preserves missing timestamps instead of converting them to Unix epoch", () => {
  assert.equal(isoTime(null), null);
  assert.equal(isoTime(undefined), null);
  assert.equal(isoTime(""), null);
  assert.equal(isoTime(0), "1970-01-01T00:00:00.000Z");
});

test("worker handoff stays compact and links live runs by stable objective", () => {
  const job = {
    id: "dashboard-123",
    objective: "Add a production database migration",
    artifacts: { task: ".ai/tasks/dashboard-123/TASK.md", plan: ".ai/tasks/dashboard-123/PLAN.md", acceptance: ".ai/tasks/dashboard-123/ACCEPTANCE.md" },
  };
  const workerObjective = buildWorkerObjective(job);
  assert.doesNotMatch(workerObjective, /production database migration/);
  assert.match(workerObjective, /dashboard-123/);
  assert.equal(dashboardJobMatchesRun({ ...job, worker_objective: workerObjective, started_at: "2026-01-01T00:00:00Z" }, "C:\\repo\\run.json", workerObjective, Date.now()), true);
});

test("routing preflight includes user constraints and requested task class", () => {
  const objective = buildRoutingObjective("Fix the failing test", "Also migrate the production database", "production-gate");
  assert.match(objective, /Fix the failing test/);
  assert.match(objective, /migrate the production database/);
  assert.match(objective, /production-gate/);
});

test("delivery artifact uses the persisted compact supervisor run evidence", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "zcode-dashboard-delivery-"));
  try {
    const artifactDirectory = join(workspace, ".ai", "tasks", "dashboard-123");
    const runPath = join(workspace, ".codex", "zcode", "runs", "worker.zcode.json");
    await mkdir(artifactDirectory, { recursive: true });
    await mkdir(join(workspace, ".codex", "zcode", "runs"), { recursive: true });
    await writeFile(runPath, JSON.stringify({
      audit: {
        changed_files: { added: ["src/new.js"], modified: ["src/app.js"], deleted: [] },
        validation: { ok: true },
      },
    }));
    const job = {
      provider: "workbuddy",
      model: "deepseek-v4.1-flash",
      run_path: runPath,
      validation: "node --test",
      artifacts: { directory: ".ai/tasks/dashboard-123" },
    };
    await writeDeliveryArtifact(workspace, job, {
      ok: true,
      zcode_attempted: true,
      zcode_ok: true,
      route_used: "zcode_cli",
      run_result_summary: { validation_ok: true, changed_count: 2 },
    });
    const delivery = await readFile(join(artifactDirectory, "DELIVERY.md"), "utf8");
    assert.match(delivery, /src\/new\.js/);
    assert.match(delivery, /src\/app\.js/);
    assert.match(delivery, /结果：通过/);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("timed out stages terminate their descendant process tree", async () => {
  const directory = await mkdtemp(join(tmpdir(), "zcode-dashboard-tree-"));
  const pidPath = join(directory, "grandchild.pid");
  let grandchildPid = null;
  try {
    const parentCode = [
      "const { spawn } = require('node:child_process');",
      "const { writeFileSync } = require('node:fs');",
      "const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: process.platform !== 'win32', stdio: 'ignore', windowsHide: true });",
      `writeFileSync(${JSON.stringify(pidPath)}, String(child.pid));`,
      "setInterval(() => {}, 1000);",
    ].join(" ");
    await assert.rejects(
      runProcessCapture(process.execPath, ["-e", parentCode], { timeout: 800 }),
      (error) => error?.code === "PROCESS_TIMEOUT",
    );
    grandchildPid = Number(await readFile(pidPath, "utf8"));
    let alive = true;
    for (let attempt = 0; attempt < 20 && alive; attempt += 1) {
      try { process.kill(grandchildPid, 0); }
      catch { alive = false; }
      if (alive) await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.equal(alive, false, `descendant process ${grandchildPid} survived the timeout`);
  } finally {
    if (grandchildPid) { try { process.kill(grandchildPid, "SIGKILL"); } catch { /* already stopped */ } }
    await rm(directory, { recursive: true, force: true });
  }
});
