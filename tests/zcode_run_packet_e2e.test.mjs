import test from "node:test";
import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { createHash } from "node:crypto";
import http from "node:http";
import { mkdtemp, writeFile, mkdir, readFile, chmod, symlink, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";

const ROOT = resolve(import.meta.dirname, "..");
const ZCODECTL = resolve(ROOT, "tools", "zcode_control", "zcodectl.mjs");
const SUPERVISOR = resolve(ROOT, "tools", "zcode_supervisor", "zcode_supervisor.py");
const execFile = promisify(execFileCallback);
const GIT_CONTROL_ENV_VARS = [
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_INDEX_FILE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_COMMON_DIR",
  "GIT_NAMESPACE",
  "GIT_CEILING_DIRECTORIES",
];

async function makeWorkspace(initialText, validationText, options = {}) {
  const root = await mkdtemp(join(tmpdir(), "zcode-run-packet-e2e-"));
  const workspace = join(root, "workspace");
  await mkdir(join(workspace, "src"), { recursive: true });
  await writeFile(join(workspace, "src", "app.js"), initialText);
  if (options.visionImage) {
    await mkdir(join(workspace, "screenshots"), { recursive: true });
    await writeFile(
      join(workspace, "screenshots", "state.png"),
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    );
  }
  await writeFile(
    join(workspace, "check.py"),
    [
      "from pathlib import Path",
      `raise SystemExit(0 if Path('src/app.js').read_text() == ${JSON.stringify(validationText)} else 1)`,
      "",
    ].join("\n"),
  );
  const packet = join(root, "packet.json");
  const packetArgs = [
    SUPERVISOR,
    "packet",
    "--workspace",
    workspace,
    "--objective",
    "synthetic provider overload regression test",
    "--allowed",
    "src/app.js",
    "--validation",
    "python3 check.py",
    "--out",
    packet,
  ];
  if (options.visionImage) packetArgs.push("--vision-image", "screenshots/state.png");
  if (options.visionRequired) packetArgs.push("--vision-required");
  if (options.strictContract) {
    packetArgs.push(
      "--strict-contract-rubric-id",
      "billing_cent_rounding.v1",
      "--strict-contract-task-id",
      "billing-credit-contract",
    );
  }
  await execFile("python3", packetArgs);
  return { root, workspace, packet };
}

async function writeFakeCli(root, source, name = "fake-zcode.cjs") {
  const cli = join(root, name);
  await writeFile(cli, source);
  await chmod(cli, 0o755);
  return cli;
}

async function makeModelUsageDb(root) {
  const db = join(root, "model-usage.sqlite");
  await execFile("python3", [
    "-c",
    [
      "import sqlite3, sys",
      "conn=sqlite3.connect(sys.argv[1])",
      "conn.execute('create table model_usage (provider_id text, model_id text, status text, started_at integer, completed_at integer, computed_total_tokens integer, input_tokens integer, output_tokens integer)')",
      "conn.commit()",
      "conn.close()",
    ].join(";"),
    db,
  ]);
  return db;
}

async function runPacket({ packet, cli, codexbar, usageSnapshotSource = "none", extraArgs = [], env = {} }) {
  const out = join(dirname(packet), "run.json");
  await execFile(
    "node",
    [
      ZCODECTL,
      "run-packet",
      "--packet",
      packet,
      "--max-attempts",
      "2",
      "--retry-delay-ms",
      "0",
      "--no-bootstrap",
      "--usage-snapshot-source",
      usageSnapshotSource,
      ...extraArgs,
      "--out",
      out,
    ],
    {
      env: {
        ...process.env,
        ZCODE_CLI_PATH: cli,
        ...(codexbar ? { CODEXBAR_PATH: codexbar } : {}),
        ...env,
      },
      cwd: ROOT,
      maxBuffer: 10 * 1024 * 1024,
    },
  ).catch(() => {});
  return JSON.parse(await readFile(out, "utf8"));
}

async function waitForJsonFile(path, predicate) {
  let lastError;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const payload = JSON.parse(await readFile(path, "utf8"));
      if (predicate(payload)) return payload;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
  }
  throw lastError ?? new Error(`timed out waiting for ${path}`);
}

async function withQuotaServer(percentages, callback, options = {}) {
  let calls = 0;
  const server = http.createServer((request, response) => {
    assert.equal(request.headers.authorization, "Bearer local-fixture-value");
    const percentage = percentages[Math.min(calls, percentages.length - 1)];
    calls += 1;
    const tokenLimit = {
      type: "TOKENS_LIMIT",
      percentage,
      nextResetTime: 1781680000000,
    };
    if (options.includeTokenCounts) {
      tokenLimit.usage = percentage * 1000;
      tokenLimit.remaining = 100000 - tokenLimit.usage;
    }
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({
      code: 200,
      data: {
        level: "pro",
        limits: [
          tokenLimit,
          {
            type: "TIME_LIMIT",
            percentage: 0.5,
            usage: 30,
            remaining: 70,
          },
        ],
      },
    }));
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  try {
    const { port } = server.address();
    return await callback(`http://127.0.0.1:${port}/quota`, () => calls);
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose));
  }
}

function decodeWebSocketFrame(buffer) {
  const opcode = buffer[0] & 0x0f;
  if (opcode === 8) return { close: true };
  if (opcode !== 1) return null;
  const second = buffer[1];
  let length = second & 0x7f;
  let offset = 2;
  if (length === 126) {
    length = buffer.readUInt16BE(offset);
    offset += 2;
  } else if (length === 127) {
    length = Number(buffer.readBigUInt64BE(offset));
    offset += 8;
  }
  const masked = Boolean(second & 0x80);
  const mask = masked ? buffer.subarray(offset, offset + 4) : null;
  if (masked) offset += 4;
  const payload = Buffer.from(buffer.subarray(offset, offset + length));
  if (mask) {
    for (let index = 0; index < payload.length; index += 1) {
      payload[index] ^= mask[index % 4];
    }
  }
  return JSON.parse(payload.toString("utf8"));
}

