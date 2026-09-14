import test from "node:test";
import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { createHash } from "node:crypto";
import http from "node:http";
import { access, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";

const ROOT = resolve(import.meta.dirname, "..");
const ZCODECTL = resolve(ROOT, "tools", "zcode_control", "zcodectl.mjs");
const SUPERVISOR = resolve(ROOT, "tools", "zcode_supervisor", "zcode_supervisor.py");
const execFile = promisify(execFileCallback);
const PYTHON = process.env.ZCODE_SUPERVISOR_PYTHON || (process.platform === "win32" ? "python" : "python3");
const PYTHON_VALIDATION = process.platform === "win32" ? "python check.py" : "python3 check.py";

async function makeWorkspace(initialText, validationText) {
  const root = await mkdtemp(join(tmpdir(), "zcode-app-runner-cdp-"));
  const workspace = join(root, "workspace");
  await mkdir(join(workspace, "src"), { recursive: true });
  await writeFile(join(workspace, "src", "app.js"), initialText);
  await writeFile(
    join(workspace, "check.py"),
    [
      "from pathlib import Path",
      `raise SystemExit(0 if Path('src/app.js').read_text() == ${JSON.stringify(validationText)} else 1)`,
      "",
    ].join("\n"),
  );
  const packet = join(root, "packet.json");
  await execFile(PYTHON, [
    SUPERVISOR,
    "packet",
    "--workspace",
    workspace,
    "--objective",
    "synthetic non-live app-backed runner scaffold test",
    "--allowed",
    "src/app.js",
    "--validation",
    PYTHON_VALIDATION,
    "--worker-finalization",
    "supervisor_owned",
    "--max-changed-files",
    "1",
    "--out",
    packet,
  ]);
  const packetData = JSON.parse(await readFile(packet, "utf8"));
  return { root, workspace, packetWorkspace: packetData.workspace, packet };
}

async function runAppPacket({ packet, port, extraArgs = [], env = {}, expectFailure = false }) {
  const out = join(dirname(packet), "zcode-run.json");
  try {
    await execFile(
      "node",
      [
        ZCODECTL,
        "app-run-packet",
        "--packet",
        packet,
        "--timeout-ms",
        "1000",
        "--interval-ms",
        "5",
        "--validation-timeout",
        "5",
        ...(port ? ["--port", String(port)] : []),
        ...extraArgs,
        "--out",
        out,
      ],
      {
        cwd: ROOT,
        env: { ...process.env, ...env },
        timeout: 5_000,
        maxBuffer: 5 * 1024 * 1024,
      },
    );
  } catch (error) {
    if (!expectFailure) throw error;
  }
  return JSON.parse(await readFile(out, "utf8"));
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function createModelUsageDb(db) {
  await execFile(PYTHON, [
    "-c",
    [
      "import sqlite3, sys",
      "from pathlib import Path",
      "path = Path(sys.argv[1])",
      "path.parent.mkdir(parents=True, exist_ok=True)",
      "conn = sqlite3.connect(path)",
      "conn.execute('create table model_usage (provider_id TEXT, model_id TEXT, input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER, computed_total_tokens INTEGER, provider_total_tokens INTEGER, status TEXT, started_at INTEGER, completed_at INTEGER)')",
      "conn.commit()",
      "conn.close()",
    ].join("\n"),
    db,
  ]);
}

async function insertModelUsage(db, fields = {}) {
  await execFile(PYTHON, [
    "-c",
    [
      "import json, sqlite3, sys, time",
      "db = sys.argv[1]",
      "fields = json.loads(sys.argv[2])",
      "now = int(time.time() * 1000)",
      "values = {",
      "  'provider_id': fields.get('provider', 'zai'),",
      "  'model_id': fields.get('model', 'glm-5.2'),",
      "  'input_tokens': fields.get('input_tokens', 31),",
      "  'output_tokens': fields.get('output_tokens', 11),",
      "  'reasoning_tokens': fields.get('reasoning_tokens', 5),",
      "  'cache_creation_input_tokens': fields.get('cache_write_tokens', 7),",
      "  'cache_read_input_tokens': fields.get('cache_read_tokens', 3),",
      "  'computed_total_tokens': fields.get('total', 42),",
      "  'provider_total_tokens': fields.get('total', 42),",
      "  'status': fields.get('status', 'completed'),",
      "  'started_at': fields.get('started_at', now),",
      "  'completed_at': fields.get('completed_at', now),",
      "}",
      "columns = list(values)",
      "conn = sqlite3.connect(db)",
      "conn.execute(f\"insert into model_usage ({', '.join(columns)}) values ({', '.join('?' for _ in columns)})\", [values[c] for c in columns])",
      "conn.commit()",
      "conn.close()",
    ].join("\n"),
    db,
    JSON.stringify(fields),
  ]);
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
  if (payload.length < 126) return Buffer.concat([Buffer.from([0x81, payload.length]), payload]);
  const header = Buffer.alloc(4);
  header[0] = 0x81;
  header[1] = 126;
  header.writeUInt16BE(payload.length, 2);
  return Buffer.concat([header, payload]);
}

function matchingWorkspaceBinding(workspace) {
  return {
    ok: true,
    bound: true,
    status: "app_cdp_workspace_bound",
    detected_workspace: workspace,
    evidence_source: "fixture_active_workspace",
  };
}

async function withFakeCdpServer({ onSubmit, workspaceBinding = null }, callback) {
  const sockets = new Set();
  let submitCount = 0;
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
    socket.on("data", async (data) => {
      const message = decodeWebSocketFrame(data);
      if (!message) return;
      if (message.close) {
        socket.end(Buffer.from([0x88, 0x00]));
        return;
      }
      let result = {};
      if (message.method === "Runtime.evaluate") {
        const expression = message.params.expression;
        if (expression.includes("workspace binding preflight")) {
          result = {
            result: {
              type: "object",
              value: workspaceBinding ?? {
                ok: false,
                bound: null,
                status: "app_cdp_workspace_unknown",
                detected_workspace: null,
                reason: "fixture workspace binding not configured",
              },
            },
          };
        } else if (expression.includes("composer not found")) {
          result = { result: { type: "object", value: { ok: true, text: "fixture prompt" } } };
        } else if (expression.includes("button not found")) {
          submitCount += 1;
          await onSubmit();
          result = { result: { type: "object", value: { ok: true, label: "Send", contains: false, x: 10, y: 10 } } };
        } else if (expression.includes("Worked for")) {
          result = {
            result: {
              type: "object",
              value: {
                running: false,
                awaitingApproval: false,
                workedFor: "1s",
                lastText: "Worked for 1s",
              },
            },
          };
        } else {
          result = { result: { type: "object", value: { ok: true } } };
        }
      }
      socket.write(encodeWebSocketText({ id: message.id, result }));
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  try {
    return await callback(server.address().port, () => submitCount);
  } finally {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolveClose) => server.close(resolveClose));
  }
}

test("app-run-packet refuses submit without explicit live opt-in", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");

  const result = await runAppPacket({ packet: fixture.packet, expectFailure: true });

  assert.equal(result.ok, false);
  assert.equal(result.status, "app_cdp_submit_not_allowed");
  assert.equal(result.supervisor_state, "app_cdp_submit_not_allowed");
  assert.equal(result.worker_execution_backend, "zcode_app_cdp");
  assert.equal(result.worker_finalization, "supervisor_owned");
  assert.equal(result.provider_status, "not_invoked_by_app_runner_scaffold");
  assert.equal(result.provider_error, false);
  assert.equal(result.app_cdp.submit_allowed, false);
  assert.equal(result.usage_accounting.worker_usage_capture_method, "zcode_cli_model_usage_db_delta");
  assert.equal(result.usage_accounting.worker_usage_isolation_required, true);
  assert.equal(result.usage_accounting.measured_app_backed_tokens, false);
  assert.equal(result.worker_usage_status, "unavailable");
  assert.equal(result.worker_usage_unit, "unknown");
  assert.equal(result.worker_total_tokens, null);
  assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
});

test("app-run-packet require-workspace-bound can prove binding without Send", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");

  await withFakeCdpServer({
    workspaceBinding: matchingWorkspaceBinding(fixture.packetWorkspace),
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--require-workspace-bound"],
    });

    assert.equal(result.ok, true);
    assert.equal(result.status, "app_cdp_workspace_bound");
    assert.equal(result.supervisor_state, "app_cdp_workspace_bound");
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, fixture.packetWorkspace);
    assert.equal(result.workspace_binding_status, "app_cdp_workspace_bound");
    assert.equal(result.workspace_binding_ok, true);
    assert.equal(result.app_cdp.preflight_only, true);
    assert.equal(result.app_cdp.submit_allowed, false);
    assert.equal(result.app_cdp.submit_blocked_reason, "preflight_only");
    assert.equal(result.app_cdp.send_ok, undefined);
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(submitCount(), 0);
  });
});

