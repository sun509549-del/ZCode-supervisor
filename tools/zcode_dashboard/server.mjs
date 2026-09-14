#!/usr/bin/env node

import { execFile, spawn } from "node:child_process";
import { createServer } from "node:http";
import { lstat, mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const MODULE_DIR = resolve(fileURLToPath(new URL(".", import.meta.url)));
const ROOT = resolve(MODULE_DIR, "..", "..");
const PUBLIC_DIR = join(MODULE_DIR, "public");
const ZCODECTL = join(ROOT, "tools", "zcode_control", "zcodectl.mjs");
const SUPERVISOR = join(ROOT, "tools", "zcode_supervisor", "zcode_supervisor.py");
const PYTHON = process.env.ZCODE_SUPERVISOR_PYTHON || (process.platform === "win32" ? "python" : "python3");
const MAX_BODY_BYTES = 64 * 1024;
const MAX_CAPTURE_BYTES = 2 * 1024 * 1024;
const TASK_CLASSES = new Set(["small-fix", "long-horizon", "architecture", "root-cause", "production-gate", "mobile-debug", "research"]);
const SECRET_PATH_PATTERN = /(^|[\\/])(?:\.env(?:\.|$)|\.ssh(?:[\\/]|$)|id_(?:rsa|ed25519)(?:\.|$)|[^\\/]*(?:credential|credentials|private[_-]?key)[^\\/]*)/i;
const STATIC_FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
]);

let activeJob = null;
let taskLaunchPending = false;
let profileCache = null;

function parseArgs(argv) {
  const result = { workspace: process.cwd(), host: "127.0.0.1", port: 4173, open: true };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--workspace") result.workspace = argv[++index];
    else if (arg === "--host") result.host = argv[++index];
    else if (arg === "--port") result.port = Number(argv[++index]);
    else if (arg === "--no-open") result.open = false;
    else if (arg === "--help" || arg === "-h") result.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  result.workspace = resolve(result.workspace);
  if (!Number.isInteger(result.port) || result.port < 1 || result.port > 65535) throw new Error("--port must be between 1 and 65535");
  if (!["127.0.0.1", "localhost", "::1"].includes(result.host)) throw new Error("Dashboard only binds to a loopback host");
  return result;
}

function printHelp() {
  console.log(`zcode-dashboard

Usage:
  node tools/zcode_dashboard/server.mjs --workspace <repo> [--port 4173] [--no-open]

The dashboard binds to localhost, reads .codex/zcode task artifacts, and never returns API keys.`);
}

function jsonResponse(response, statusCode, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}

function errorMessage(error) {
  const message = error instanceof Error ? error.message : String(error);
  return message.slice(0, 1000);
}

async function readArtifactJson(path) {
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 10 * 1024 * 1024) return null;
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return null;
  }
}