function encodeWebSocketText(value) {
  const payload = Buffer.from(JSON.stringify(value));
  if (payload.length < 126) {
    return Buffer.concat([Buffer.from([0x81, payload.length]), payload]);
  }
  if (payload.length <= 0xffff) {
    const header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(payload.length, 2);
    return Buffer.concat([header, payload]);
  }
  const header = Buffer.alloc(10);
  header[0] = 0x81;
  header[1] = 127;
  header.writeBigUInt64BE(BigInt(payload.length), 2);
  return Buffer.concat([header, payload]);
}

function fixtureCdpValue(expression) {
  if (expression.includes("document.body ? document.body.innerText")) {
    return "Hello ZCode UI\nWorked for 1s\nQuota remaining 42%";
  }
  if (expression.includes("activeWorkspace")) {
    return {
      running: false,
      awaitingApproval: false,
      workedFor: "1s",
      contextUsage: "Context usage 1%",
      activeMode: "Plan",
      activeModel: "GLM-5.2",
      activeWorkspace: "fixture",
      lastText: "Worked for 1s",
    };
  }
  if (expression.includes("workspace not found")) return { ok: true, workspace: "fixture", x: 10, y: 10 };
  if (expression.includes("mode switch not found")) return { ok: true, current: "Auto Edit", x: 10, y: 10 };
  if (expression.includes("mode option not found")) return { ok: true, mode: "Plan", x: 10, y: 30, text: "Plan" };
  if (expression.includes("composer not found")) return { ok: true, text: "hello fixture" };
  if (expression.includes("button not found")) return { ok: true, label: expression.includes("Send") ? "Send" : "Usage", contains: false, x: 10, y: 10 };
  if (expression.includes("Worked for")) return { running: false, awaitingApproval: false, workedFor: "1s", lastText: "Worked for 1s" };
  if (expression.includes("querySelectorAll('textarea,input")) {
    return [{ i: 0, tag: "TEXTAREA", placeholder: "Ask ZCode", text: "", rect: { x: 1, y: 2, w: 300, h: 40 } }];
  }
  if (expression.includes("querySelectorAll('button,[role=button]')") && expression.includes("disabled")) {
    return [{ i: 0, text: "Send", aria: null, disabled: false, rect: { x: 10, y: 10, w: 60, h: 30 } }];
  }
  if (expression.includes("visible_usage_lines")) {
    return {
      captured_at: "2026-06-22T00:00:00.000Z",
      title: "ZCode",
      visible_usage_lines: ["Quota remaining 42%"],
      token_candidates: [],
      quota_percent_candidates: [{ line: "Quota remaining 42%", value: 42 }],
      best: { tokens_total: null, tokens_line: null, quota_percent: 42, quota_percent_line: "Quota remaining 42%" },
    };
  }
  if (expression.includes("usage entry not found")) return { ok: true, phase: "usage", clicked: { text: "Usage", x: 1, y: 1 } };
  return { ok: true, expression: expression.slice(0, 80) };
}