test("app-run-packet require-workspace-bound blocks mismatch without Send", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const oldWorkspace = join(fixture.root, "old-workspace");

  await withFakeCdpServer({
    workspaceBinding: {
      ok: false,
      status: "app_cdp_workspace_mismatch",
      detected_workspace: oldWorkspace,
      evidence_source: "fixture_active_workspace",
    },
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--require-workspace-bound"],
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_workspace_mismatch");
    assert.equal(result.supervisor_state, "app_cdp_workspace_mismatch");
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, oldWorkspace);
    assert.equal(result.workspace_binding_status, "app_cdp_workspace_mismatch");
    assert.equal(result.workspace_binding_ok, false);
    assert.equal(result.app_cdp.preflight_only, true);
    assert.equal(result.app_cdp.submit_allowed, false);
    assert.equal(result.app_cdp.submit_blocked_reason, "preflight_only");
    assert.equal(result.app_cdp.send_ok, undefined);
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(submitCount(), 0);
  });
});

test("app-run-packet workspace binding blocks unknown workspace before Send", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--require-workspace-bound", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_workspace_unknown");
    assert.equal(result.supervisor_state, "app_cdp_workspace_unknown");
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, null);
    assert.equal(result.workspace_binding_status, "app_cdp_workspace_unknown");
    assert.equal(result.app_cdp.submit_blocked_reason, "app_cdp_workspace_unknown");
    assert.equal(result.app_cdp.send_ok, undefined);
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(submitCount(), 0);
  });
});