function pathInside(root, candidate) {
  const rel = relative(resolve(root), resolve(candidate));
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

async function runJson(command, args, options = {}) {
  let stdout;
  try {
    ({ stdout } = await execFileAsync(command, args, {
      cwd: options.cwd || ROOT,
      encoding: "utf8",
      timeout: options.timeout || 20_000,
      maxBuffer: MAX_CAPTURE_BYTES,
      windowsHide: true,
    }));
  } catch (error) {
    stdout = typeof error?.stdout === "string" ? error.stdout : "";
    if (!stdout.trim()) throw error;
  }
  const parsed = JSON.parse(stdout);
  if (!parsed || typeof parsed !== "object") throw new Error("Command returned invalid JSON");
  return parsed;
}

function selectedModel(mainModel) {
  if (typeof mainModel !== "string") return { provider: null, model: null };
  const separator = mainModel.indexOf("/");
  return separator > 0
    ? { provider: mainModel.slice(0, separator), model: mainModel.slice(separator + 1) }
    : { provider: null, model: mainModel };
}

async function loadProfiles({ fresh = false } = {}) {
  const now = Date.now();
  if (!fresh && profileCache && now - profileCache.time < 5000) return profileCache.value;
  const [profilesResult, preflightResult] = await Promise.all([
    runJson("node", [ZCODECTL, "api-profiles"]),
    runJson("node", [ZCODECTL, "cli-preflight"]),
  ]);
  const profiles = Array.isArray(profilesResult.profiles)
    ? profilesResult.profiles.map((profile) => ({
        id: profile.id,
        name: profile.name || profile.id,
        kind: profile.kind || null,
        models: Array.isArray(profile.models) ? profile.models : [],
      }))
    : [];
  const selection = selectedModel(preflightResult.config?.main_model);
  const value = { profiles, selection };
  profileCache = { time: now, value };
  return value;
}

function finiteNumber(...values) {
  for (const value of values) if (Number.isFinite(value)) return value;
  return null;
}

function isoTime(value) {
  const parsed = typeof value === "string" ? Date.parse(value) : Number(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
}

function flattenChangedFiles(audit) {
  const changed = audit?.changed_files;
  if (!changed || typeof changed !== "object") return [];
  return [...new Set([...(changed.added || []), ...(changed.modified || []), ...(changed.deleted || [])])];
}

export function normalizeStatus(run) {
  if (run?.status === "success" && run?.ok === true) return "success";
  if (["running", "retrying", "preparing", "queued"].includes(run?.status)) return run.status;
  if (run?.timed_out === true || run?.status === "run_timeout") return "timeout";
  if (run?.status === "aborted") return "aborted";
  return run?.status || (run?.ok === false ? "failed" : "unknown");
}

async function fileTimes(path) {
  try {
    const info = await stat(path);
    return { created: info.birthtime.toISOString(), modified: info.mtime.toISOString() };
  } catch {
    return { created: null, modified: null };
  }
}

function usageFromRun(run) {
  const accounting = run?.usage_accounting || {};
  const normalized = run?.usage_normalized || {};
  const usage = run?.usage || {};
  const total = finiteNumber(
    run?.worker_total_tokens,
    accounting.worker_total_tokens,
    accounting.tokens_total,
    accounting.tokens_used,
    normalized.total_tokens,
    usage.total_tokens,
  );
  return {
    available: total !== null,
    total,
    input: finiteNumber(accounting.input_tokens, normalized.input_tokens, usage.input_tokens, usage.prompt_tokens),
    output: finiteNumber(accounting.output_tokens, normalized.output_tokens, usage.output_tokens, usage.completion_tokens),
    reasoning: finiteNumber(accounting.reasoning_tokens, normalized.reasoning_tokens, usage.reasoning_tokens),
    source: accounting.tokens_source || run?.worker_usage_capture_method || null,
    unavailable_reason: total === null ? (accounting.no_usage_reason || run?.no_usage_reason || "token_usage_unavailable") : null,
  };
}

async function loadRunTask(path, jobs) {
  const run = await readArtifactJson(path);
  if (!run) return null;
  const workspace = resolve(path, "..", "..", "..", "..");
  const packet = typeof run.packet === "string" && pathInside(workspace, run.packet)
    ? await readArtifactJson(run.packet)
    : null;
  const times = await fileTimes(path);
  const startedAt = isoTime(packet?.created_at) || times.created;
  const terminal = !["running", "retrying", "preparing", "queued"].includes(normalizeStatus(run));
  const completedAt = terminal ? times.modified : null;
  const startedMs = startedAt ? Date.parse(startedAt) : null;
  const completedMs = completedAt ? Date.parse(completedAt) : Date.now();
  const job = jobs.find((candidate) => {
    if (candidate.run_path && resolve(candidate.run_path) === resolve(path)) return true;
    const delta = startedMs && candidate.started_at ? Math.abs(startedMs - Date.parse(candidate.started_at)) : Infinity;
    return !candidate.run_path && candidate.objective === packet?.objective && delta < 15_000;
  });
  const accounting = run.usage_accounting || {};
  const quotaEvidenceAvailable = Boolean(
    accounting.quota_source
    || run.usage_snapshots?.before?.ok === true
    || run.usage_snapshots?.after?.ok === true
  );
  const audit = run.audit || run.accepted_audit || null;
  return {
    id: path.split(/[\\/]/).pop().replace(/\.zcode\.json$/, ""),
    objective: packet?.objective || job?.objective || "未命名任务",
    status: normalizeStatus(run),
    provider: job?.provider || run.provider_id || (quotaEvidenceAvailable ? accounting.quota_provider : null),
    model: job?.model || run.worker_model || null,
    tokens: usageFromRun(run),
    duration_ms: startedMs && Number.isFinite(completedMs) ? Math.max(0, completedMs - startedMs) : null,
    changed_files: flattenChangedFiles(audit),
    allowed_files: Array.isArray(packet?.allowed_files) ? packet.allowed_files : [],
    started_at: startedAt,
    completed_at: completedAt,
    attempt_count: finiteNumber(run.attempt_count, run.attempts),
    audit,
    validation: run.validation || audit?.validation || null,
    failure_reason: run.failure_reason || run.blocker_kind || run.provider_error_kind || null,
    artifact_quality: audit?.artifact_quality || null,
    run_path: path,
    packet_path: run.packet || null,
  };
}

async function loadJobs(runDirectory) {
  let names = [];
  try {
    names = await readdir(runDirectory);
  } catch {
    return [];
  }
  const jobs = await Promise.all(
    names.filter((name) => name.endsWith(".dashboard.json")).map((name) => readArtifactJson(join(runDirectory, name))),
  );
  return jobs.filter(Boolean);
}

function standaloneJob(job) {
  const started = isoTime(job.started_at);
  const completed = isoTime(job.completed_at);
  return {
    id: job.id,
    objective: job.objective,
    status: job.status || "preparing",
    provider: job.provider,
    model: job.model,
    tokens: { available: false, total: null, input: null, output: null, reasoning: null, source: null, unavailable_reason: "task_not_completed" },
    duration_ms: started ? Math.max(0, (completed ? Date.parse(completed) : Date.now()) - Date.parse(started)) : null,
    changed_files: [],
    allowed_files: job.allowed_files || [],
    started_at: started,
    completed_at: completed,
    attempt_count: null,
    audit: null,
    validation: null,
    failure_reason: job.error || null,
    artifact_quality: null,
    run_path: job.run_path || null,
    packet_path: null,
  };
}

async function loadTasks(workspace) {
  const runDirectory = join(workspace, ".codex", "zcode", "runs");
  let names = [];
  try {
    names = await readdir(runDirectory);
  } catch {
    return [];
  }
  const jobs = await loadJobs(runDirectory);
  const runPaths = names.filter((name) => name.endsWith(".zcode.json")).map((name) => join(runDirectory, name));
  const tasks = (await Promise.all(runPaths.map((path) => loadRunTask(path, jobs)))).filter(Boolean);
  const linkedJobIds = new Set();
  for (const task of tasks) {
    const match = jobs.find((job) => (job.run_path && resolve(job.run_path) === resolve(task.run_path)) || (!job.run_path && job.objective === task.objective));
    if (match) linkedJobIds.add(match.id);
  }
  for (const job of jobs) if (!linkedJobIds.has(job.id)) tasks.push(standaloneJob(job));
  return tasks.sort((left, right) => Date.parse(right.started_at || 0) - Date.parse(left.started_at || 0));
}

export function summarize(tasks) {
  const running = tasks.filter((task) => ["queued", "preparing", "running", "retrying"].includes(task.status)).length;
  const success = tasks.filter((task) => task.status === "success").length;
  const terminal = tasks.filter((task) => !["queued", "preparing", "running", "retrying", "unknown"].includes(task.status)).length;
  const measured = tasks.filter((task) => task.tokens.available);
  const durations = tasks.map((task) => task.duration_ms).filter(Number.isFinite);
  return {
    total_tasks: tasks.length,
    running_tasks: running,
    successful_tasks: success,
    success_rate: terminal > 0 ? success / terminal : null,
    measured_token_tasks: measured.length,
    total_tokens: measured.length > 0 ? measured.reduce((sum, task) => sum + task.tokens.total, 0) : null,
    total_duration_ms: durations.length > 0 ? durations.reduce((sum, value) => sum + value, 0) : null,
    average_duration_ms: durations.length > 0 ? durations.reduce((sum, value) => sum + value, 0) / durations.length : null,
  };
}

async function dashboardPayload(workspace) {
  const [{ profiles, selection }, tasks] = await Promise.all([loadProfiles(), loadTasks(workspace)]);
  return {
    ok: true,
    workspace,
    generated_at: new Date().toISOString(),
    selection,
    profiles,
    summary: summarize(tasks),
    tasks,
    active_job: activeJob ? { id: activeJob.id, started_at: activeJob.started_at } : null,
  };
}

function validateSelection(payload, profiles) {
  const provider = profiles.find((item) => item.id === payload?.provider);
  if (!provider) throw Object.assign(new Error("请选择有效的 API"), { statusCode: 400 });
  const model = provider.models.find((item) => item.toLowerCase() === String(payload?.model || "").toLowerCase());
  if (!model) throw Object.assign(new Error("请选择该 API 下已配置的模型"), { statusCode: 400 });
  return { provider: provider.id, model };
}

async function applySelection(selection) {
  const payload = await runJson("node", [
    ZCODECTL,
    "bootstrap-cli-config",
    "--provider",
    selection.provider,
    "--model",
    selection.model,
  ]);
  if (payload.ok !== true) throw new Error(payload.error || "API 配置切换失败");
  profileCache = null;
  return payload;
}

export function parseAllowedFiles(value) {
  const items = Array.isArray(value) ? value : String(value || "").split(/[\n,]/);
  const files = [...new Set(items.map((item) => String(item).trim().replaceAll("\\", "/")).filter(Boolean))];
  if (files.length < 1 || files.length > 20) throw Object.assign(new Error("允许文件数量必须为 1–20 个"), { statusCode: 400 });
  for (const file of files) {
    if (file.length > 300 || file.startsWith("/") || /^[A-Za-z]:[\\/]/.test(file) || file.split(/[\\/]/).includes("..")) {
      throw Object.assign(new Error(`允许文件必须是工作区内的相对路径：${file}`), { statusCode: 400 });
    }
    if (SECRET_PATH_PATTERN.test(file)) throw Object.assign(new Error(`不允许委派敏感路径：${file}`), { statusCode: 400 });
  }
  return files;
}

async function writeJob(workspace, job) {
  const directory = join(workspace, ".codex", "zcode", "runs");
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, `${job.id}.dashboard.json`), `${JSON.stringify(job, null, 2)}\n`, "utf8");
}