async function withFakeCdpServer(callback) {
  const sockets = new Set();
  const server = http.createServer((request, response) => {
    if (request.url === "/json/version") {
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ Browser: "FakeZCode/1.0" }));
      return;
    }
    if (request.url === "/json/list") {
      const { port } = server.address();
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify([
        {
          id: "fixture-page",
          type: "page",
          title: "ZCode",
          url: "app://zcode",
          webSocketDebuggerUrl: `ws://127.0.0.1:${port}/devtools/page/fixture-page`,
        },
      ]));
      return;
    }
    response.statusCode = 404;
    response.end("{}");
  });
  server.on("upgrade", (request, socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    const key = request.headers["sec-websocket-key"];
    const accept = createHash("sha1")
      .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write([
      "HTTP/1.1 101 Switching Protocols",
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Accept: ${accept}`,
      "",
      "",
    ].join("\r\n"));
    socket.on("data", (data) => {
      const message = decodeWebSocketFrame(data);
      if (!message) return;
      if (message.close) {
        socket.end(Buffer.from([0x88, 0x00]));
        return;
      }
      let result = {};
      if (message.method === "Runtime.evaluate") {
        result = { result: { type: "object", value: fixtureCdpValue(message.params.expression) } };
      } else if (message.method === "Page.captureScreenshot") {
        result = { data: Buffer.from("fake-png").toString("base64") };
      }
      socket.write(encodeWebSocketText({ id: message.id, result }));
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  try {
    return await callback(server.address().port);
  } finally {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolveClose) => server.close(resolveClose));
  }
}

function runZcodectlCdp(args) {
  return execFile("node", [ZCODECTL, ...args], {
    cwd: ROOT,
    timeout: 5_000,
    maxBuffer: 5 * 1024 * 1024,
  });
}

const overloadStderr = `
process.stderr.write("ProviderBusinessError: [1305][The service may be temporarily overloaded, please try again later][synthetic]\\n  code: 'PROVIDER_BUSINESS_ERROR',\\n  isProviderBusinessError: true,\\n  providerCode: '1305',\\n  providerId: 'zai',\\n  providerKind: 'anthropic',\\n  providerMessage: '[1305][The service may be temporarily overloaded, please try again later][synthetic]'\\n");
process.exit(143);
`;

const rateLimit1302Burst = `
for (const id of ["one", "two", "three"]) {
  process.stderr.write("ProviderBusinessError: [1302][Rate limit reached for requests][" + id + "]\\n  code: 'PROVIDER_BUSINESS_ERROR',\\n  isProviderBusinessError: true,\\n  providerCode: '1302',\\n  providerId: 'zai',\\n  providerKind: 'anthropic',\\n  providerMessage: '[1302][Rate limit reached for requests][" + id + "]'\\n");
}
setTimeout(() => {}, 10_000);
`;

test("run-packet retries no-change provider overload and reports retryable state", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(fixture.root, `#!/usr/bin/env node\n${overloadStderr}`);

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.cli_ok, false);
  assert.equal(result.supervisor_state, "retryable_provider_error");
  assert.equal(result.provider_error, true);
  assert.equal(result.provider_code, "1305");
  assert.equal(result.provider_error_kind, "provider_overload");
  assert.equal(result.blocker_kind, "infrastructure_blocker");
  assert.equal(result.infrastructure_blocker, true);
  assert.equal(result.attempts, 2);
  assert.equal(result.attempt_count, 2);
  assert.equal(result.retry_count, 1);
  assert.deepEqual(result.retry_delays_ms, [0]);
  assert.equal(result.safe_to_retry_later, true);
  assert.equal(result.partial_artifacts_possible, false);
  assert.equal(result.usage_available, false);
  assert.equal(result.no_usage_reason, "provider_error_without_zcode_cli_usage");
  assert.equal(result.usage_accounting.no_usage_reason, "provider_error_without_zcode_cli_usage");
  assert.equal(result.attempt_results[0].changed_count, 0);
  assert.equal(result.attempt_results[0].no_usage_reason, "provider_error_without_zcode_cli_usage");
});

test("run-packet fails fast on repeated provider 1302 rate limit", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(fixture.root, `#!/usr/bin/env node\n${rateLimit1302Burst}`);

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: [
      "--max-attempts",
      "1",
      "--timeout-ms",
      "5000",
      "--provider-rate-limit-fail-fast-count",
      "3",
    ],
  });

  assert.equal(result.ok, false);
  assert.equal(result.cli_ok, false);
  assert.equal(result.supervisor_state, "retryable_provider_error");
  assert.equal(result.status, "retryable_provider_error");
  assert.equal(result.timed_out, false);
  assert.equal(result.provider_fail_fast, true);
  assert.equal(result.provider_fail_fast_reason, "repeated_provider_rate_limit_1302");
  assert.equal(result.provider_error_kind, "provider_rate_limit_1302");
  assert.equal(result.provider_code, "1302");
  assert.equal(result.provider_rate_limit_1302, true);
  assert.equal(result.provider_rate_limit_1302_count, 3);
  assert.equal(result.attempt_results[0].provider_fail_fast, true);
  assert.equal(result.attempt_results[0].provider_error_kind, "provider_rate_limit_1302");
  assert.equal(result.audit.changed_count, 0);
  assert.equal(result.validation.ok, true);
});

test("run-packet strips Git control env from provider child process", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const envCapture = join(fixture.root, "provider-child-env.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
const names = ${JSON.stringify(GIT_CONTROL_ENV_VARS)};
const captured = Object.fromEntries(names.map((name) => [name, process.env[name] ?? null]));
fs.writeFileSync(${JSON.stringify(envCapture)}, JSON.stringify(captured, null, 2));
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
process.stdout.write(JSON.stringify({ response: "ok" }) + "\\n");
`,
  );
  const dirtyGitEnv = Object.fromEntries(GIT_CONTROL_ENV_VARS.map((name) => [name, `${name}-sentinel`]));

  const result = await runPacket({ packet: fixture.packet, cli, env: dirtyGitEnv });
  const captured = JSON.parse(await readFile(envCapture, "utf8"));

  assert.equal(result.ok, true);
  for (const name of GIT_CONTROL_ENV_VARS) assert.equal(captured[name], null, name);
});

test("run-packet preserves audited partial artifacts after provider overload", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
${overloadStderr}`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.cli_ok, false);
  assert.equal(result.supervisor_state, "partial_success");
  assert.equal(result.provider_error, true);
  assert.equal(result.provider_code, "1305");
  assert.equal(result.provider_error_kind, "provider_overload");
  assert.equal(result.blocker_kind, "infrastructure_blocker");
  assert.equal(result.infrastructure_blocker, true);
  assert.equal(result.attempts, 1);
  assert.equal(result.attempt_count, 1);
  assert.equal(result.retry_count, 0);
  assert.equal(result.safe_to_retry_later, false);
  assert.equal(result.partial_artifacts_possible, true);
  assert.equal(result.usage_available, false);
  assert.equal(result.no_usage_reason, "provider_error_without_zcode_cli_usage");
  assert.equal(result.usage_accounting.no_usage_reason, "provider_error_without_zcode_cli_usage");
  assert.equal(result.audit.ok, true);
  assert.equal(result.audit.changed_count, 1);
  assert.equal(result.audit.validation.ok, true);
});

test("run-packet audits successful CLI output before accepting it", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
process.stdout.write(JSON.stringify({
  response: "done",
  usage: { totalTokens: 15, inputTokens: 10, outputTokens: 5 }
}) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.cli_ok, true);
  assert.equal(result.supervisor_state, "success");
  assert.equal(result.audit.ok, true);
  assert.equal(result.audit.changed_count, 1);
  assert.equal(result.audit.validation.ok, true);
  assert.equal(result.validation.ok, true);
  assert.equal(result.validation_ok, true);
});