test("app-run-packet workspace binding blocks mismatched workspace before Send", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  const oldWorkspace = join(fixture.root, "old-workspace");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    workspaceBinding: {
      ok: false,
      status: "app_cdp_workspace_mismatch",
      detected_workspace: oldWorkspace,
      evidence_source: "fixture_active_workspace",
    },
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_workspace_mismatch");
    assert.equal(result.supervisor_state, "app_cdp_workspace_mismatch");
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, oldWorkspace);
    assert.equal(result.app_cdp.workspace_binding_status, "app_cdp_workspace_mismatch");
    assert.equal(result.app_cdp.submit_blocked_reason, "app_cdp_workspace_mismatch");
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(submitCount(), 0);
  });
});

test("app-run-packet workspace binding rejects old restored session before Send", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  const restoredWorkspace = join(fixture.root, "restored-session-workspace");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    workspaceBinding: {
      ok: false,
      bound: false,
      status: "app_cdp_workspace_not_bound",
      detected_workspace: restoredWorkspace,
      reason: "restored_session_workspace",
      evidence_source: "fixture_restored_session",
    },
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_workspace_not_bound");
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, restoredWorkspace);
    assert.equal(result.app_cdp.workspace_binding.reason, "restored_session_workspace");
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(submitCount(), 0);
  });
});