function parseLastJson(stdout) {
  try {
    return JSON.parse(stdout);
  } catch {
    const lines = stdout.trim().split(/\r?\n/).reverse();
    for (const line of lines) {
      try { return JSON.parse(line); } catch { /* continue */ }
    }
    return null;
  }
}

async function launchTask(workspace, raw) {
  if (activeJob || taskLaunchPending) throw Object.assign(new Error("当前已有任务运行，请等待完成后再启动下一项"), { statusCode: 409 });
  taskLaunchPending = true;
  try {
    if (!(await readArtifactJson(join(workspace, ".codex", "zcode-routing.json")))) {
      throw Object.assign(new Error("目标工作区尚未安装 ZCode 路由，请先运行 setup-windows.ps1 或 zcode-install-repo"), { statusCode: 412 });
    }
  const objective = String(raw?.objective || "").trim();
  const validation = String(raw?.validation || "").trim();
  if (objective.length < 3 || objective.length > 4000) throw Object.assign(new Error("任务目标长度必须为 3–4000 个字符"), { statusCode: 400 });
  if (!validation || validation.length > 2000) throw Object.assign(new Error("请填写有效的验证命令"), { statusCode: 400 });
  const allowedFiles = parseAllowedFiles(raw?.allowed_files);
  const taskClass = TASK_CLASSES.has(raw?.task_class) ? raw.task_class : "small-fix";
  const { profiles } = await loadProfiles({ fresh: true });
  const selection = validateSelection(raw, profiles);
  await applySelection(selection);

  const id = `dashboard-${new Date().toISOString().replace(/\D/g, "").slice(0, 17)}`;
  const job = {
    version: 1,
    id,
    objective,
    provider: selection.provider,
    model: selection.model,
    allowed_files: allowedFiles,
    validation,
    task_class: taskClass,
    status: "preparing",
    started_at: new Date().toISOString(),
    completed_at: null,
    run_path: null,
    error: null,
  };
  await writeJob(workspace, job);
  activeJob = job;
  const args = [
    SUPERVISOR,
    "auto-route",
    "--workspace", workspace,
    "--objective", objective,
    "--task-kind", "implementation",
    "--validation", validation,
    "--execute",
    "--run-mode", "edit",
    "--task-class", taskClass,
    "--risk-budget", "low",
    "--workspace-kind", "regular",
    "--max-changed-files", String(allowedFiles.length),
    "--max-attempts", "1",
    "--usage-snapshot-source", "none",
    "--no-repair-validation",
    "--result-verbosity", "compact",
  ];
  for (const file of allowedFiles) args.push("--allowed", file);
  const child = spawn(PYTHON, args, { cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  job.status = "running";
  await writeJob(workspace, job);
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout = `${stdout}${chunk}`.slice(-MAX_CAPTURE_BYTES); });
  child.stderr.on("data", (chunk) => { stderr = `${stderr}${chunk}`.slice(-MAX_CAPTURE_BYTES); });
  child.on("error", async (error) => {
    job.status = "failed";
    job.completed_at = new Date().toISOString();
    job.error = errorMessage(error);
    await writeJob(workspace, job).catch(() => {});
    if (activeJob?.id === job.id) activeJob = null;
  });
  child.on("close", async (code) => {
    const route = parseLastJson(stdout);
    job.status = code === 0 && route?.ok === true ? "success" : "failed";
    job.completed_at = new Date().toISOString();
    job.run_path = typeof route?.run === "string" ? route.run : null;
    job.error = job.status === "failed" ? (route?.failure_reason || stderr.trim().slice(-1000) || `任务进程退出码 ${code}`) : null;
    await writeJob(workspace, job).catch(() => {});
    if (activeJob?.id === job.id) activeJob = null;
  });
    return job;
  } finally {
    taskLaunchPending = false;
  }
}