test("run-packet preserves measured usage from JSONL stdout line", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
process.stdout.write("ZCode progress: editing file\\n");
process.stdout.write(JSON.stringify({
  response: "done",
  usage: {
    totalTokens: 42,
    inputTokens: 30,
    outputTokens: 10,
    reasoningTokens: 2
  }
}) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.usage_available, true);
  assert.equal(result.usage_accounting.usage_available, true);
  assert.equal(result.usage_accounting.tokens_source, "zcode_cli_json_usage");
  assert.equal(result.usage_accounting.tokens_used, 42);
  assert.equal(result.usage_accounting.input_tokens, 30);
  assert.equal(result.usage_accounting.output_tokens, 10);
  assert.equal(result.usage_accounting.reasoning_tokens, 2);
  assert.equal(result.usage_normalized.total_tokens, 42);
  assert.equal(result.attempt_results[0].tokens_total, 42);
});

test("run-packet can capture worker tokens from opt-in model usage DB delta", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = await makeModelUsageDb(fixture.root);
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
execFileSync('python3', ['-c', [
  'import sqlite3, sys, time',
  'db=sys.argv[1]',
  'now=int(time.time()*1000)',
  'conn=sqlite3.connect(db)',
  'conn.execute("insert into model_usage (provider_id, model_id, status, started_at, completed_at, computed_total_tokens, input_tokens, output_tokens) values (?,?,?,?,?,?,?,?)", ("zai", "glm-5.2", "completed", now, now + 1, 58, 40, 18))',
  'conn.commit()',
  'conn.close()'
].join(';'), process.env.TEST_ZCODE_MODEL_USAGE_DB]);
process.stdout.write(JSON.stringify({ response: "done without stdout usage" }) + "\\n");
`,
  );

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--model-usage-db", db],
    env: { TEST_ZCODE_MODEL_USAGE_DB: db },
  });
  const ledger = JSON.parse(
    (await readFile(join(fixture.root, "worker-usage.jsonl"), "utf8")).trim(),
  );

  assert.equal(result.ok, true);
  assert.equal(result.usage_accounting.usage_available, true);
  assert.equal(result.usage_accounting.tokens_source, "zcode_cli_model_usage_db_delta");
  assert.equal(result.usage_accounting.tokens_used, 58);
  assert.equal(result.usage_accounting.input_tokens, 40);
  assert.equal(result.usage_accounting.output_tokens, 18);
  assert.equal(result.worker_usage_status, "measured");
  assert.equal(result.worker_usage_unit, "tokens");
  assert.equal(result.worker_total_tokens, 58);
  assert.equal(result.worker_usage_source_path, "worker-usage.jsonl");
  assert.deepEqual(result.row_ids, [1]);
  assert.equal(ledger.source_type, "zcode_cli_model_usage_db_delta");
  assert.equal(ledger.usage.total_tokens, 58);
});

test("run-packet requires and accepts strict contract self-audit evidence", async () => {
  const fixture = await makeWorkspace("before\n", "after\n", { strictContract: true });
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
const auditDir = path.join(cwd, '.codex', 'zcode', 'runs');
fs.mkdirSync(auditDir, { recursive: true });
fs.writeFileSync(path.join(auditDir, 'zcode_self_audit.json'), JSON.stringify({
  schema_version: "zcode_self_audit.v1",
  contract_id: "billing-credit-contract@billing_cent_rounding.v1",
  task_id: "billing-credit-contract",
  overall_status: "pass",
  requirements: [
    { id: "REQ-001", status: "satisfied", evidence: [
      { type: "validation_result", ref: "validation.json", summary: "validation passed" },
      { type: "changed_file", ref: "src/app.js", summary: "implementation changed" }
    ] },
    { id: "REQ-002", status: "satisfied", evidence: [
      { type: "changed_file", ref: "src/app.js", summary: "allowed file changed" },
      { type: "diffstat", ref: "diffstat.json", summary: "one file changed" }
    ] },
    { id: "REQ-003", status: "satisfied", evidence: [
      { type: "diffstat", ref: "diffstat.json", summary: "small diff" }
    ] }
  ],
  edge_cases: [],
  validation: { result: "pass", summary: "validation passed" },
  deviations_from_plan: [],
  unresolved_questions: [],
  risk_flags: [],
  blocked_reasons: []
}));
process.stdout.write(JSON.stringify({
  response: "done",
  usage: { totalTokens: 15, inputTokens: 10, outputTokens: 5 }
}) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.audit.ok, true);
  assert.equal(result.audit.strict_contract.accepted, true);
  assert.equal(result.audit.strict_contract.trace_coverage.requirements_with_evidence, 3);
});

test("run-packet writes structured JSON when the ZCode CLI times out", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
setTimeout(() => {}, 10_000);
`,
  );

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--timeout-ms", "100"],
  });

  assert.equal(result.ok, false);
  assert.equal(result.cli_ok, false);
  assert.equal(result.supervisor_state, "run_timeout");
  assert.equal(result.status, "run_timeout");
  assert.equal(result.timed_out, true);
  assert.equal(result.attempts, 1);
  assert.equal(result.audit.changed_count, 0);
  assert.equal(result.validation.ok, true);
});

test("run-packet can accept a validated artifact before a hung CLI times out", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
setTimeout(() => {
  fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');
}, 100);
setTimeout(() => {}, 10_000);
`,
  );

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: [
      "--max-attempts",
      "1",
      "--timeout-ms",
      "5000",
      "--accept-validated-artifact-after-ms",
      "200",
    ],
  });

  assert.equal(result.ok, true);
  assert.equal(result.cli_ok, true);
  assert.equal(result.accepted_validated_artifact, true);
  assert.equal(result.supervisor_state, "success");
  assert.equal(result.status, "success");
  assert.equal(result.timed_out, false);
  assert.equal(result.audit.changed_count, 1);
  assert.equal(result.validation.ok, true);
});

test("run-packet writes running progress before the ZCode CLI returns", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const out = join(fixture.root, "run-progress.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
setTimeout(() => {
  process.stdout.write(JSON.stringify({ response: "finished" }) + "\\n");
}, 800);
`,
  );

  const child = execFileCallback(
    "node",
    [
      ZCODECTL,
      "run-packet",
      "--packet",
      fixture.packet,
      "--max-attempts",
      "1",
      "--no-bootstrap",
      "--usage-snapshot-source",
      "none",
      "--out",
      out,
    ],
    {
      env: {
        ...process.env,
        ZCODE_CLI_PATH: cli,
      },
      cwd: ROOT,
      maxBuffer: 10 * 1024 * 1024,
    },
    () => {},
  );

  const progress = await waitForJsonFile(out, (payload) => payload.status === "running");
  assert.equal(progress.ok, false);
  assert.equal(progress.supervisor_state, "running");
  assert.equal(progress.attempt_count, 0);
  await new Promise((resolveWait) => child.on("exit", resolveWait));
});