test("app-run-packet workspace binding allows matching workspace and captures DB-delta usage against fake CDP", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    workspaceBinding: matchingWorkspaceBinding(fixture.packetWorkspace),
    onSubmit: async () => {
      await writeFile(join(fixture.workspace, "src", "app.js"), "after\n");
      await insertModelUsage(db, { total: 52, input_tokens: 40, output_tokens: 12, reasoning_tokens: 0 });
    },
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
    });

    assert.equal(result.ok, true);
    assert.equal(result.status, "success");
    assert.equal(result.supervisor_state, "success");
    assert.equal(result.worker_execution_backend, "zcode_app_cdp");
    assert.equal(result.worker_finalization, "supervisor_owned");
    assert.equal(result.cli_ok, false);
    assert.equal(result.provider_status, "not_invoked_by_app_runner_scaffold");
    assert.equal(result.provider_error, false);
    assert.equal(result.provider_error_kind, null);
    assert.equal(result.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.detected_workspace, fixture.packetWorkspace);
    assert.equal(result.workspace_binding_status, "app_cdp_workspace_bound");
    assert.equal(result.workspace_binding_ok, true);
    assert.equal(result.validation_ok, true);
    assert.equal(result.validation_rc, 0);
    assert.equal(result.final_validation_rc, 0);
    assert.equal(result.audit_ok, true);
    assert.equal(result.changed_count, 1);
    assert.equal(result.changed_files.modified.includes("src/app.js"), true);
    assert.equal(result.route_rc, null);
    assert.equal(result.acceptance_rc, null);
    assert.equal(result.strict_accepted, null);
    assert.equal(result.usage_accounting.worker_usage_capture_method, "zcode_cli_model_usage_db_delta");
    assert.equal(result.usage_accounting.worker_usage_isolation_required, true);
    assert.equal(result.usage_accounting.measured_app_backed_tokens, true);
    assert.equal(result.source_type, "zcode_cli_model_usage_db_delta");
    assert.equal(result.worker_usage_status, "measured");
    assert.equal(result.worker_usage_unit, "tokens");
    assert.equal(result.worker_total_tokens, 52);
    assert.equal(result.worker_usage_source_path, "worker-usage.jsonl");
    assert.equal(result.usage_accounting.tokens_source, "zcode_cli_model_usage_db_delta");
    assert.equal(result.usage_accounting.tokens_used, 52);
    assert.equal(result.usage_accounting.input_tokens, 40);
    assert.equal(result.usage_accounting.output_tokens, 12);
    assert.equal(result.usage_accounting.reasoning_tokens, 0);
    assert.equal(result.before_max_rowid, 0);
    assert.equal(result.after_max_rowid, 1);
    assert.deepEqual(result.row_ids, [1]);
    assert.equal(result.rows[0].total_tokens, 52);
    assert.equal(result.total_tokens, 52);
    assert.equal(result.usage_available, true);
    assert.equal(result.app_cdp.submit_allowed, true);
    assert.equal(result.app_cdp.expected_workspace, fixture.packetWorkspace);
    assert.equal(result.app_cdp.detected_workspace, fixture.packetWorkspace);
    assert.equal(result.app_cdp.workspace_binding_status, "app_cdp_workspace_bound");
    assert.equal(result.app_cdp.set_composer_ok, true);
    assert.equal(result.app_cdp.send_ok, true);
    assert.equal(result.app_cdp.wait_idle_ok, true);
    assert.equal(submitCount(), 1);

    const ledger = join(fixture.root, "worker-usage.jsonl");
    const records = (await readFile(ledger, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
    assert.equal(records.length, 1);
    assert.equal(records[0].source_type, "zcode_cli_model_usage_db_delta");
    assert.equal(records[0].total_tokens, 52);
    assert.deepEqual(records[0].row_ids, [1]);
    assert.equal(await pathExists(join(fixture.root, "zcode-model-usage-before.json")), true);
    assert.equal(await pathExists(join(fixture.root, "zcode-model-usage-delta.json")), true);
  });
});