async function readRequestJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error("请求内容过大"), { statusCode: 413 });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    throw Object.assign(new Error("请求 JSON 无效"), { statusCode: 400 });
  }
}

function allowMutation(request, port) {
  const origin = request.headers.origin;
  if (!origin) return true;
  try {
    const parsed = new URL(origin);
    return ["127.0.0.1", "localhost", "[::1]", "::1"].includes(parsed.hostname) && Number(parsed.port || 80) === port;
  } catch {
    return false;
  }
}

async function serveStatic(pathname, response) {
  const target = STATIC_FILES.get(pathname);
  if (!target) return false;
  const [name, contentType] = target;
  const body = await readFile(join(PUBLIC_DIR, name));
  response.writeHead(200, {
    "Content-Type": contentType,
    "Content-Length": body.length,
    "Cache-Control": "no-cache",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
  });
  response.end(body);
  return true;
}

export function createDashboardServer(options) {
  const workspace = resolve(options.workspace);
  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${request.headers.host || `127.0.0.1:${options.port}`}`);
      if (request.method === "GET" && url.pathname === "/api/dashboard") {
        return jsonResponse(response, 200, await dashboardPayload(workspace));
      }
      if (request.method === "POST" && ["/api/selection", "/api/tasks"].includes(url.pathname)) {
        if (!allowMutation(request, options.port)) return jsonResponse(response, 403, { ok: false, error: "跨来源写入已阻止" });
        const body = await readRequestJson(request);
        if (url.pathname === "/api/selection") {
          if (activeJob || taskLaunchPending) return jsonResponse(response, 409, { ok: false, error: "任务运行时不能切换 API" });
          const { profiles } = await loadProfiles({ fresh: true });
          const selection = validateSelection(body, profiles);
          await applySelection(selection);
          return jsonResponse(response, 200, { ok: true, selection });
        }
        const job = await launchTask(workspace, body);
        return jsonResponse(response, 202, { ok: true, task: standaloneJob(job) });
      }
      if (request.method === "GET" && await serveStatic(url.pathname, response)) return;
      jsonResponse(response, 404, { ok: false, error: "未找到页面" });
    } catch (error) {
      jsonResponse(response, error.statusCode || 500, { ok: false, error: errorMessage(error) });
    }
  });
}

function openBrowser(url) {
  const command = process.platform === "win32" ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  const child = spawn(command, args, { detached: true, stdio: "ignore", windowsHide: true });
  child.unref();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return printHelp();
  await stat(args.workspace);
  const server = createDashboardServer(args);
  await new Promise((resolveReady, reject) => {
    server.once("error", reject);
    server.listen(args.port, args.host, resolveReady);
  });
  const displayHost = args.host === "::1" ? "[::1]" : args.host;
  const url = `http://${displayHost}:${args.port}`;
  console.log(`ZCode Dashboard 已启动：${url}`);
  console.log(`工作区：${args.workspace}`);
  if (args.open) openBrowser(url);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main().catch((error) => {
    console.error(`zcode-dashboard: ${errorMessage(error)}`);
    process.exitCode = 1;
  });
}