test("run-packet writes terminal JSON when interrupted", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const out = join(fixture.root, "run-interrupted.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
setTimeout(() => {
  process.stdout.write(JSON.stringify({ response: "finished" }) + "\\n");
}, 800);
`,
  );

  const child = execFileCallback(
    "node",
    [
      ZCODECTL,
      "run-packet",
      "--packet",
      fixture.packet,
      "--max-attempts",
      "1",
      "--no-bootstrap",
      "--usage-snapshot-source",
      "none",
      "--out",
      out,
    ],
    {
      env: {
        ...process.env,
        ZCODE_CLI_PATH: cli,
      },
      cwd: ROOT,
      maxBuffer: 10 * 1024 * 1024,
    },
    () => {},
  );

  await waitForJsonFile(out, (payload) => payload.status === "running");
  child.kill("SIGTERM");
  await new Promise((resolveWait) => child.on("exit", resolveWait));
  const result = JSON.parse(await readFile(out, "utf8"));
  assert.equal(result.ok, false);
  assert.equal(result.status, "aborted");
  assert.equal(result.supervisor_state, "aborted");
  assert.equal(result.signal, "SIGTERM");
  assert.notEqual(result.status, "running");
});

test("run-packet rejects successful CLI output when supervisor audit fails", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'wrong\\n');
process.stdout.write(JSON.stringify({
  response: "done",
  usage: { totalTokens: 15, inputTokens: 10, outputTokens: 5 }
}) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.cli_ok, true);
  assert.equal(result.supervisor_state, "audit_failed");
  assert.equal(result.audit.ok, false);
  assert.equal(result.audit.changed_count, 1);
  assert.equal(result.audit.validation.ok, false);
  assert.equal(result.validation.ok, false);
  assert.equal(result.validation_ok, false);
  assert.equal(result.partial_artifacts_possible, true);
});

test("run-packet retries validation failures with a bounded repair prompt", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const promptLog = join(fixture.root, "prompts.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const promptIndex = process.argv.indexOf('--prompt');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
const prompt = promptIndex >= 0 ? process.argv[promptIndex + 1] : '';
const prompts = fs.existsSync(${JSON.stringify(promptLog)})
  ? JSON.parse(fs.readFileSync(${JSON.stringify(promptLog)}, 'utf8'))
  : [];
prompts.push(prompt);
fs.writeFileSync(${JSON.stringify(promptLog)}, JSON.stringify(prompts));
const repaired = prompt.includes('Validation stderr tail:');
fs.writeFileSync(path.join(cwd, 'src/app.js'), repaired ? 'after\\n' : 'wrong\\n');
process.stdout.write(JSON.stringify({
  response: repaired ? "repaired" : "first attempt",
  usage: { totalTokens: repaired ? 9 : 7, inputTokens: 5, outputTokens: repaired ? 4 : 2 }
}) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.supervisor_state, "success");
  assert.equal(result.attempts, 2);
  assert.equal(result.retry_count, 1);
  assert.deepEqual(result.retry_delays_ms, [0]);
  assert.equal(result.attempt_results[0].validation_ok, false);
  assert.equal(result.attempt_results[1].validation_ok, true);
  assert.equal(result.validation_ok, true);
  const prompts = JSON.parse(await readFile(promptLog, "utf8"));
  assert.equal(prompts.length, 2);
  assert.match(prompts[1], /previous attempt edited files but failed supervisor validation/i);
  assert.match(prompts[1], /Validation stderr tail:/);
  assert.match(prompts[1], /expected value as the acceptance contract/);
});

test("run-packet can fail closed without validation repair retries", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const promptLog = join(fixture.root, "no-repair-prompts.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const promptIndex = process.argv.indexOf('--prompt');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
const prompt = promptIndex >= 0 ? process.argv[promptIndex + 1] : '';
const prompts = fs.existsSync(${JSON.stringify(promptLog)})
  ? JSON.parse(fs.readFileSync(${JSON.stringify(promptLog)}, 'utf8'))
  : [];
prompts.push(prompt);
fs.writeFileSync(${JSON.stringify(promptLog)}, JSON.stringify(prompts));
fs.writeFileSync(path.join(cwd, 'src/app.js'), 'wrong\\n');
process.stdout.write(JSON.stringify({
  response: "first attempt",
  usage: { totalTokens: 7, inputTokens: 5, outputTokens: 2 }
}) + "\\n");
`,
  );

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--no-repair-validation"],
  });

  assert.equal(result.ok, false);
  assert.equal(result.supervisor_state, "audit_failed");
  assert.equal(result.attempts, 1);
  assert.equal(result.retry_count, 0);
  assert.equal(result.validation_ok, false);
  const prompts = JSON.parse(await readFile(promptLog, "utf8"));
  assert.equal(prompts.length, 1);
});