test("app-run-packet captures DB-delta fail closed when no rows are attributable", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    workspaceBinding: matchingWorkspaceBinding(fixture.packetWorkspace),
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_usage_unavailable");
    assert.equal(result.supervisor_state, "app_cdp_usage_unavailable");
    assert.equal(result.validation_ok, true);
    assert.equal(result.final_validation_rc, 0);
    assert.equal(result.audit_ok, true);
    assert.equal(result.worker_usage_status, "unavailable");
    assert.equal(result.worker_usage_unit, "unknown");
    assert.equal(result.worker_total_tokens, null);
    assert.equal(result.total_tokens, null);
    assert.equal(result.usage_accounting.no_usage_reason, "zcode_model_usage_no_new_rows");
    assert.equal(result.usage_accounting.measured_app_backed_tokens, false);
    assert.equal(result.usage_accounting.row_ids.length, 0);
    assert.equal(result.worker_usage_source_path, null);
    assert.equal(await pathExists(join(fixture.root, "worker-usage.jsonl")), false);
    assert.equal(submitCount(), 1);
  });
});

test("app-run-packet workspace binding classifies no workspace change and no DB-delta rows as binding failure", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "db.sqlite");
  await createModelUsageDb(db);

  await withFakeCdpServer({
    workspaceBinding: matchingWorkspaceBinding(fixture.packetWorkspace),
    onSubmit: async () => {},
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_workspace_not_bound");
    assert.equal(result.supervisor_state, "app_cdp_workspace_not_bound");
    assert.equal(result.validation_ok, false);
    assert.equal(result.final_validation_rc, 1);
    assert.equal(result.audit_ok, false);
    assert.equal(result.changed_count, 0);
    assert.equal(result.strict_accepted, false);
    assert.equal(result.worker_usage_status, "unavailable");
    assert.equal(result.worker_total_tokens, null);
    assert.equal(result.total_tokens, null);
    assert.deepEqual(result.row_ids, []);
    assert.equal(result.usage_accounting.no_usage_reason, "zcode_model_usage_no_new_rows");
    assert.equal(result.app_cdp.workspace_binding_failure_inferred, true);
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(await pathExists(join(fixture.root, "worker-usage.jsonl")), false);
    assert.equal(submitCount(), 1);
  });
});

test("app-run-packet captures DB-delta fail closed before submit when DB is missing", async () => {
  const fixture = await makeWorkspace("before\n", "after\n");
  const db = join(fixture.root, "fake-zcode", "missing.sqlite");

  await withFakeCdpServer({
    workspaceBinding: matchingWorkspaceBinding(fixture.packetWorkspace),
    onSubmit: () => writeFile(join(fixture.workspace, "src", "app.js"), "after\n"),
  }, async (port, submitCount) => {
    const result = await runAppPacket({
      packet: fixture.packet,
      port,
      extraArgs: ["--allow-submit", "--model-usage-db", db],
      env: { ZCODE_APP_CDP_ALLOW_SUBMIT: "1" },
      expectFailure: true,
    });

    assert.equal(result.ok, false);
    assert.equal(result.status, "app_cdp_usage_unavailable");
    assert.equal(result.supervisor_state, "app_cdp_usage_unavailable");
    assert.equal(result.worker_usage_status, "unavailable");
    assert.equal(result.worker_usage_unit, "unknown");
    assert.equal(result.worker_total_tokens, null);
    assert.equal(result.usage_accounting.no_usage_reason, "zcode_model_usage_db_missing");
    assert.equal(result.app_cdp.submit_blocked_reason, "db_delta_before_unavailable");
    assert.equal(await readFile(join(fixture.workspace, "src", "app.js"), "utf8"), "before\n");
    assert.equal(await pathExists(join(fixture.root, "worker-usage.jsonl")), false);
    assert.equal(submitCount(), 0);
  });
});