test("run-packet retries safe partial timeout artifacts when only validation failed", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const promptLog = join(fixture.root, "partial-prompts.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const cwdIndex = process.argv.indexOf('--cwd');
const promptIndex = process.argv.indexOf('--prompt');
const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();
const prompt = promptIndex >= 0 ? process.argv[promptIndex + 1] : '';
const prompts = fs.existsSync(${JSON.stringify(promptLog)})
  ? JSON.parse(fs.readFileSync(${JSON.stringify(promptLog)}, 'utf8'))
  : [];
prompts.push(prompt);
fs.writeFileSync(${JSON.stringify(promptLog)}, JSON.stringify(prompts));
const repaired = prompt.includes('Validation stderr tail:');
fs.writeFileSync(path.join(cwd, 'src/app.js'), repaired ? 'after\\n' : 'wrong\\n');
process.stdout.write(JSON.stringify({ response: repaired ? "repaired" : "partial" }) + "\\n");
if (!repaired) process.exit(143);
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, true);
  assert.equal(result.supervisor_state, "success");
  assert.equal(result.attempts, 2);
  assert.equal(result.attempt_results[0].supervisor_state, "unsafe_partial");
  assert.equal(result.attempt_results[1].validation_ok, true);
  const prompts = JSON.parse(await readFile(promptLog, "utf8"));
  assert.match(prompts[1], /repair attempt 2/);
});

test("run-packet attaches packet vision images when image service is configured", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const argvPath = join(fixture.root, "argv.json");
  const envPath = join(fixture.root, "env.json");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
fs.writeFileSync(${JSON.stringify(argvPath)}, JSON.stringify(process.argv));
fs.writeFileSync(${JSON.stringify(envPath)}, JSON.stringify({
  zAiApiKey: process.env.Z_AI_API_KEY || null
}));
process.stdout.write(JSON.stringify({
  response: "vision done",
  usage: { totalTokens: 11, inputTokens: 7, outputTokens: 4 }
}) + "\\n");
`,
  );
  const cliConfig = join(fixture.root, "cli-config.json");
  await writeFile(cliConfig, JSON.stringify({
    provider: {
      zai: {
        options: {
          ["api" + "Key"]: "fixture-" + "zai-value",
        },
      },
    },
    mcp: {
      servers: {
        "image-service": {
          command: "npx",
          args: ["-y", "@z_ai/mcp-server"],
        },
      },
    },
  }));

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--cli-config", cliConfig],
    env: {
      ["Z_AI_" + "API" + "_KEY"]: "",
      ["ZAI_" + "API" + "_KEY"]: "legacy-" + "fixture-value",
    },
  });

  assert.equal(result.ok, true);
  assert.equal(result.vision.required, true);
  assert.deepEqual(result.vision.image_files, ["screenshots/state.png"]);
  assert.equal(result.vision_preflight.ok, true);
  assert.equal(result.vision_preflight.detected_server, "image-service");
  assert.equal(result.audit.ok, true);
  assert.equal(result.validation.ok, true);
  assert.equal(result.vision_service_credential_source, "env:ZAI_API_KEY");
  const argv = JSON.parse(await readFile(argvPath, "utf8"));
  const attachIndex = argv.indexOf("--attach");
  assert.notEqual(attachIndex, -1);
  assert.equal(argv[attachIndex + 1], result.vision.attached_files[0]);
  const envSeen = JSON.parse(await readFile(envPath, "utf8"));
  assert.equal(envSeen.zAiApiKey, "legacy-fixture-value");
});

test("run-packet stops required vision packets before CLI when image service is missing", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );
  const cliConfig = join(fixture.root, "cli-config.json");
  await writeFile(cliConfig, JSON.stringify({
    mcp: {
      servers: {
        "generic-vision": {
          command: "vision-mcp",
          args: ["serve"],
        },
      },
    },
  }));

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--cli-config", cliConfig],
  });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_service_unavailable");
  assert.equal(result.supervisor_state, "vision_service_unavailable");
  assert.equal(result.vision_preflight.ok, false);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet does not satisfy custom vision service with default ZAI MCP", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  const payload = JSON.parse(await readFile(fixture.packet, "utf8"));
  payload.vision.service = "custom-image-service";
  await writeFile(fixture.packet, JSON.stringify(payload));
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );
  const cliConfig = join(fixture.root, "cli-config.json");
  await writeFile(cliConfig, JSON.stringify({
    mcp: {
      servers: {
        "default-zai": {
          command: "npx",
          args: ["-y", "@z_ai/mcp-server"],
        },
      },
    },
  }));

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    extraArgs: ["--cli-config", cliConfig],
  });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_service_unavailable");
  assert.equal(result.vision.service, "custom-image-service");
  assert.equal(result.vision_preflight.ok, false);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet rejects unsafe packet vision attachments before CLI", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  await writeFile(join(fixture.workspace, "screenshots", "credential.png"), Buffer.from([0x89, 0x50]));
  const payload = JSON.parse(await readFile(fixture.packet, "utf8"));
  payload.vision.image_files = ["screenshots/credential.png"];
  await writeFile(fixture.packet, JSON.stringify(payload));
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_attachment_invalid");
  assert.match(result.error, /secret-like vision attachment/);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet rejects non-image packet vision attachments before CLI", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  await writeFile(join(fixture.workspace, "screenshots", "state.txt"), "not an image");
  const payload = JSON.parse(await readFile(fixture.packet, "utf8"));
  payload.vision.image_files = ["screenshots/state.txt"];
  await writeFile(fixture.packet, JSON.stringify(payload));
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_attachment_invalid");
  assert.match(result.error, /must be an image file/);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet rejects fake image-extension vision attachments before CLI", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  await writeFile(join(fixture.workspace, "screenshots", "fake.png"), "not an image");
  const payload = JSON.parse(await readFile(fixture.packet, "utf8"));
  payload.vision.image_files = ["screenshots/fake.png"];
  await writeFile(fixture.packet, JSON.stringify(payload));
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_attachment_invalid");
  assert.match(result.error, /not a valid image file/);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet rejects symlinked vision attachments outside workspace before CLI", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n", { visionImage: true });
  const calledPath = join(fixture.root, "called.txt");
  const outsideImage = join(fixture.root, "outside.png");
  await writeFile(outsideImage, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  await unlink(join(fixture.workspace, "screenshots", "state.png"));
  await symlink(outsideImage, join(fixture.workspace, "screenshots", "state.png"));
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
require('node:fs').writeFileSync(${JSON.stringify(calledPath)}, 'called');
process.stdout.write(JSON.stringify({ response: "should not run" }) + "\\n");
`,
  );

  const result = await runPacket({ packet: fixture.packet, cli });

  assert.equal(result.ok, false);
  assert.equal(result.status, "vision_attachment_invalid");
  assert.match(result.error, /target escapes workspace/);
  await assert.rejects(readFile(calledPath, "utf8"));
});

test("run-packet captures CodexBar quota snapshots and ZCode CLI token usage", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
process.stdout.write(JSON.stringify({
  response: "done",
  usage: {
    totalTokens: 1234,
    inputTokens: 1000,
    outputTokens: 234,
    cacheReadTokens: 456
  },
  projection: { contextWindow: 200000 }
}) + "\\n");
`,
  );
  const counterPath = join(fixture.root, "codexbar-count.txt");
  const codexbar = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = ${JSON.stringify(counterPath)};
const previous = fs.existsSync(path) ? Number(fs.readFileSync(path, 'utf8')) : 0;
const next = previous + 1;
fs.writeFileSync(path, String(next));
const usedPercent = next === 1 ? 1.25 : 1.75;
process.stdout.write(JSON.stringify([{
  provider: "zai",
  source: "api",
  usage: {
    identity: { providerID: "zai" },
    primary: {
      resetDescription: "5 hours window",
      resetsAt: "2026-06-17T18:30:44Z",
      usedPercent,
      windowMinutes: 300
    },
    secondary: {
      resetDescription: "Monthly",
      resetsAt: "2026-07-04T05:08:05Z",
      usedPercent: 0.5
    },
    tertiary: null,
    updatedAt: "2026-06-17T14:01:03Z"
  }
}]) + "\\n");
`,
    "fake-codexbar.cjs",
  );

  const result = await runPacket({
    packet: fixture.packet,
    cli,
    codexbar,
    usageSnapshotSource: "codexbar",
  });

  assert.equal(result.ok, true);
  assert.equal(result.supervisor_state, "success");
  assert.equal(result.usage_snapshots.before.ok, true);
  assert.equal(result.usage_snapshots.after.ok, true);
  assert.equal(result.usage_snapshots.before.best.quota_percent, 1.25);
  assert.equal(result.usage_snapshots.after.best.quota_percent, 1.75);
  assert.equal(result.usage_accounting.tokens_source, "zcode_cli_json_usage");
  assert.equal(result.usage_accounting.tokens_used, 1234);
  assert.equal(result.usage_accounting.input_tokens, 1000);
  assert.equal(result.usage_accounting.output_tokens, 234);
  assert.equal(result.usage_accounting.cache_read_tokens, 456);
  assert.equal(result.usage_accounting.quota_source, "codexbar");
  assert.equal(result.usage_accounting.quota_percent_direction, "used");
  assert.equal(result.usage_accounting.quota_percent_before, 1.25);
  assert.equal(result.usage_accounting.quota_percent_after, 1.75);
  assert.equal(result.usage_accounting.quota_percent_used, 0.5);
  assert.equal(result.usage_accounting.quota_windows.primary.used_percent_delta, 0.5);
  assert.equal(result.usage_normalized.total_tokens, 1234);
  assert.equal(result.attempt_results[0].tokens_total, 1234);
});

test("run-packet captures Z.AI quota snapshots without CodexBar", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
process.stdout.write(JSON.stringify({
  response: "done",
  usage: {
    totalTokens: 77,
    inputTokens: 50,
    outputTokens: 27
  }
}) + "\\n");
`,
  );

  await withQuotaServer([2.0, 2.75], async (quotaUrl, calls) => {
    const result = await runPacket({
      packet: fixture.packet,
      cli,
      usageSnapshotSource: "zai-api",
      extraArgs: ["--zai-quota-url", quotaUrl],
      env: { ["ZAI_" + "API_KEY"]: "local-fixture-value" },
    });

    assert.equal(calls(), 2);
    assert.equal(result.ok, true);
    assert.equal(result.supervisor_state, "success");
    assert.equal(result.usage_snapshots.before.ok, true);
    assert.equal(result.usage_snapshots.after.ok, true);
    assert.equal(result.usage_snapshots.before.source, "zai-api");
    assert.equal(result.usage_snapshots.after.source, "zai-api");
    assert.equal(result.usage_snapshots.before.credential_source, "env:ZAI_API_KEY");
    assert.equal(result.usage_snapshots.before.windows.primary.authoritative, true);
    assert.equal(result.usage_snapshots.before.best.quota_percent, 2.0);
    assert.equal(result.usage_snapshots.after.best.quota_percent, 2.75);
    assert.equal(result.usage_accounting.tokens_used, 77);
    assert.equal(result.usage_accounting.quota_source, "zai-api");
    assert.equal(result.usage_accounting.quota_percent_before, 2.0);
    assert.equal(result.usage_accounting.quota_percent_after, 2.75);
    assert.equal(result.usage_accounting.quota_percent_used, 0.75);
    assert.equal(result.usage_accounting.quota_percent_status, "measured");
    assert.equal(result.usage_accounting.quota_windows.primary.used_percent_delta, 0.75);
  }, { includeTokenCounts: true });
});

test("run-packet derives quota delta from Z.AI percentage without token counts", async () => {
  const fixture = await makeWorkspace("ok\n", "ok\n");
  const cli = await writeFakeCli(
    fixture.root,
    `#!/usr/bin/env node
process.stdout.write(JSON.stringify({
  response: "done",
  usage: {
    totalTokens: 77,
    inputTokens: 50,
    outputTokens: 27
  }
}) + "\\n");
`,
  );

  await withQuotaServer([2.0, 2.75], async (quotaUrl, calls) => {
    const result = await runPacket({
      packet: fixture.packet,
      cli,
      usageSnapshotSource: "zai-api",
      extraArgs: ["--zai-quota-url", quotaUrl],
      env: { ["ZAI_" + "API_KEY"]: "local-fixture-value" },
    });

    assert.equal(calls(), 2);
    assert.equal(result.ok, true);
    assert.equal(result.usage_snapshots.before.windows.primary.authoritative, true);
    assert.equal(result.usage_snapshots.before.windows.primary.token_counts_available, false);
    assert.equal(result.usage_snapshots.before.windows.primary.used_percent, 2.0);
    assert.equal(result.usage_snapshots.before.windows.primary.non_authoritative_used_percent, null);
    assert.equal(result.usage_snapshots.before.windows.primary.quota_percent_unavailable_reason, null);
    assert.equal(result.usage_snapshots.before.best.quota_percent, 2.0);
    assert.equal(result.usage_snapshots.before.best.raw_quota_percent, 2.0);
    assert.equal(result.usage_accounting.quota_percent_before, 2.0);
    assert.equal(result.usage_accounting.quota_percent_after, 2.75);
    assert.equal(result.usage_accounting.quota_percent_used, 0.75);
    assert.equal(result.usage_accounting.quota_percent_status, "measured");
    assert.equal(result.usage_accounting.quota_percent_unavailable_reason, null);
    assert.equal(result.usage_accounting.quota_windows.primary.used_percent_delta, 0.75);
  });
});

test("desktop CDP commands explain how to recover when ZCode is not launched", async () => {
  const server = http.createServer();
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const port = server.address().port;
  await new Promise((resolveClose) => server.close(resolveClose));

  await assert.rejects(
    execFile("node", [ZCODECTL, "targets", "--port", String(port)], { cwd: ROOT }),
    (error) => {
      assert.equal(error.code, 1);
      assert.match(error.stderr, /ZCode CDP is not reachable/);
      assert.match(error.stderr, new RegExp(`zcodectl launch --port ${port}`));
      return true;
    },
  );
});

test("desktop CDP inspection commands work against a ZCode-shaped CDP target", async () => {
  await withFakeCdpServer(async (port) => {
    const targets = JSON.parse((await runZcodectlCdp(["targets", "--port", String(port)])).stdout);
    assert.equal(targets[0].title, "ZCode");
    assert.equal(targets[0].has_websocket, true);

    const text = JSON.parse((await runZcodectlCdp(["text", "--port", String(port), "--max", "20"])).stdout);
    assert.match(text.value, /Hello ZCode/);

    const textboxes = JSON.parse((await runZcodectlCdp(["textboxes", "--port", String(port)])).stdout);
    assert.equal(textboxes.value[0].placeholder, "Ask ZCode");

    const buttons = JSON.parse((await runZcodectlCdp(["buttons", "--port", String(port)])).stdout);
    assert.equal(buttons.value[0].text, "Send");

    const summary = JSON.parse((await runZcodectlCdp(["summary", "--port", String(port)])).stdout);
    assert.equal(summary.value.activeMode, "Plan");

    const usageOut = join(tmpdir(), `zcode-usage-${Date.now()}.json`);
    const usage = JSON.parse((await runZcodectlCdp(["usage", "--port", String(port), "--out", usageOut])).stdout);
    assert.equal(usage.best.quota_percent, 42);
    assert.equal(JSON.parse(await readFile(usageOut, "utf8")).best.quota_percent, 42);

    const openUsage = JSON.parse((await runZcodectlCdp(["open-usage", "--port", String(port)])).stdout);
    assert.equal(openUsage.value.ok, true);
  });
});

test("desktop CDP interaction commands send expected renderer actions", async () => {
  await withFakeCdpServer(async (port) => {
    const newTask = JSON.parse((await runZcodectlCdp(["new-task", "--workspace", "fixture", "--port", String(port)])).stdout);
    assert.equal(newTask.value.ok, true);

    const setMode = JSON.parse((await runZcodectlCdp(["set-mode", "--mode", "Plan", "--port", String(port)])).stdout);
    assert.equal(setMode.value.mode, "Plan");

    const setComposer = JSON.parse((await runZcodectlCdp(["set-composer", "--text", "hello fixture", "--port", String(port)])).stdout);
    assert.equal(setComposer.value.text, "hello fixture");

    const click = JSON.parse((await runZcodectlCdp(["click", "--text", "Send", "--port", String(port)])).stdout);
    assert.equal(click.value.ok, true);

    const clickContains = JSON.parse((await runZcodectlCdp(["click-contains", "--text", "Sen", "--port", String(port)])).stdout);
    assert.equal(clickContains.value.ok, true);

    const waitIdle = JSON.parse((await runZcodectlCdp(["wait-idle", "--timeout-ms", "50", "--interval-ms", "5", "--port", String(port)])).stdout);
    assert.equal(waitIdle.ok, true);

    const screenshotPath = join(tmpdir(), `zcode-shot-${Date.now()}.png`);
    const screenshotStdout = (await runZcodectlCdp(["screenshot", "--out", screenshotPath, "--port", String(port)])).stdout.trim();
    assert.equal(screenshotStdout, screenshotPath);
    assert.equal(await readFile(screenshotPath, "utf8"), "fake-png");
  });
});
