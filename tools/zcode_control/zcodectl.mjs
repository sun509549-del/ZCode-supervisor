#!/usr/bin/env node
// Minimal Codex-side controller for ZCode.
// Uses stable surfaces first: app metadata, cua-driver launch, and Electron CDP.

import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { access, chmod, mkdir, open, readFile, realpath, rename, unlink, writeFile } from "node:fs/promises";
import http from "node:http";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, extname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { openUsageExpression, summaryExpression, usageSnapshotExpression } from "./browser_scripts.mjs";
import {
  DEFAULT_PROVIDER_MAX_ATTEMPTS,
  DEFAULT_PROVIDER_RATE_LIMIT_FAIL_FAST_COUNT,
  DEFAULT_PROVIDER_RETRY_DELAY_MS,
  classifyProviderError,
  classifyProviderRunState,
  providerRateLimit1302Count,
  usageAvailableFromStdout,
} from "./provider_errors.mjs";

const execFileAsync = promisify(execFile);
const DEFAULT_PORT = 9223;
const DEFAULT_BUNDLE_ID = "dev.zcode.app";
const MACOS_ZCODE_CLI = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs";
const WINDOWS_UNINSTALL_ROOTS = [
  "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
  "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
  "HKLM\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
];
const PROMPT_TIMEOUT_MS = 30 * 60 * 1000;
const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const SUPERVISOR_SCRIPT = resolve(TOOL_DIR, "..", "zcode_supervisor", "zcode_supervisor.py");
const MODEL_USAGE_DB_DELTA_SCRIPT = resolve(TOOL_DIR, "..", "zcode_eval", "zcode_model_usage_db_delta.py");
const MODEL_USAGE_DB_DELTA_SOURCE_TYPE = "zcode_cli_model_usage_db_delta";
const MODEL_USAGE_DB_DELTA_BEFORE_NAME = "zcode-model-usage-before.json";
const MODEL_USAGE_DB_DELTA_AFTER_NAME = "zcode-model-usage-delta.json";
const WORKER_USAGE_LEDGER_NAME = "worker-usage.jsonl";
const DEFAULT_USAGE_PROVIDER = "zai";
const DEFAULT_USAGE_SNAPSHOT_TIMEOUT_MS = 20_000;
const DEFAULT_ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit";
const DEFAULT_VISION_SERVICE = "zai-mcp-server";
const IMAGE_EXTENSIONS = new Set([".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"]);
const SECRET_PATH_NEEDLES = [".env", "id_rsa", "id_ed25519", ".ssh", "credential", "credentials"];
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
const SENSITIVE_CHILD_ENV_PATTERN = /(?:^|_)(?:API_?KEY|ACCESS_?KEY(?:_ID)?|PRIVATE_?KEY|CLIENT_?SECRET|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|COOKIE|SESSION)(?:$|_)/i;
const SENSITIVE_CHILD_ENV_NAMES = new Set(["SSH_AUTH_SOCK", "GIT_ASKPASS", "SSH_ASKPASS"]);

function usage() {
  console.log(`zcodectl

Usage:
  node tools/zcode_control/zcodectl.mjs doctor
  node tools/zcode_control/zcodectl.mjs cli-path
  node tools/zcode_control/zcodectl.mjs cli-doctor
  node tools/zcode_control/zcodectl.mjs cli-preflight
  node tools/zcode_control/zcodectl.mjs cli-version
  node tools/zcode_control/zcodectl.mjs api-profiles [--source-config <json>] [--out <json>]
  node tools/zcode_control/zcodectl.mjs bootstrap-cli-config [--provider <zcode-provider-id>] [--model <model-id>] [--source-config <json>] [--cli-config <json>] [--out <json>]
  node tools/zcode_control/zcodectl.mjs vision-preflight [--workspace <path>] [--vision-service zai-mcp-server] [--cli-config <json>] [--out <json>]
  node tools/zcode_control/zcodectl.mjs cli-prompt (--text <prompt> | --text-file <path>) [--workspace <path>] [--mode plan|edit|build|yolo] [--timeout-ms 1800000] [--json] [--out <json>]
  node tools/zcode_control/zcodectl.mjs run-packet --packet <json> [--mode plan|edit|build|yolo] [--max-attempts 2] [--retry-delay-ms 60000] [--timeout-ms 1800000] [--validation-timeout 60] [--repair-validation|--no-repair-validation] [--accept-validated-artifact-after-ms <ms>] [--provider-rate-limit-fail-fast-count 3|--no-provider-rate-limit-fail-fast] [--usage-snapshot-source auto|zai-api|codexbar|none] [--usage-provider zai] [--model-usage-db <sqlite>] [--vision-preflight auto|required|off] [--json] [--out <json>]
  node tools/zcode_control/zcodectl.mjs app-run-packet --packet <json> [--port 9223] [--timeout-ms 300000] [--interval-ms 2000] [--validation-timeout 60] [--allow-submit] [--require-workspace-bound] [--expected-workspace <path>] [--model-usage-db <sqlite>] [--out <json>]
  node tools/zcode_control/zcodectl.mjs launch [--port 9223] [--new-instance]
  node tools/zcode_control/zcodectl.mjs targets [--port 9223]
  node tools/zcode_control/zcodectl.mjs text [--port 9223] [--max 4000]
  node tools/zcode_control/zcodectl.mjs eval --expr <js> [--port 9223]
  node tools/zcode_control/zcodectl.mjs textboxes [--port 9223]
  node tools/zcode_control/zcodectl.mjs buttons [--port 9223]
  node tools/zcode_control/zcodectl.mjs summary [--port 9223]
  node tools/zcode_control/zcodectl.mjs open-usage [--port 9223]
  node tools/zcode_control/zcodectl.mjs usage [--out <json>] [--port 9223]
  node tools/zcode_control/zcodectl.mjs new-task --workspace <name> [--port 9223]
  node tools/zcode_control/zcodectl.mjs set-mode --mode <mode> [--port 9223]
  node tools/zcode_control/zcodectl.mjs set-composer (--text <text> | --text-file <path>) [--port 9223]
  node tools/zcode_control/zcodectl.mjs submit-task (--text <prompt> | --text-file <path>) [--port 9223]
  node tools/zcode_control/zcodectl.mjs goal (--text <prompt> | --text-file <path>) [--port 9223]
  node tools/zcode_control/zcodectl.mjs wait-idle [--timeout-ms 300000] [--interval-ms 2000] [--port 9223]
  node tools/zcode_control/zcodectl.mjs click --text <label> [--port 9223]
  node tools/zcode_control/zcodectl.mjs click-contains --text <needle> [--port 9223]
  node tools/zcode_control/zcodectl.mjs screenshot --out <png> [--port 9223]

Notes:
  - launch uses cua-driver with Electron remote debugging enabled.
  - cli-* and run-packet use the bundled ZCode headless CLI when available.
  - app-run-packet is a non-live scaffold for fake/fixture CDP flows. Real app submit requires --allow-submit and ZCODE_APP_CDP_ALLOW_SUBMIT=1.
  - vision-preflight checks for the ZCode/Z.AI image MCP service without printing secrets.
  - run-packet captures before/after quota snapshots via the Z.AI API or CodexBar when available.
  - eval runs JavaScript inside the ZCode renderer. Do not use it for secrets.
`);
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const args = { command, port: DEFAULT_PORT };
  for (let index = 0; index < rest.length; index += 1) {
    const arg = rest[index];
    if (arg === "--port") args.port = Number(rest[++index]);
    else if (arg === "--max") args.max = Number(rest[++index]);
    else if (arg === "--expr") args.expr = rest[++index];
    else if (arg === "--out") args.out = rest[++index];
    else if (arg === "--text") args.text = rest[++index];
    else if (arg === "--text-file") args.textFile = rest[++index];
    else if (arg === "--packet") args.packet = rest[++index];
    else if (arg === "--workspace") args.workspace = rest[++index];
    else if (arg === "--mode") args.mode = rest[++index];
    else if (arg === "--provider") args.provider = rest[++index];
    else if (arg === "--model") args.model = rest[++index];
    else if (arg === "--lite-model") args.liteModel = rest[++index];
    else if (arg === "--source-config") args.sourceConfig = rest[++index];
    else if (arg === "--cli-config") args.cliConfig = rest[++index];
    else if (arg === "--json") args.json = true;
    else if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--no-bootstrap") args.noBootstrap = true;
    else if (arg === "--attach") {
      args.attach ??= [];
      args.attach.push(rest[++index]);
    }
    else if (arg === "--resume") args.resume = rest[++index];
    else if (arg === "--continue") args.continue = true;
    else if (arg === "--target") args.target = rest[++index];
    else if (arg === "--target-replace") args.targetReplace = true;
    else if (arg === "--timeout-ms") args.timeoutMs = Number(rest[++index]);
    else if (arg === "--interval-ms") args.intervalMs = Number(rest[++index]);
    else if (arg === "--max-attempts") args.maxAttempts = Number(rest[++index]);
    else if (arg === "--retry-delay-ms") args.retryDelayMs = Number(rest[++index]);
    else if (arg === "--validation-timeout") args.validationTimeout = Number(rest[++index]);
    else if (arg === "--repair-validation") args.repairValidation = true;
    else if (arg === "--no-repair-validation") args.repairValidation = false;
    else if (arg === "--accept-validated-artifact-after-ms") args.acceptValidatedArtifactAfterMs = Number(rest[++index]);
    else if (arg === "--provider-rate-limit-fail-fast-count") args.providerRateLimitFailFastCount = Number(rest[++index]);
    else if (arg === "--no-provider-rate-limit-fail-fast") args.providerRateLimitFailFast = false;
    else if (arg === "--usage-snapshot-source") args.usageSnapshotSource = rest[++index];
    else if (arg === "--usage-provider") args.usageProvider = rest[++index];
    else if (arg === "--usage-snapshot-timeout-ms") args.usageSnapshotTimeoutMs = Number(rest[++index]);
    else if (arg === "--model-usage-db") args.modelUsageDb = rest[++index];
    else if (arg === "--expected-workspace") args.expectedWorkspace = rest[++index];
    else if (arg === "--codexbar-path") args.codexbarPath = rest[++index];
    else if (arg === "--zai-quota-url") args.zaiQuotaUrl = rest[++index];
    else if (arg === "--vision-preflight") args.visionPreflight = rest[++index];
    else if (arg === "--vision-service") args.visionService = rest[++index];
    else if (arg === "--allow-submit") args.allowSubmit = true;
    else if (arg === "--require-workspace-bound") args.requireWorkspaceBound = true;
    else if (arg === "--new-instance") args.newInstance = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return args;
}

async function readPrompt(args) {
  if (args.text && args.textFile) {
    throw new Error("Use either --text or --text-file, not both");
  }
  if (args.textFile) {
    return readFile(resolve(args.textFile), "utf8");
  }
  if (args.text) return args.text;
  throw new Error("--text or --text-file is required");
}

async function runJson(cmd, args) {
  const { stdout } = await execFileAsync(cmd, args, {
    maxBuffer: 10 * 1024 * 1024,
  });
  return JSON.parse(stdout);
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function homeFile(...parts) {
  const userHome = process.env.HOME || process.env.USERPROFILE || homedir();
  if (!userHome) throw new Error("User home directory could not be resolved");
  return join(userHome, ...parts);
}

function pythonCommand() {
  return process.env.ZCODE_SUPERVISOR_PYTHON || (process.platform === "win32" ? "python" : "python3");
}

function defaultCliConfigPath() {
  return homeFile(".zcode", "cli", "config.json");
}

function defaultGuiConfigPath() {
  return homeFile(".zcode", "v2", "config.json");
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sanitizeChildEnv(baseEnv = process.env, extraEnv = {}) {
  const env = { ...baseEnv };
  for (const name of GIT_CONTROL_ENV_VARS) delete env[name];
  for (const name of Object.keys(env)) {
    if (SENSITIVE_CHILD_ENV_PATTERN.test(name) || SENSITIVE_CHILD_ENV_NAMES.has(name.toUpperCase())) {
      delete env[name];
    }
  }
  return { ...env, ...extraEnv };
}

async function readJsonFile(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function readJsonFileOrEmpty(path) {
  if (!(await pathExists(path))) return {};
  const value = await readJsonFile(path);
  if (!isRecord(value)) throw new Error(`Config file must be a JSON object: ${path}`);
  return value;
}

async function writeJsonFileAtomic(path, value) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const tempPath = join(
    dirname(path),
    `.config.json.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`,
  );
  try {
    await writeFile(tempPath, `${JSON.stringify(value, null, 2)}\n`, {
      mode: 0o600,
    });
    await rename(tempPath, path);
    await chmod(path, 0o600).catch(() => {});
  } catch (error) {
    await unlink(tempPath).catch(() => {});
    throw error;
  }
}

function defaultZcodeCliCandidates() {
  const candidates = [];
  if (process.platform === "darwin") candidates.push(MACOS_ZCODE_CLI);
  if (process.platform === "win32") {
    for (const root of [
      process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, "Programs", "ZCode") : null,
      process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, "ZCode") : null,
      process.env.ProgramFiles ? join(process.env.ProgramFiles, "ZCode") : null,
      process.env["ProgramFiles(x86)"] ? join(process.env["ProgramFiles(x86)"], "ZCode") : null,
    ].filter(Boolean)) {
      candidates.push(join(root, "resources", "glm", "zcode.cjs"));
    }
  }
  return candidates;
}

function windowsInstallDirsFromRegistry(raw) {
  const installDirs = [];
  for (const line of String(raw ?? "").split(/\r?\n/)) {
    const match = line.match(/^\s+(DisplayIcon|UninstallString)\s+REG_\w+\s+(.+?)\s*$/i);
    if (!match) continue;
    let executable = match[2].trim();
    const quoted = executable.match(/^"([^"]+)"/);
    if (quoted) executable = quoted[1];
    else executable = executable.replace(/\s+\/\w.*$/, "").replace(/,\d+$/, "").trim();
    if (executable) installDirs.push(dirname(executable));
  }
  return [...new Set(installDirs)];
}

async function windowsRegistryZcodeCliCandidates() {
  if (process.platform !== "win32") return [];
  const candidates = [];
  for (const root of WINDOWS_UNINSTALL_ROOTS) {
    try {
      const { stdout } = await execFileAsync("reg.exe", ["query", root, "/s", "/f", "ZCode", "/d"], {
        maxBuffer: 2 * 1024 * 1024,
        timeout: 5000,
      });
      for (const installDir of windowsInstallDirsFromRegistry(stdout)) {
        candidates.push(join(installDir, "resources", "glm", "zcode.cjs"));
      }
    } catch {
      // Missing keys and access-denied machine hives are normal for per-user installs.
    }
  }
  return [...new Set(candidates)];
}

async function resolveZcodeCliPath() {
  const candidates = [
    process.env.ZCODE_CLI_PATH,
    ...defaultZcodeCliCandidates(),
    ...(await windowsRegistryZcodeCliCandidates()),
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (await pathExists(candidate)) return candidate;
  }
  throw new Error("ZCode CLI not found. Set ZCODE_CLI_PATH or install ZCode Desktop.");
}

async function runZcodeCli(cliArgs, options = {}) {
  const cliPath = await resolveZcodeCliPath();
  if (
    (options.acceptValidatedArtifactAfterMs > 0 && options.auditValidatedArtifact) ||
    options.providerRateLimitFailFastCount > 0
  ) {
    return runZcodeCliWithValidatedArtifactAccept(cliPath, cliArgs, options);
  }
  const timeout = options.timeoutMs ?? 0;
  try {
    const { stdout, stderr } = await execFileAsync("node", [cliPath, ...cliArgs], {
      cwd: options.cwd ?? process.cwd(),
      env: sanitizeChildEnv(process.env, options.env ?? {}),
      maxBuffer: 50 * 1024 * 1024,
      timeout: timeout > 0 ? timeout : undefined,
    });
    return enrichCliResult({ ok: true, cli_path: cliPath, stdout, stderr, exit_code: 0 });
  } catch (error) {
    const timedOut = timeout > 0 && error.killed === true;
    return enrichCliResult({
      ok: false,
      cli_path: cliPath,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? error.message,
      exit_code: typeof error.code === "number" ? error.code : 1,
      timed_out: timedOut,
      timeout_ms: timedOut ? timeout : null,
    });
  }
}

async function runZcodeCliWithValidatedArtifactAccept(cliPath, cliArgs, options = {}) {
  const timeout = options.timeoutMs ?? 0;
  const acceptAfterMs = options.acceptValidatedArtifactAfterMs ?? 0;
  const auditIntervalMs = options.auditValidatedArtifactIntervalMs ?? 1000;
  const child = spawn("node", [cliPath, ...cliArgs], {
    cwd: options.cwd ?? process.cwd(),
    env: sanitizeChildEnv(process.env, options.env ?? {}),
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stdoutChunks = [];
  const stderrChunks = [];
  let timedOut = false;
  let acceptedAudit = null;
  let providerFailFast = null;
  let settled = false;
  const outputLimit = 50 * 1024 * 1024;

  const appendChunk = (chunks, chunk) => {
    const currentSize = chunks.reduce((total, item) => total + item.length, 0);
    if (currentSize < outputLimit) chunks.push(chunk);
  };
  const finish = (result) => enrichCliResult({
    ...result,
    cli_path: cliPath,
    stdout: Buffer.concat(stdoutChunks).toString("utf8"),
    stderr: Buffer.concat(stderrChunks).toString("utf8"),
  });

  return new Promise((resolveRun) => {
    let timeoutTimer = null;
    let acceptTimer = null;
    let acceptInterval = null;
    let killTimer = null;

    const cleanup = () => {
      if (timeoutTimer) clearTimeout(timeoutTimer);
      if (acceptTimer) clearTimeout(acceptTimer);
      if (acceptInterval) clearInterval(acceptInterval);
      if (killTimer) clearTimeout(killTimer);
    };

    const stopChild = () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGTERM");
        killTimer = setTimeout(() => {
          if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
        }, 2000);
      }
    };

    const tryProviderRateLimitFailFast = () => {
      if (settled) return;
      const threshold = positiveIntOrDefault(options.providerRateLimitFailFastCount, 0);
      if (threshold <= 0) return;
      const stderr = Buffer.concat(stderrChunks).toString("utf8");
      const stdout = Buffer.concat(stdoutChunks).toString("utf8");
      const count = providerRateLimit1302Count(`${stderr}\n${stdout}`);
      if (count < threshold) return;
      settled = true;
      providerFailFast = classifyProviderError({ stdout, stderr, exitCode: 143 });
      stopChild();
    };

    child.stdout.on("data", (chunk) => {
      appendChunk(stdoutChunks, chunk);
      tryProviderRateLimitFailFast();
    });
    child.stderr.on("data", (chunk) => {
      appendChunk(stderrChunks, chunk);
      tryProviderRateLimitFailFast();
    });

    const tryAccept = async () => {
      if (settled) return;
      if (!(acceptAfterMs > 0) || !options.auditValidatedArtifact) return;
      try {
        const audit = await options.auditValidatedArtifact();
        if (
          audit?.ok === true &&
          audit?.validation?.ok === true &&
          Number.isInteger(audit?.changed_count) &&
          audit.changed_count > 0
        ) {
          settled = true;
          acceptedAudit = audit;
          stopChild();
        }
      } catch {
        // The normal post-run audit remains authoritative.
      }
    };

    if (timeout > 0) {
      timeoutTimer = setTimeout(() => {
        if (settled) return;
        timedOut = true;
        settled = true;
        stopChild();
      }, timeout);
    }
    if (acceptAfterMs > 0 && options.auditValidatedArtifact) {
      acceptTimer = setTimeout(() => {
        tryAccept();
        acceptInterval = setInterval(tryAccept, auditIntervalMs);
      }, acceptAfterMs);
    }

    child.on("error", (error) => {
      cleanup();
      resolveRun(finish({
        ok: false,
        stderr: error.message,
        exit_code: 1,
        timed_out: false,
        timeout_ms: null,
      }));
    });

    child.on("close", (code, signal) => {
      cleanup();
      if (acceptedAudit) {
        resolveRun(finish({
          ok: true,
          exit_code: 0,
          accepted_validated_artifact: true,
          accepted_audit: acceptedAudit,
        }));
        return;
      }
      const exitCode = typeof code === "number" ? code : signalExitCode(signal);
      if (providerFailFast) {
        resolveRun(finish({
          ok: false,
          exit_code: exitCode,
          timed_out: false,
          timeout_ms: null,
          provider_fail_fast: true,
          provider_fail_fast_reason: "repeated_provider_rate_limit_1302",
          provider_rate_limit_1302_count: providerFailFast.provider_rate_limit_1302_count,
        }));
        return;
      }
      resolveRun(finish({
        ok: exitCode === 0,
        exit_code: exitCode,
        timed_out: timedOut,
        timeout_ms: timedOut ? timeout : null,
      }));
    });
  });
}

function enrichCliResult(result) {
  const provider = classifyProviderError({
    stdout: result.stdout,
    stderr: result.stderr,
    exitCode: result.exit_code,
  });
  return {
    ...result,
    cli_ok: result.ok,
    usage_available: usageAvailableFromStdout(result.stdout),
    ...provider,
  };
}

async function printCliResult(result, out) {
  if (out) {
    const outputPath = resolve(out);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`);
  }
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (!result.ok) process.exitCode = result.exit_code || 1;
}

async function cliPath() {
  console.log(await resolveZcodeCliPath());
}

async function cliDoctor(out) {
  const result = await runZcodeCli(["doctor", "--json"]);
  await printCliResult(result, out);
}

async function cliVersion(out) {
  const result = await runZcodeCli(["version", "--json"]);
  await printCliResult(result, out);
}

function redactCliConfig(config) {
  const modelConfig = isRecord(config.model) ? config.model : {};
  const providerConfig = isRecord(config.provider) ? config.provider : {};
  const mcpServers = summarizeMcpServers(config);
  const visionService = detectVisionService(config, DEFAULT_VISION_SERVICE);
  const availableModels = Array.isArray(modelConfig.available) ? modelConfig.available : null;
  const availableShapeOk = availableModels === null || availableModels.every(
    (model) => isRecord(model) && typeof model.provider === "string" && typeof model.model === "string",
  );
  const providers = Object.entries(providerConfig).map(([id, provider]) => {
    const providerRecord = isRecord(provider) ? provider : {};
    const options = isRecord(providerRecord.options) ? providerRecord.options : {};
    return {
      id,
      kind: typeof providerRecord.kind === "string" ? providerRecord.kind : null,
      name: typeof providerRecord.name === "string" ? providerRecord.name : null,
      base_url: typeof options.baseURL === "string" ? options.baseURL : null,
      api_key_required: Boolean(options.apiKeyRequired),
      has_api_key: typeof options.apiKey === "string" && options.apiKey.trim().length > 0,
      models: Object.keys(isRecord(providerRecord.models) ? providerRecord.models : {}).sort(),
    };
  });
  const mainModel = typeof config.model === "string"
    ? config.model
    : typeof modelConfig.main === "string"
      ? modelConfig.main
      : null;
  const liteModel = typeof modelConfig.lite === "string" ? modelConfig.lite : null;
  const selectedProviderId = mainModel?.includes("/") ? mainModel.slice(0, mainModel.indexOf("/")) : null;
  const selectedProvider = providers.find((provider) => provider.id === selectedProviderId);
  return {
    has_model: Boolean(mainModel),
    main_model: mainModel,
    lite_model: liteModel,
    provider_ids: providers.map((provider) => provider.id),
    providers,
    mcp_servers: mcpServers,
    vision_service: {
      configured: Boolean(visionService),
      service: DEFAULT_VISION_SERVICE,
      server: visionService?.name ?? null,
    },
    has_coding_plan_api_key: providers.some(
      (provider) => ["zai", "bigmodel"].includes(provider.id) && provider.has_api_key,
    ),
    has_selected_provider_api_key: Boolean(selectedProvider?.has_api_key),
    config_shape_ok: availableShapeOk,
    diagnostics: availableShapeOk
      ? []
      : ["model.available must not be an array of strings in ZCode CLI 0.14.5 config"],
  };
}

function mcpServerEntries(config) {
  const mcpConfig = isRecord(config.mcp) ? config.mcp : {};
  if (isRecord(mcpConfig.servers)) return Object.entries(mcpConfig.servers);
  if (isRecord(config.mcpServers)) return Object.entries(config.mcpServers);
  return [];
}

function summarizeMcpServers(config) {
  return mcpServerEntries(config).map(([name, server]) => {
    const record = isRecord(server) ? server : {};
    return {
      name,
      enabled: record.enable !== false,
      type: typeof record.type === "string" ? record.type : "stdio",
      command: typeof record.command === "string" ? record.command : null,
      args_count: Array.isArray(record.args) ? record.args.length : 0,
      has_env: isRecord(record.env) && Object.keys(record.env).length > 0,
    };
  });
}

function detectVisionService(config, serviceName) {
  const normalizedService = normalizeVisionServiceName(serviceName);
  const compactService = compactVisionServiceName(serviceName);
  const allowZaiAliases = compactService === compactVisionServiceName(DEFAULT_VISION_SERVICE);
  for (const [name, server] of mcpServerEntries(config)) {
    const record = isRecord(server) ? server : {};
    if (record.enable === false) continue;
    const haystack = [
      name,
      record.command,
      ...(Array.isArray(record.args) ? record.args : []),
    ]
      .filter((item) => typeof item === "string")
      .join(" ")
      .toLowerCase();
    const normalizedHaystack = normalizeVisionServiceName(haystack);
    const compactHaystack = compactVisionServiceName(haystack);
    if (
      normalizedHaystack.includes(normalizedService)
      || compactHaystack.includes(compactService)
      || (
        allowZaiAliases
        && (
          compactHaystack.includes("zaimcpserver")
          || compactHaystack.includes("zaimcp")
          || normalizedHaystack.includes("zai-mcp")
        )
      )
    ) {
      return {
        name,
        command: typeof record.command === "string" ? record.command : null,
      };
    }
  }
  return null;
}

function normalizeVisionServiceName(value) {
  return String(value ?? "").toLowerCase().replaceAll("_", "-");
}

function compactVisionServiceName(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

async function inspectMcpConfig(path, source, serviceName) {
  const exists = await pathExists(path);
  if (!exists) {
    return {
      source,
      path,
      exists: false,
      servers: [],
      vision_service: null,
    };
  }
  const config = await readJsonFileOrEmpty(path);
  return {
    source,
    path,
    exists: true,
    note: "secret values are redacted",
    servers: summarizeMcpServers(config),
    vision_service: detectVisionService(config, serviceName),
  };
}

async function inspectVisionServices(args, workspace, serviceName) {
  const configs = [];
  configs.push(await inspectMcpConfig(argsPathOrDefault(args.cliConfig, defaultCliConfigPath()), "user-cli", serviceName));
  configs.push(await inspectMcpConfig(join(workspace, ".zcode", "config.json"), "workspace-zcode", serviceName));
  configs.push(await inspectMcpConfig(join(workspace, ".agents", "mcp.json"), "workspace-agents", serviceName));
  const detected = configs.find((config) => Boolean(config.vision_service));
  return {
    ok: Boolean(detected),
    service: serviceName,
    workspace,
    detected_source: detected?.source ?? null,
    detected_server: detected?.vision_service?.name ?? null,
    configs,
    next_action: detected
      ? null
      : `Configure and enable ${serviceName} in ZCode MCP settings before running required image-understanding tasks.`,
  };
}

async function inspectCliConfig(path = defaultCliConfigPath()) {
  const exists = await pathExists(path);
  if (!exists) {
    return {
      path,
      exists: false,
      note: "CLI config does not exist",
    };
  }
  const config = await readJsonFileOrEmpty(path);
  return {
    path,
    exists: true,
    note: "secret values are redacted",
    ...redactCliConfig(config),
  };
}

async function cliPreflight(args) {
  const cliPathValue = await resolveZcodeCliPath();
  const configPath = argsPathOrDefault(args.cliConfig, defaultCliConfigPath());
  const version = await runZcodeCli(["--version"]);
  const doctorResult = await runZcodeCli(["doctor", "--json"]);
  const config = await inspectCliConfig(configPath);
  const promptReady = Boolean(
    config.exists && config.has_model && config.has_selected_provider_api_key && config.config_shape_ok,
  );
  const payload = {
    ok: version.ok && doctorResult.ok && promptReady,
    cli_path: cliPathValue,
    cli_version: version.stdout.trim() || null,
    config,
    doctor: parseJsonOrText(doctorResult.stdout),
    prompt_ready: promptReady,
  };
  if (!payload.prompt_ready) {
    payload.next_action = "Run zcodectl bootstrap-cli-config, or run ZCode CLI login/configuration before headless prompts.";
  }
  const textPayload = JSON.stringify(payload, null, 2);
  if (args.out) {
    const outputPath = resolve(args.out);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, `${textPayload}\n`);
  }
  console.log(textPayload);
  if (!payload.ok) process.exitCode = 1;
}

function argsPathOrDefault(path, fallback) {
  return path ? resolve(path) : fallback;
}

function normalizeModelId(model) {
  const value = String(model ?? "").trim();
  if (!value) throw new Error("model must not be empty");
  return value.toLowerCase();
}

function sourceProviderCandidates(providerId, guiConfig) {
  const providers = isRecord(guiConfig.provider) ? guiConfig.provider : {};
  const direct = isRecord(providers[providerId]) ? [providerId] : [];
  if (providerId === "zai") {
    return [...direct, "builtin:zai-coding-plan", "builtin:zai", "builtin:zai-start-plan"];
  }
  if (providerId === "bigmodel") {
    return [...direct, "builtin:bigmodel-coding-plan", "builtin:bigmodel", "builtin:bigmodel-start-plan"];
  }
  return direct.length > 0 ? direct : [providerId];
}

function displayProviderName(providerId) {
  if (providerId === "bigmodel") return "Bigmodel Coding Plan";
  if (providerId === "zai") return "Z.AI Coding Plan";
  return providerId;
}

function fallbackProviderBaseUrl(providerId) {
  if (providerId === "bigmodel") return "https://open.bigmodel.cn/api/anthropic";
  if (providerId === "zai") return "https://api.z.ai/api/anthropic";
  return null;
}

function pickSourceProvider(guiConfig, providerId, requestedModel = null) {
  const providers = isRecord(guiConfig.provider) ? guiConfig.provider : {};
  const requestedModelId = requestedModel ? normalizeModelId(requestedModel) : null;
  for (const id of sourceProviderCandidates(providerId, guiConfig)) {
    const provider = providers[id];
    if (!isRecord(provider)) continue;
    const options = isRecord(provider.options) ? provider.options : {};
    const apiKey = typeof options.apiKey === "string" ? options.apiKey.trim() : "";
    if (!apiKey) continue;
    const modelIds = Object.keys(isRecord(provider.models) ? provider.models : {});
    if (modelIds.length === 0) continue;
    if (requestedModelId && !modelIds.some((modelId) => normalizeModelId(modelId) === requestedModelId)) {
      continue;
    }
    return { id, provider, options, apiKey };
  }
  throw new Error(`No usable API profile found for ZCode provider: ${providerId}`);
}

function apiProfileRows(guiConfig) {
  const providers = isRecord(guiConfig.provider) ? guiConfig.provider : {};
  return Object.entries(providers)
    .filter(([, provider]) => isRecord(provider))
    .map(([id, provider]) => {
      const options = isRecord(provider.options) ? provider.options : {};
      const models = isRecord(provider.models) ? Object.keys(provider.models) : [];
      return {
        id,
        name: typeof provider.name === "string" ? provider.name : id,
        kind: typeof provider.kind === "string" ? provider.kind : null,
        models,
        has_api_key: typeof options.apiKey === "string" && Boolean(options.apiKey.trim()),
        has_base_url: typeof options.baseURL === "string" && Boolean(options.baseURL.trim()),
      };
    })
    .filter((profile) => profile.has_api_key && profile.models.length > 0)
    .sort((left, right) => left.name.localeCompare(right.name));
}

async function apiProfiles(args) {
  const sourceConfigPath = argsPathOrDefault(args.sourceConfig, defaultGuiConfigPath());
  const guiConfig = await readJsonFile(sourceConfigPath);
  if (!isRecord(guiConfig)) throw new Error(`GUI config must be a JSON object: ${sourceConfigPath}`);
  await printJsonPayload({
    ok: true,
    source_config: sourceConfigPath,
    note: "API keys and base URLs are not included.",
    profiles: apiProfileRows(guiConfig),
  }, args.out);
}

function sourceModelIds(sourceProvider, preferredModels) {
  const sourceModels = isRecord(sourceProvider.models) ? sourceProvider.models : {};
  const ids = new Map();
  for (const model of [...preferredModels, ...Object.keys(sourceModels)]) {
    const exact = String(model ?? "").trim();
    const normalized = exact ? normalizeModelId(exact) : null;
    if (normalized && !ids.has(normalized)) ids.set(normalized, exact);
  }
  return [...ids.values()];
}

function modelDisplayName(modelId) {
  return modelId
    .split("-")
    .map((part) => (part === "glm" ? "GLM" : part.toUpperCase()))
    .join("-");
}

async function bootstrapCliConfig(args) {
  const providerId = args.provider ?? "zai";
  const sourceConfigPath = argsPathOrDefault(args.sourceConfig, defaultGuiConfigPath());
  const cliConfigPath = argsPathOrDefault(args.cliConfig, defaultCliConfigPath());
  const existedBefore = await pathExists(cliConfigPath);
  const guiConfig = await readJsonFile(sourceConfigPath);
  if (!isRecord(guiConfig)) throw new Error(`GUI config must be a JSON object: ${sourceConfigPath}`);
  const source = pickSourceProvider(guiConfig, providerId, args.model);
  const availableModelIds = Object.keys(isRecord(source.provider.models) ? source.provider.models : {});
  const requestedModelId = normalizeModelId(args.model ?? availableModelIds[0] ?? "glm-5.3");
  const availableByNormalizedId = new Map(
    availableModelIds.map((modelId) => [normalizeModelId(modelId), modelId]),
  );
  if (!availableByNormalizedId.has(requestedModelId)) {
    throw new Error(`Model ${args.model} is not configured for ZCode provider ${source.id}`);
  }
  const mainModelId = availableByNormalizedId.get(requestedModelId);
  const targetProviderId = providerId === "zai" || providerId === "bigmodel" ? providerId : source.id;
  const existing = await readJsonFileOrEmpty(cliConfigPath);
  const providerConfig = isRecord(existing.provider) ? existing.provider : {};
  const existingProvider = isRecord(providerConfig[targetProviderId]) ? providerConfig[targetProviderId] : {};
  const existingOptions = isRecord(existingProvider.options) ? existingProvider.options : {};
  const existingModels = isRecord(existingProvider.models) ? existingProvider.models : {};
  const sourceBaseUrl = typeof source.options.baseURL === "string" && source.options.baseURL.trim()
    ? source.options.baseURL.trim()
    : fallbackProviderBaseUrl(providerId);
  if (!sourceBaseUrl) throw new Error(`No base URL found for ZCode provider: ${source.id}`);
  const configuredModelIds = sourceModelIds(source.provider, [mainModelId]);
  const configuredByNormalizedId = new Map(
    configuredModelIds.map((modelId) => [normalizeModelId(modelId), modelId]),
  );
  const requestedLiteModelId = args.liteModel ? normalizeModelId(args.liteModel) : null;
  if (requestedLiteModelId && !configuredByNormalizedId.has(requestedLiteModelId)) {
    throw new Error(`Lite model ${args.liteModel} is not configured for ZCode provider ${source.id}`);
  }
  const liteModelId = requestedLiteModelId
    ? configuredByNormalizedId.get(requestedLiteModelId)
    : configuredByNormalizedId.has("glm-5-turbo")
      ? configuredByNormalizedId.get("glm-5-turbo")
      : null;
  const modelIds = sourceModelIds(source.provider, liteModelId ? [mainModelId, liteModelId] : [mainModelId]);
  const models = { ...existingModels };
  const sourceModels = isRecord(source.provider.models) ? source.provider.models : {};
  const sourceModelsByNormalizedId = new Map(
    Object.entries(sourceModels).map(([modelId, value]) => [normalizeModelId(modelId), value]),
  );
  for (const modelId of modelIds) {
    const sourceModel = sourceModelsByNormalizedId.get(normalizeModelId(modelId));
    models[modelId] = {
      ...(isRecord(models[modelId]) ? models[modelId] : {}),
      ...(isRecord(sourceModel) ? sourceModel : {}),
      name: isRecord(sourceModel) && typeof sourceModel.name === "string"
        ? sourceModel.name
        : isRecord(models[modelId]) && typeof models[modelId].name === "string"
          ? models[modelId].name
        : modelDisplayName(modelId),
    };
  }
  const modelConfig = isRecord(existing.model) ? { ...existing.model } : {};
  modelConfig.main = `${targetProviderId}/${mainModelId}`;
  if (liteModelId) modelConfig.lite = `${targetProviderId}/${liteModelId}`;
  delete modelConfig.available;
  const nextConfig = {
    ...existing,
    provider: {
      ...providerConfig,
      [targetProviderId]: {
        ...existingProvider,
        kind: typeof source.provider.kind === "string" ? source.provider.kind : "anthropic",
        name: typeof source.provider.name === "string" ? source.provider.name : displayProviderName(targetProviderId),
        options: {
          ...existingOptions,
          ...source.options,
          apiKeyRequired: source.options.apiKeyRequired !== false,
          baseURL: sourceBaseUrl,
          apiKey: source.apiKey,
        },
        models,
      },
    },
    model: modelConfig,
  };
  if (!args.dryRun) await writeJsonFileAtomic(cliConfigPath, nextConfig);
  const payload = {
    ok: true,
    dry_run: Boolean(args.dryRun),
    cli_config: {
      path: cliConfigPath,
      existed_before: existedBefore,
      wrote_file: !args.dryRun,
      permissions: args.dryRun ? null : "0600",
    },
    source: {
      path: sourceConfigPath,
      provider_id: source.id,
      base_url: sourceBaseUrl,
      has_api_key: true,
    },
    target: {
      provider_id: targetProviderId,
      source_provider_id: source.id,
      main_model: modelConfig.main,
      lite_model: modelConfig.lite ?? null,
      configured_models: modelIds.map((modelId) => `${targetProviderId}/${modelId}`),
    },
    secret_handling: "API key copied locally from GUI config to CLI config; secret values were not printed.",
    preflight: redactCliConfig(nextConfig),
  };
  const textPayload = JSON.stringify(payload, null, 2);
  if (!args.quiet && args.out) {
    const outputPath = resolve(args.out);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, `${textPayload}\n`);
  }
  if (!args.quiet) console.log(textPayload);
  return payload;
}

async function ensureCliPromptReady(args) {
  if (args.noBootstrap) return;
  const config = await inspectCliConfig(argsPathOrDefault(args.cliConfig, defaultCliConfigPath()));
  if (config.exists && config.has_model && config.has_selected_provider_api_key && config.config_shape_ok) return;
  await bootstrapCliConfig({
    provider: args.provider,
    model: args.model,
    liteModel: args.liteModel,
    sourceConfig: args.sourceConfig,
    cliConfig: args.cliConfig,
    dryRun: false,
    quiet: true,
  });
}

function parseJsonOrText(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function parseJsonObject(raw) {
  const parsed = parseJsonOrText(raw);
  return isRecord(parsed) ? parsed : null;
}

function parsedJsonObjects(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!trimmed) return [];
  const candidates = [trimmed];
  for (const line of trimmed.split(/\r?\n/)) {
    const candidate = line.trim();
    if (candidate.startsWith("{") && candidate !== trimmed) candidates.push(candidate);
  }
  const objects = [];
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (isRecord(parsed)) objects.push(parsed);
    } catch {
      // CLI stdout may include progress prose; non-JSON chunks are ignored.
    }
  }
  return objects;
}

function finiteNumber(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function firstFiniteNumber(...values) {
  for (const value of values) {
    const number = finiteNumber(value);
    if (number !== null) return number;
  }
  return null;
}

function firstNonEmptyString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function tokenInteger(value, { positive = false } = {}) {
  const number = finiteNumber(value);
  if (number === null || !Number.isInteger(number)) return null;
  if (positive && number <= 0) return null;
  if (!positive && number < 0) return null;
  return number;
}

function tailText(value, max = 2000) {
  return String(value ?? "").slice(-max);
}

function hasTokenUsageShape(usage) {
  return [
    "totalTokens",
    "total_tokens",
    "tokens_total",
    "tokensTotal",
    "tokensUsed",
    "tokens_used",
    "inputTokens",
    "input_tokens",
    "promptTokens",
    "prompt_tokens",
    "outputTokens",
    "output_tokens",
    "completionTokens",
    "completion_tokens",
    "reasoningTokens",
    "reasoning_tokens",
    "reasoning_output_tokens",
    "cacheReadTokens",
    "cache_read_tokens",
    "cacheWriteTokens",
    "cache_write_tokens",
  ].some((key) => finiteNumber(usage?.[key]) !== null);
}

function pushTokenUsageCandidates(value, candidates, seen, depth = 0) {
  if (depth > 5 || !isRecord(value) || seen.has(value)) return;
  seen.add(value);
  if (hasTokenUsageShape(value)) candidates.push(value);
  for (const key of [
    "usage",
    "usage_normalized",
    "usage_accounting",
    "response",
    "result",
    "data",
    "message",
    "payload",
  ]) {
    const nested = value[key];
    if (isRecord(nested)) pushTokenUsageCandidates(nested, candidates, seen, depth + 1);
    else if (Array.isArray(nested)) {
      for (const item of nested) pushTokenUsageCandidates(item, candidates, seen, depth + 1);
    }
  }
}

function tokenUsageCandidates(payload) {
  const candidates = [];
  pushTokenUsageCandidates(payload, candidates, new Set());
  return candidates;
}

function tokenUsageCandidate(payload) {
  const candidates = tokenUsageCandidates(payload);
  return candidates.length > 0 ? candidates[candidates.length - 1] : null;
}

function normalizeTokenUsage(usage) {
  const inputTokens = firstFiniteNumber(
    usage.inputTokens,
    usage.input_tokens,
    usage.promptTokens,
    usage.prompt_tokens,
  );
  const outputTokens = firstFiniteNumber(
    usage.outputTokens,
    usage.output_tokens,
    usage.completionTokens,
    usage.completion_tokens,
  );
  const reasoningTokens = firstFiniteNumber(
    usage.reasoningTokens,
    usage.reasoning_tokens,
    usage.reasoning_output_tokens,
    usage.output_tokens_details?.reasoning_tokens,
    usage.completion_tokens_details?.reasoning_tokens,
  );
  const totalTokens = firstFiniteNumber(
    usage.totalTokens,
    usage.total_tokens,
    usage.tokens_total,
    usage.tokensTotal,
    usage.tokensUsed,
    usage.tokens_used,
  ) ?? (
    inputTokens !== null && outputTokens !== null
      ? inputTokens + outputTokens
      : inputTokens !== null && reasoningTokens !== null
        ? inputTokens + reasoningTokens
        : null
  );
  return {
    total_tokens: totalTokens,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    reasoning_tokens: reasoningTokens,
    cache_read_tokens: firstFiniteNumber(
      usage.cacheReadTokens,
      usage.cache_read_tokens,
      usage.input_tokens_details?.cached_tokens,
      usage.prompt_tokens_details?.cached_tokens,
    ),
    cache_write_tokens: firstFiniteNumber(usage.cacheWriteTokens, usage.cache_write_tokens),
  };
}

function normalizedZcodeUsageFromStdout(stdout) {
  const objects = parsedJsonObjects(stdout);
  let selectedPayload = null;
  let usage = null;
  for (const payload of objects) {
    const candidate = tokenUsageCandidate(payload);
    if (candidate) {
      selectedPayload = payload;
      usage = candidate;
    }
  }
  const payload = selectedPayload ?? (objects.length > 0 ? objects[objects.length - 1] : parseJsonObject(stdout));
  if (!usage) {
    return {
      payload,
      usage: null,
      normalized: null,
      projection: isRecord(payload?.projection) ? payload.projection : null,
      response: payload?.response ?? null,
    };
  }
  const normalized = normalizeTokenUsage(usage);
  return {
    payload,
    usage,
    normalized,
    projection: isRecord(payload?.projection) ? payload.projection : null,
    response: payload?.response ?? null,
  };
}

function pathIsInside(childPath, parentPath) {
  const rel = relative(parentPath, childPath);
  return rel === "" || (Boolean(rel) && !rel.startsWith("..") && !isAbsolute(rel));
}

function providerUsageLedgerTarget(env, packetPath) {
  const raw = env?.ZCODE_PROVIDER_USAGE_LEDGER;
  if (typeof raw !== "string" || !raw.trim()) {
    return { path: null, status: "not_configured" };
  }
  const ledgerPath = resolve(raw.trim());
  const rowDir = dirname(resolve(packetPath));
  if (!pathIsInside(ledgerPath, rowDir)) {
    return { path: ledgerPath, status: "out_of_scope" };
  }
  return { path: ledgerPath, status: "configured" };
}

function ledgerUsagePayload(normalized) {
  const total = tokenInteger(normalized?.total_tokens, { positive: true });
  if (total === null) return null;
  const usage = { total_tokens: total };
  for (const [target, source] of [
    ["input_tokens", normalized?.input_tokens],
    ["output_tokens", normalized?.output_tokens],
    ["reasoning_tokens", normalized?.reasoning_tokens],
    ["cache_read_tokens", normalized?.cache_read_tokens],
    ["cache_write_tokens", normalized?.cache_write_tokens],
  ]) {
    const value = tokenInteger(source);
    if (value !== null) usage[target] = value;
  }
  return usage;
}

function packetTaskId(packet) {
  const strict = isRecord(packet?.strict_contract) ? packet.strict_contract : {};
  const contract = isRecord(strict.task_contract) ? strict.task_contract : {};
  return firstNonEmptyString(packet?.task_id, contract.task_id);
}

function packetRowId(packetPath) {
  return firstNonEmptyString(basename(dirname(resolve(packetPath))));
}

function providerUsageLedgerRecord(runUsage, packet, packetPath, attempt) {
  const usage = ledgerUsagePayload(runUsage?.normalized);
  if (!usage) return null;
  const payload = isRecord(runUsage?.payload) ? runUsage.payload : {};
  const response = isRecord(runUsage?.response) ? runUsage.response : {};
  const usagePayload = isRecord(runUsage?.usage) ? runUsage.usage : {};
  const record = {
    source_type: "provider_usage_ledger",
    unit: "tokens",
    usage,
    provider_call_index: attempt,
  };
  const provider = firstNonEmptyString(
    payload.provider,
    payload.provider_id,
    response.provider,
    response.provider_id,
    usagePayload.provider,
    usagePayload.provider_id,
  );
  const model = firstNonEmptyString(
    payload.model,
    payload.model_id,
    response.model,
    response.model_id,
    usagePayload.model,
    usagePayload.model_id,
  );
  const taskId = packetTaskId(packet);
  const rowId = packetRowId(packetPath);
  if (provider) record.provider = provider;
  if (model) record.model = model;
  if (taskId) record.task_id = taskId;
  else if (rowId) record.row_id = rowId;
  return record;
}

async function appendProviderUsageLedger(runUsage, { env = process.env, packetPath, packet, attempt }) {
  const target = providerUsageLedgerTarget(env, packetPath);
  if (target.status !== "configured") {
    return { appended: false, status: target.status, path: target.path ?? null };
  }
  const record = providerUsageLedgerRecord(runUsage, packet, packetPath, attempt);
  if (!record) {
    return { appended: false, status: "no_measured_token_usage", path: target.path };
  }
  await mkdir(dirname(target.path), { recursive: true, mode: 0o700 });
  const handle = await open(target.path, "a", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(record)}\n`);
  } finally {
    await handle.close();
  }
  return {
    appended: true,
    status: "appended",
    path: target.path,
    source_type: record.source_type,
    total_tokens: record.usage.total_tokens,
  };
}

function usageSnapshotMode(args) {
  const mode = String(
    args.usageSnapshotSource ?? process.env.ZCODE_USAGE_SNAPSHOT_SOURCE ?? "auto",
  ).trim().toLowerCase();
  if (["off", "false", "0"].includes(mode)) return "none";
  if (["auto", "zai-api", "codexbar", "none"].includes(mode)) return mode;
  throw new Error("--usage-snapshot-source must be auto, zai-api, codexbar, or none");
}

function usageProvider(args) {
  return args.usageProvider ?? process.env.ZCODE_USAGE_PROVIDER ?? DEFAULT_USAGE_PROVIDER;
}

function codexBarPath(args) {
  return args.codexbarPath ?? process.env.CODEXBAR_PATH ?? "codexbar";
}

function zaiQuotaUrl(args) {
  return args.zaiQuotaUrl ?? process.env.ZCODE_ZAI_QUOTA_URL ?? DEFAULT_ZAI_QUOTA_URL;
}

function isoFromEpochMillis(value) {
  const number = finiteNumber(value);
  if (number === null || number <= 0) return null;
  const millis = number > 10_000_000_000 ? number : number * 1000;
  const date = new Date(millis);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function providerApiKeyFromConfig(config, providerId) {
  const providers = isRecord(config.provider) ? config.provider : {};
  const provider = isRecord(providers[providerId]) ? providers[providerId] : {};
  const options = isRecord(provider.options) ? provider.options : {};
  const apiKey = typeof options.apiKey === "string" ? options.apiKey.trim() : "";
  return apiKey || null;
}

async function launchctlGetenv(name) {
  try {
    const { stdout } = await execFileAsync("launchctl", ["getenv", name], {
      maxBuffer: 1024 * 1024,
      timeout: 5000,
    });
    return stdout.trim() || null;
  } catch {
    return null;
  }
}

async function resolveZaiQuotaApiKey(args) {
  const envKey = typeof process.env.ZAI_API_KEY === "string" ? process.env.ZAI_API_KEY.trim() : "";
  if (envKey) return { ok: true, api_key: envKey, source: "env:ZAI_API_KEY" };
  const launchctlKey = await launchctlGetenv("ZAI_API_KEY");
  if (launchctlKey) return { ok: true, api_key: launchctlKey, source: "launchctl:ZAI_API_KEY" };
  const cliConfigPath = argsPathOrDefault(args.cliConfig, defaultCliConfigPath());
  try {
    const cliConfig = await readJsonFileOrEmpty(cliConfigPath);
    const apiKey = providerApiKeyFromConfig(cliConfig, "zai");
    if (apiKey) return { ok: true, api_key: apiKey, source: "zcode-cli-config" };
  } catch (error) {
    return { ok: false, source: "zcode-cli-config", error: error.message };
  }
  return { ok: false, source: "zcode-cli-config", error: "ZAI_API_KEY not found" };
}

function normalizeCodexBarWindow(name, value) {
  if (!isRecord(value)) return null;
  return {
    name,
    used_percent: finiteNumber(value.usedPercent ?? value.used_percent),
    reset_description: typeof value.resetDescription === "string" ? value.resetDescription : null,
    resets_at: typeof value.resetsAt === "string" ? value.resetsAt : null,
    window_minutes: firstFiniteNumber(value.windowMinutes, value.window_minutes),
  };
}

function normalizeCodexBarPayload(payload, provider) {
  const rows = Array.isArray(payload) ? payload : isRecord(payload) ? [payload] : [];
  const row = rows.find((item) => item?.provider === provider) ?? rows[0] ?? null;
  const usage = isRecord(row?.usage) ? row.usage : {};
  const windows = {};
  for (const name of ["primary", "secondary", "tertiary"]) {
    const normalized = normalizeCodexBarWindow(name, usage[name]);
    if (normalized) windows[name] = normalized;
  }
  const quotaCandidates = Object.values(windows)
    .filter((window) => window.used_percent !== null)
    .map((window) => ({
      name: window.name,
      value: window.used_percent,
      line: `${window.name}.usedPercent (${window.reset_description ?? "quota window"})`,
    }));
  const best = quotaCandidates[0] ?? null;
  return {
    identity: isRecord(usage.identity) ? usage.identity : {},
    codexbar_source: typeof row?.source === "string" ? row.source : null,
    updated_at: typeof usage.updatedAt === "string" ? usage.updatedAt : null,
    windows,
    best: {
      tokens_total: null,
      tokens_line: null,
      quota_percent: best?.value ?? null,
      quota_percent_line: best?.line ?? null,
    },
    token_candidates: [],
    quota_percent_candidates: quotaCandidates,
  };
}

function normalizeZaiLimit(name, limit) {
  if (!isRecord(limit)) return null;
  const rawUsedPercent = finiteNumber(limit.percentage ?? limit.usedPercent ?? limit.used_percent);
  const usage = finiteNumber(limit.usage);
  const remaining = finiteNumber(limit.remaining);
  const tokenCountsAvailable = usage !== null && remaining !== null;
  const authoritative = rawUsedPercent !== null;
  const unavailableReason = rawUsedPercent === null ? "zai_limit_missing_percentage" : null;
  return {
    name,
    type: typeof limit.type === "string" ? limit.type : null,
    used_percent: rawUsedPercent,
    raw_used_percent: rawUsedPercent,
    non_authoritative_used_percent: null,
    authoritative,
    token_counts_available: tokenCountsAvailable,
    quota_percent_unavailable_reason: unavailableReason,
    reset_description: name === "primary" ? "Tokens limit" : "Time limit",
    resets_at: isoFromEpochMillis(limit.nextResetTime ?? limit.resetsAt ?? limit.resets_at),
    usage,
    remaining,
  };
}

function normalizeZaiApiPayload(payload, provider) {
  const data = isRecord(payload?.data) ? payload.data : {};
  const limits = Array.isArray(data.limits) ? data.limits : [];
  const byType = new Map(limits.filter(isRecord).map((limit) => [limit.type, limit]));
  const candidates = [
    ["primary", byType.get("TOKENS_LIMIT")],
    ["secondary", byType.get("TIME_LIMIT")],
  ];
  const windows = {};
  for (const [name, limit] of candidates) {
    const normalized = normalizeZaiLimit(name, limit);
    if (normalized) windows[name] = normalized;
  }
  const primaryWindow = windows.primary ?? null;
  const quotaCandidates = primaryWindow && primaryWindow.used_percent !== null
    ? [
      {
        name: primaryWindow.name,
        value: primaryWindow.used_percent,
        line: `${primaryWindow.type ?? primaryWindow.name}.percentage (${primaryWindow.reset_description})`,
      },
    ]
    : [];
  const rawQuotaCandidates = Object.values(windows)
    .filter((window) => window.raw_used_percent !== null)
    .map((window) => ({
      name: window.name,
      value: window.raw_used_percent,
      line: `${window.type ?? window.name}.percentage (${window.reset_description})`,
      authoritative: window.authoritative !== false,
      unavailable_reason: window.quota_percent_unavailable_reason ?? null,
    }));
  const best = quotaCandidates[0] ?? null;
  const rawBest = rawQuotaCandidates[0] ?? null;
  return {
    identity: { providerID: provider },
    plan: typeof data.level === "string" ? data.level : null,
    windows,
    best: {
      tokens_total: null,
      tokens_line: null,
      quota_percent: best?.value ?? null,
      quota_percent_line: best?.line ?? null,
      raw_quota_percent: rawBest?.value ?? null,
      quota_percent_authoritative: Boolean(best),
      quota_percent_unavailable_reason: best ? null : primaryWindow?.quota_percent_unavailable_reason ?? null,
    },
    token_candidates: [],
    quota_percent_candidates: quotaCandidates,
    quota_percent_raw_candidates: rawQuotaCandidates,
  };
}

async function captureCodexBarUsageSnapshot(args, phase) {
  const mode = usageSnapshotMode(args);
  const provider = usageProvider(args);
  if (mode === "none") {
    return { ok: false, phase, source: "none", provider, reason: "disabled" };
  }
  const startedAt = new Date().toISOString();
  const command = codexBarPath(args);
  const timeoutMs = positiveIntOrDefault(args.usageSnapshotTimeoutMs, DEFAULT_USAGE_SNAPSHOT_TIMEOUT_MS);
  try {
    const { stdout, stderr } = await execFileAsync(
      command,
      ["usage", "--provider", provider, "--format", "json"],
      {
        maxBuffer: 5 * 1024 * 1024,
        timeout: timeoutMs,
      },
    );
    const payload = JSON.parse(stdout);
    return {
      ok: true,
      phase,
      source: "codexbar",
      provider,
      captured_at: startedAt,
      command,
      stderr_tail: tailText(stderr, 1000),
      raw: payload,
      ...normalizeCodexBarPayload(payload, provider),
    };
  } catch (error) {
    const isMissing = error.code === "ENOENT";
    return {
      ok: false,
      phase,
      source: "codexbar",
      provider,
      captured_at: startedAt,
      command,
      error_type: isMissing ? "not_found" : "command_failed",
      exit_code: typeof error.code === "number" ? error.code : null,
      message: error.message,
      stdout_tail: tailText(error.stdout, 1000),
      stderr_tail: tailText(error.stderr, 1000),
      auto_mode: mode === "auto",
    };
  }
}

function snapshotFailure({ phase, source, provider, startedAt, errorType, message, extra = {} }) {
  return {
    ok: false,
    phase,
    source,
    provider,
    captured_at: startedAt,
    error_type: errorType,
    message,
    ...extra,
  };
}

async function captureZaiApiUsageSnapshot(args, phase) {
  const provider = usageProvider(args);
  const startedAt = new Date().toISOString();
  if (provider !== "zai") {
    return snapshotFailure({
      phase,
      source: "zai-api",
      provider,
      startedAt,
      errorType: "unsupported_provider",
      message: "Z.AI quota API snapshots require --usage-provider zai",
    });
  }
  const credential = await resolveZaiQuotaApiKey(args);
  if (!credential.ok) {
    return snapshotFailure({
      phase,
      source: "zai-api",
      provider,
      startedAt,
      errorType: "missing_api_key",
      message: credential.error,
      extra: { credential_source: credential.source },
    });
  }
  return fetchZaiApiUsageSnapshot(args, phase, provider, startedAt, credential);
}

async function fetchZaiApiUsageSnapshot(args, phase, provider, startedAt, credential) {
  const timeoutMs = positiveIntOrDefault(args.usageSnapshotTimeoutMs, DEFAULT_USAGE_SNAPSHOT_TIMEOUT_MS);
  const url = zaiQuotaUrl(args);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${credential.api_key}`,
        "Content-Type": "application/json",
      },
      signal: controller.signal,
    });
    const text = await response.text();
    const payload = parseJsonOrText(text);
    if (!response.ok) {
      return snapshotFailure({
        phase,
        source: "zai-api",
        provider,
        startedAt,
        errorType: "http_error",
        message: `HTTP ${response.status}`,
        extra: { status_code: response.status, body_tail: tailText(text, 1000) },
      });
    }
    if (!isRecord(payload)) {
      return snapshotFailure({
        phase,
        source: "zai-api",
        provider,
        startedAt,
        errorType: "invalid_json",
        message: "Z.AI quota API response was not a JSON object",
      });
    }
    if (payload.code !== undefined && payload.code !== 200) {
      return snapshotFailure({
        phase,
        source: "zai-api",
        provider,
        startedAt,
        errorType: "api_error",
        message: `Z.AI quota API returned code ${payload.code}`,
        extra: { body_tail: tailText(text, 1000) },
      });
    }
    return {
      ok: true,
      phase,
      source: "zai-api",
      provider,
      captured_at: startedAt,
      credential_source: credential.source,
      raw: payload,
      ...normalizeZaiApiPayload(payload, provider),
    };
  } catch (error) {
    return snapshotFailure({
      phase,
      source: "zai-api",
      provider,
      startedAt,
      errorType: error.name === "AbortError" ? "timeout" : "request_failed",
      message: error.message,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function captureUsageSnapshot(args, phase) {
  const mode = usageSnapshotMode(args);
  const provider = usageProvider(args);
  if (mode === "none") return { ok: false, phase, source: "none", provider, reason: "disabled" };
  if (mode === "zai-api") return captureZaiApiUsageSnapshot(args, phase);
  if (mode === "codexbar") return captureCodexBarUsageSnapshot(args, phase);
  const direct = await captureZaiApiUsageSnapshot(args, phase);
  if (direct.ok) return direct;
  const fallback = await captureCodexBarUsageSnapshot(args, phase);
  return fallback.ok
    ? { ...fallback, fallback_from: { source: direct.source, error_type: direct.error_type, message: direct.message } }
    : { ...fallback, fallback_from: direct };
}

function deriveQuotaWindowDelta(beforeWindow, afterWindow) {
  const before = finiteNumber(beforeWindow?.used_percent);
  const after = finiteNumber(afterWindow?.used_percent);
  const beforeAuthoritative = beforeWindow?.authoritative !== false;
  const afterAuthoritative = afterWindow?.authoritative !== false;
  const authoritative = beforeAuthoritative && afterAuthoritative;
  const resetChanged = Boolean(beforeWindow?.resets_at && afterWindow?.resets_at && beforeWindow.resets_at !== afterWindow.resets_at);
  const rawDelta = before !== null && after !== null ? after - before : null;
  const unavailableReason = !authoritative
    ? beforeWindow?.quota_percent_unavailable_reason
      ?? afterWindow?.quota_percent_unavailable_reason
      ?? "quota_percent_window_non_authoritative"
    : before === null || after === null
      ? "quota_percent_window_missing_used_percent"
      : resetChanged
        ? "quota_window_reset_changed"
        : rawDelta < 0
          ? "quota_percent_delta_negative"
          : null;
  const usedDelta = rawDelta !== null && rawDelta >= 0 && !resetChanged && authoritative
    ? Number(rawDelta.toFixed(4))
    : null;
  return {
    before_used_percent: before,
    after_used_percent: after,
    before_raw_used_percent: finiteNumber(beforeWindow?.raw_used_percent),
    after_raw_used_percent: finiteNumber(afterWindow?.raw_used_percent),
    used_percent_delta: usedDelta,
    authoritative,
    quota_percent_unavailable_reason: usedDelta === null ? unavailableReason : null,
    reset_changed: resetChanged,
    before_resets_at: beforeWindow?.resets_at ?? null,
    after_resets_at: afterWindow?.resets_at ?? null,
    reset_description: afterWindow?.reset_description ?? beforeWindow?.reset_description ?? null,
  };
}

function deriveQuotaUsage(beforeSnapshot, afterSnapshot) {
  const available = Boolean(beforeSnapshot?.ok && afterSnapshot?.ok);
  const sources = new Set([beforeSnapshot?.source, afterSnapshot?.source].filter(Boolean));
  const result = {
    available,
    source: available ? (sources.size === 1 ? [...sources][0] : "mixed") : null,
    provider: afterSnapshot?.provider ?? beforeSnapshot?.provider ?? null,
    quota_percent_direction: "used",
    quota_percent_before: null,
    quota_percent_after: null,
    quota_percent_used: null,
    quota_percent_status: "unavailable",
    quota_percent_unavailable_reason: available ? null : "usage_snapshot_unavailable",
    windows: {},
  };
  if (!available) return result;
  const names = new Set([
    ...Object.keys(beforeSnapshot.windows ?? {}),
    ...Object.keys(afterSnapshot.windows ?? {}),
  ]);
  for (const name of names) {
    result.windows[name] = deriveQuotaWindowDelta(
      beforeSnapshot.windows?.[name],
      afterSnapshot.windows?.[name],
    );
  }
  const primary = result.windows.primary ?? Object.values(result.windows)[0] ?? null;
  if (primary) {
    result.quota_percent_before = primary.before_used_percent;
    result.quota_percent_after = primary.after_used_percent;
    result.quota_percent_used = primary.used_percent_delta;
    result.quota_percent_status = primary.used_percent_delta !== null ? "measured" : "unavailable";
    result.quota_percent_unavailable_reason = primary.used_percent_delta !== null
      ? null
      : primary.quota_percent_unavailable_reason ?? "quota_percent_delta_unavailable";
  } else {
    result.quota_percent_unavailable_reason = "quota_percent_window_unavailable";
  }
  return result;
}

function missingUsageReason(finalResult) {
  if (finalResult?.provider_error) return "provider_error_without_zcode_cli_usage";
  if (finalResult?.cli_ok === false || finalResult?.ok === false) return "zcode_cli_result_missing_usage";
  return "provider_success_without_usage_payload";
}

function buildUsageAccounting(finalResult, beforeSnapshot, afterSnapshot, options = {}) {
  const runUsage = normalizedZcodeUsageFromStdout(finalResult?.stdout ?? "");
  const quota = deriveQuotaUsage(beforeSnapshot, afterSnapshot);
  const outputPath = options.outputPath ?? null;
  const hasDbDelta = Boolean(options.dbDeltaBefore || options.dbDeltaAfter);
  const measuredDbDelta = outputPath && hasDbDelta
    ? measuredDbDeltaUsage(options.dbDeltaAfter, outputPath)
    : null;
  const unavailableDbDelta = outputPath && hasDbDelta
    ? unavailableDbDeltaUsage({
      before: options.dbDeltaBefore,
      after: options.dbDeltaAfter,
      outputPath,
    })
    : null;
  const dbDelta = measuredDbDelta ?? unavailableDbDelta;
  const dbDeltaRequired = outputPath && hasDbDelta;
  const usageAvailable = dbDeltaRequired ? Boolean(measuredDbDelta) : Boolean(runUsage.normalized);
  const stdoutTotal = runUsage.normalized?.total_tokens ?? null;
  return {
    usage_available: usageAvailable,
    no_usage_reason: usageAvailable ? null : dbDelta?.no_usage_reason ?? missingUsageReason(finalResult),
    ...(dbDelta ?? {}),
    tokens_source: measuredDbDelta?.tokens_source ?? (runUsage.normalized && !dbDeltaRequired ? "zcode_cli_json_usage" : null),
    tokens_used: measuredDbDelta?.tokens_used ?? (dbDeltaRequired ? null : stdoutTotal),
    tokens_total: measuredDbDelta?.tokens_total ?? (dbDeltaRequired ? null : stdoutTotal),
    input_tokens: measuredDbDelta?.input_tokens ?? (dbDeltaRequired ? null : runUsage.normalized?.input_tokens ?? null),
    output_tokens: measuredDbDelta?.output_tokens ?? (dbDeltaRequired ? null : runUsage.normalized?.output_tokens ?? null),
    reasoning_tokens: measuredDbDelta?.reasoning_tokens ?? (dbDeltaRequired ? null : runUsage.normalized?.reasoning_tokens ?? null),
    cache_read_tokens: measuredDbDelta?.cache_read_tokens ?? (dbDeltaRequired ? null : runUsage.normalized?.cache_read_tokens ?? null),
    cache_write_tokens: measuredDbDelta?.cache_write_tokens ?? (dbDeltaRequired ? null : runUsage.normalized?.cache_write_tokens ?? null),
    worker_usage_status: measuredDbDelta?.worker_usage_status ?? (dbDeltaRequired ? unavailableDbDelta?.worker_usage_status : (runUsage.normalized ? "measured" : null)),
    worker_usage_unit: measuredDbDelta?.worker_usage_unit ?? (dbDeltaRequired ? unavailableDbDelta?.worker_usage_unit : (runUsage.normalized ? "tokens" : null)),
    worker_total_tokens: measuredDbDelta?.worker_total_tokens ?? (dbDeltaRequired ? null : stdoutTotal),
    worker_usage_source_path: measuredDbDelta?.worker_usage_source_path ?? (dbDeltaRequired ? null : null),
    worker_usage_capture_method: measuredDbDelta?.worker_usage_capture_method ?? (dbDeltaRequired ? MODEL_USAGE_DB_DELTA_SOURCE_TYPE : (runUsage.normalized ? "zcode_cli_json_usage" : null)),
    quota_source: quota.source,
    quota_provider: quota.provider,
    quota_percent_direction: quota.quota_percent_direction,
    quota_percent_before: quota.quota_percent_before,
    quota_percent_after: quota.quota_percent_after,
    quota_percent_used: quota.quota_percent_used,
    quota_percent_status: quota.quota_percent_status,
    quota_percent_unavailable_reason: quota.quota_percent_unavailable_reason,
    quota_windows: quota.windows,
  };
}

function mapMode(mode) {
  const mapping = {
    "Plan": "plan",
    "Auto Edit": "edit",
    "Full Access": "yolo",
    "Confirm Before Changes": "build",
  };
  return mapping[mode] ?? mode ?? "plan";
}

function positiveIntOrDefault(value, fallback) {
  const parsed = Math.trunc(Number(value));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeIntOrDefault(value, fallback) {
  const parsed = Math.trunc(Number(value));
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function resolveWorkspacePath(workspace, relativePath) {
  const root = resolve(workspace);
  const absolute = resolve(root, relativePath);
  if (!pathIsInside(absolute, root)) {
    throw new Error(`packet vision image escapes workspace: ${relativePath}`);
  }
  return absolute;
}

function isSecretLikePath(path) {
  const lowered = String(path).toLowerCase();
  return SECRET_PATH_NEEDLES.some((needle) => lowered.includes(needle));
}

function resolveVisionAttachment(workspace, relativePath) {
  if (!relativePath || typeof relativePath !== "string") {
    throw new Error("packet vision image path must be a non-empty string");
  }
  if (isSecretLikePath(relativePath)) {
    throw new Error(`secret-like vision attachment is not allowed: ${relativePath}`);
  }
  const absolute = resolveWorkspacePath(workspace, relativePath);
  if (isSecretLikePath(absolute)) {
    throw new Error(`secret-like vision attachment is not allowed: ${relativePath}`);
  }
  if (!IMAGE_EXTENSIONS.has(extname(absolute).toLowerCase())) {
    throw new Error(`packet vision attachment must be an image file: ${relativePath}`);
  }
  return absolute;
}

function packetVision(packet, workspace) {
  const rawVision = isRecord(packet.vision) ? packet.vision : {};
  const imageFiles = Array.isArray(rawVision.image_files)
    ? rawVision.image_files.filter((item) => typeof item === "string")
    : [];
  const service = typeof rawVision.service === "string" && rawVision.service.trim()
    ? rawVision.service.trim()
    : DEFAULT_VISION_SERVICE;
  return {
    required: Boolean(rawVision.required),
    service,
    image_files: imageFiles,
    attached_files: imageFiles.map((item) => resolveVisionAttachment(workspace, item)),
    model_limit: rawVision.model_limit ?? null,
  };
}

async function missingVisionAttachment(vision) {
  for (const path of vision.attached_files) {
    if (!(await pathExists(path))) return path;
  }
  return null;
}

async function normalizeVisionAttachmentTargets(vision, workspace) {
  const realWorkspace = await realpath(workspace);
  const attachedFiles = [];
  for (let index = 0; index < vision.attached_files.length; index += 1) {
    const path = vision.attached_files[index];
    const rel = vision.image_files[index] ?? path;
    const target = await realpath(path);
    if (!pathIsInside(target, realWorkspace)) {
      throw new Error(`packet vision attachment target escapes workspace: ${rel}`);
    }
    if (isSecretLikePath(target)) {
      throw new Error(`secret-like vision attachment target is not allowed: ${rel}`);
    }
    if (!IMAGE_EXTENSIONS.has(extname(target).toLowerCase())) {
      throw new Error(`packet vision attachment target must be an image file: ${rel}`);
    }
    attachedFiles.push(target);
  }
  return {
    ...vision,
    attached_files: attachedFiles,
  };
}

async function readFileHeader(path, byteCount = 16) {
  const handle = await open(path, "r");
  try {
    const buffer = Buffer.alloc(byteCount);
    const { bytesRead } = await handle.read(buffer, 0, byteCount, 0);
    return buffer.subarray(0, bytesRead);
  } finally {
    await handle.close();
  }
}

function hasImageSignature(path, header) {
  const extension = extname(path).toLowerCase();
  if (extension === ".png") {
    return header.length >= 8 && header.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  }
  if (extension === ".jpg" || extension === ".jpeg") {
    return header.length >= 3 && header[0] === 0xFF && header[1] === 0xD8 && header[2] === 0xFF;
  }
  if (extension === ".gif") {
    const marker = header.subarray(0, 6).toString("ascii");
    return marker === "GIF87a" || marker === "GIF89a";
  }
  if (extension === ".webp") {
    return header.length >= 12
      && header.subarray(0, 4).toString("ascii") === "RIFF"
      && header.subarray(8, 12).toString("ascii") === "WEBP";
  }
  if (extension === ".bmp") {
    return header.length >= 2 && header[0] === 0x42 && header[1] === 0x4D;
  }
  return false;
}

async function invalidVisionAttachmentContent(vision) {
  for (let index = 0; index < vision.attached_files.length; index += 1) {
    const path = vision.attached_files[index];
    const header = await readFileHeader(path);
    if (!hasImageSignature(path, header)) {
      return `packet vision attachment is not a valid image file: ${vision.image_files[index] ?? path}`;
    }
  }
  return null;
}

function shouldRunVisionPreflight(mode, vision) {
  if (mode === "off") return false;
  if (mode === "required") return true;
  return vision.required;
}

async function buildVisionRunEnv(args, vision, visionPreflight) {
  if (!vision.required || !visionPreflight?.ok) return { env: {}, credential_source: null };
  if (process.env.Z_AI_API_KEY || process.env.ZAI_API_KEY) {
    const legacyKey = process.env.Z_AI_API_KEY ? null : process.env.ZAI_API_KEY;
    return {
      env: {
        Z_AI_MODE: process.env.Z_AI_MODE ?? "ZAI",
        ...(legacyKey ? { Z_AI_API_KEY: legacyKey } : {}),
      },
      credential_source: process.env.Z_AI_API_KEY ? "env:Z_AI_API_KEY" : "env:ZAI_API_KEY",
    };
  }
  const credential = await resolveZaiQuotaApiKey(args);
  if (!credential.ok || !credential.api_key) {
    return { env: {}, credential_source: null, error: credential.error ?? "Z_AI_API_KEY not found" };
  }
  return {
    env: {
      Z_AI_MODE: process.env.Z_AI_MODE ?? "ZAI",
      Z_AI_API_KEY: credential.api_key,
    },
    credential_source: credential.source,
  };
}

async function printJsonPayload(payload, out) {
  const textPayload = JSON.stringify(payload, null, 2);
  if (out) {
    const outputPath = resolve(out);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, `${textPayload}\n`);
  }
  console.log(textPayload);
  if (!payload.ok) process.exitCode = payload.exit_code ?? 1;
}

function buildPromptArgs(args, promptText, workspace) {
  const cliArgs = ["--cwd", workspace, "--prompt", promptText, "--mode", mapMode(args.mode), "--no-color"];
  if (args.json) cliArgs.push("--json");
  if (args.continue) cliArgs.push("--continue");
  if (args.resume) cliArgs.push("--resume", args.resume);
  if (args.target) cliArgs.push("--target", args.target);
  if (args.targetReplace) cliArgs.push("--target-replace");
  for (const item of args.attach ?? []) cliArgs.push("--attach", resolve(item));
  return cliArgs;
}

async function runSupervisorJson(supervisorArgs, options = {}) {
  try {
    const { stdout, stderr } = await execFileAsync(pythonCommand(), [SUPERVISOR_SCRIPT, ...supervisorArgs], {
      cwd: options.cwd ?? process.cwd(),
      maxBuffer: 25 * 1024 * 1024,
      timeout: options.timeoutMs ?? undefined,
    });
    const payload = parseJsonOrText(stdout);
    return isRecord(payload) ? { ...payload, command_exit_code: 0, command_stderr: stderr } : payload;
  } catch (error) {
    const payload = parseJsonOrText(error.stdout ?? "");
    if (isRecord(payload)) {
      return {
        ...payload,
        command_exit_code: typeof error.code === "number" ? error.code : 1,
        command_stderr: error.stderr ?? "",
      };
    }
    throw error;
  }
}

async function createRunPacketSnapshot(workspace) {
  const snapshotPath = join(tmpdir(), `zcode-run-packet-${process.pid}-${randomUUID()}.json`);
  await runSupervisorJson(["snapshot", "--workspace", workspace, "--out", snapshotPath], { cwd: workspace });
  return snapshotPath;
}

async function snapshotSecretFiles(snapshotPath) {
  const snapshot = await readJsonFile(snapshotPath);
  return Array.isArray(snapshot.secret_files)
    ? snapshot.secret_files.filter((item) => typeof item === "string" && item.trim())
    : [];
}

async function auditRunPacketAttempt({ workspace, packetPath, snapshotPath, validationTimeout }) {
  return runSupervisorJson(
    [
      "audit",
      "--workspace",
      workspace,
      "--snapshot",
      snapshotPath,
      "--packet",
      packetPath,
      "--validation-timeout",
      String(validationTimeout ?? 60),
    ],
    { cwd: workspace },
  );
}

function compactAttemptRecord(attempt, index) {
  const runUsage = normalizedZcodeUsageFromStdout(attempt.stdout ?? "");
  const usageAvailable = Boolean(runUsage.normalized);
  return {
    attempt: index + 1,
    cli_ok: attempt.cli_ok,
    exit_code: attempt.exit_code,
    provider_error: attempt.provider_error,
    provider_error_kind: attempt.provider_error_kind,
    provider_code: attempt.provider_code,
    provider_message: attempt.provider_message,
    provider_id: attempt.provider_id,
    provider_kind: attempt.provider_kind,
    provider_rate_limit_1302: attempt.provider_rate_limit_1302,
    provider_rate_limit_1302_count: attempt.provider_rate_limit_1302_count,
    provider_fail_fast: attempt.provider_fail_fast,
    provider_fail_fast_reason: attempt.provider_fail_fast_reason,
    retryable_provider_error: attempt.retryable_provider_error,
    usage_available: usageAvailable,
    no_usage_reason: usageAvailable ? null : missingUsageReason(attempt),
    tokens_total: runUsage.normalized?.total_tokens ?? null,
    provider_usage_ledger: attempt.provider_usage_ledger ?? null,
    supervisor_state: attempt.supervisor_state,
    changed_count: attempt.audit?.changed_count ?? null,
    validation_ok: attempt.audit?.validation?.ok ?? null,
    audit_ok: attempt.audit?.ok ?? null,
  };
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function isValidationFailure(audit) {
  if (!audit || audit.validation?.ok !== false) return false;
  if (!Array.isArray(audit.violations)) return true;
  return audit.violations.some((violation) => violation?.type === "validation_failed");
}

function isRepairableValidationFailure(audit) {
  if (!isValidationFailure(audit)) return false;
  if (!Array.isArray(audit.violations) || audit.violations.length === 0) return true;
  return audit.violations.every((violation) => violation?.type === "validation_failed");
}

function validationRepairPrompt(packet, audit, attemptNumber) {
  const validation = audit.validation ?? {};
  const stderrTail = validation.stderr_tail ?? "";
  const stdoutTail = validation.stdout_tail ?? "";
  const allowed = Array.isArray(packet.allowed_files) ? packet.allowed_files.join(", ") : "packet allowed files only";
  return [
    "You are a ZCode worker under Codex audit.",
    `This is repair attempt ${attemptNumber} for the same bounded implementation packet.`,
    "",
    `Original objective: ${packet.objective ?? "unspecified"}`,
    `Allowed files: ${allowed}`,
    `Validation command (you may run this exact command as a black-box check; do not inspect validator source): ${packet.validation ?? "unspecified"}`,
    "",
    "The previous attempt edited files but failed supervisor validation.",
    "Use the black-box validation output below, the project files, and the original objective to make the smallest correction.",
    "If validation output shows expected vs actual values, treat the expected value as the acceptance contract and update implementation behavior to match it exactly.",
    "Prefer the validator evidence over speculative reasoning about edge cases, rounding, formatting, ordering, or boundary behavior.",
    "After editing, run the exact validation command if the environment permits it, then keep fixing until it passes or you clearly report the blocker.",
    "Do not broaden scope. Do not edit tests unless they are explicitly allowed. Do not read secrets or files outside the workspace.",
    "",
    "Validation stdout tail:",
    stdoutTail || "(empty)",
    "",
    "Validation stderr tail:",
    stderrTail || "(empty)",
    "",
    "Final report: changed files, validation expectation addressed, remaining risks, accept/inspect/reject recommendation.",
  ].join("\n");
}

async function writeRunPacketProgress(args, payload) {
  if (!args.out) return;
  const outputPath = resolve(args.out);
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`);
}

function signalExitCode(signalName) {
  if (signalName === "SIGINT") return 130;
  if (signalName === "SIGTERM") return 143;
  return 1;
}

function runPacketTerminalPayload({
  status,
  packetPath,
  workspace,
  attempts,
  maxAttempts,
  currentAttempt,
  signalName = null,
  timedOut = false,
  error = null,
}) {
  const lastAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;
  return {
    ok: false,
    cli_ok: false,
    exit_code: signalName ? signalExitCode(signalName) : 1,
    status,
    supervisor_state: status,
    packet: packetPath,
    workspace,
    attempts: attempts.length,
    attempt_count: attempts.length,
    current_attempt: currentAttempt,
    max_attempts: maxAttempts,
    signal: signalName,
    timed_out: timedOut,
    validation_ok: lastAttempt?.audit?.validation?.ok ?? null,
    audit_ok: lastAttempt?.audit?.ok ?? null,
    changed_count: lastAttempt?.audit?.changed_count ?? null,
    attempt_results: attempts.map(compactAttemptRecord),
    error: error?.message ?? null,
  };
}

function installRunPacketSignalHandlers(writeTerminal) {
  const onSigterm = () => {
    writeTerminal("aborted", { signalName: "SIGTERM" })
      .catch((error) => console.error(error.message))
      .finally(() => process.exit(signalExitCode("SIGTERM")));
  };
  const onSigint = () => {
    writeTerminal("aborted", { signalName: "SIGINT" })
      .catch((error) => console.error(error.message))
      .finally(() => process.exit(signalExitCode("SIGINT")));
  };
  process.once("SIGTERM", onSigterm);
  process.once("SIGINT", onSigint);
  return () => {
    process.off("SIGTERM", onSigterm);
    process.off("SIGINT", onSigint);
  };
}

async function cliPrompt(args) {
  await ensureCliPromptReady(args);
  const promptText = await readPrompt(args);
  const workspace = resolve(args.workspace ?? process.cwd());
  const cliArgs = buildPromptArgs(args, promptText, workspace);
  const providerRateLimitFailFastCount = args.providerRateLimitFailFast === false
    ? 0
    : positiveIntOrDefault(args.providerRateLimitFailFastCount, DEFAULT_PROVIDER_RATE_LIMIT_FAIL_FAST_COUNT);
  const result = await runZcodeCli(cliArgs, {
    cwd: workspace,
    timeoutMs: args.timeoutMs ?? PROMPT_TIMEOUT_MS,
    providerRateLimitFailFastCount,
  });
  await printCliResult(result, args.out);
}

async function visionPreflightCommand(args) {
  const workspace = resolve(args.workspace ?? process.cwd());
  const serviceName = args.visionService ?? DEFAULT_VISION_SERVICE;
  const payload = await inspectVisionServices(args, workspace, serviceName);
  await printJsonPayload(payload, args.out);
}

function outputPathForAppRunPacket(args, packetPath) {
  return args.out ? resolve(args.out) : join(dirname(resolve(packetPath)), "zcode-run.json");
}

function relativeWorkerUsagePath(path, outputPath) {
  if (!path) return null;
  const relativePath = relative(dirname(outputPath), path);
  if (!relativePath || relativePath.startsWith("..") || isAbsolute(relativePath)) return String(path);
  return relativePath;
}

function appRunPacketDbDeltaPaths(args, packetPath) {
  const outputPath = outputPathForAppRunPacket(args, packetPath);
  const rowDir = dirname(outputPath);
  return {
    outputPath,
    rowDir,
    beforePath: join(rowDir, MODEL_USAGE_DB_DELTA_BEFORE_NAME),
    afterPath: join(rowDir, MODEL_USAGE_DB_DELTA_AFTER_NAME),
    ledgerPath: join(rowDir, WORKER_USAGE_LEDGER_NAME),
  };
}

function appRunPacketDbArg(args) {
  return args.modelUsageDb ? ["--db", resolve(args.modelUsageDb)] : [];
}

async function runModelUsageDbDelta(commandArgs, outPath) {
  try {
    await execFileAsync(pythonCommand(), [MODEL_USAGE_DB_DELTA_SCRIPT, ...commandArgs], {
      env: sanitizeChildEnv(),
      maxBuffer: 5 * 1024 * 1024,
      timeout: 30_000,
    });
    return await readJsonFile(outPath);
  } catch (error) {
    return {
      source_type: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
      unit: "tokens",
      status: "unavailable",
      usage: {},
      no_usage_reason: "zcode_model_usage_db_delta_command_failed",
      error: error.message,
    };
  }
}

async function captureAppRunPacketDbDeltaBefore(args, packetPath) {
  const paths = appRunPacketDbDeltaPaths(args, packetPath);
  await mkdir(paths.rowDir, { recursive: true });
  const before = await runModelUsageDbDelta(
    ["before", ...appRunPacketDbArg(args), "--out", paths.beforePath],
    paths.beforePath,
  );
  return { paths, before };
}

async function captureAppRunPacketDbDeltaAfter(args, packetPath) {
  const paths = appRunPacketDbDeltaPaths(args, packetPath);
  const after = await runModelUsageDbDelta(
    [
      "after",
      ...appRunPacketDbArg(args),
      "--before",
      paths.beforePath,
      "--ledger",
      paths.ledgerPath,
      "--row-dir",
      paths.rowDir,
      "--out",
      paths.afterPath,
    ],
    paths.afterPath,
  );
  return { paths, after };
}

function dbDeltaRowIds(delta) {
  return Array.isArray(delta?.row_ids)
    ? delta.row_ids.map((value) => tokenInteger(value)).filter((value) => value !== null)
    : null;
}

function dbDeltaRows(delta) {
  return Array.isArray(delta?.rows) ? delta.rows : null;
}

function measuredDbDeltaUsage(delta, outputPath) {
  const total = tokenInteger(delta?.total_tokens ?? delta?.usage?.total_tokens, { positive: true });
  if (delta?.status !== "measured" || total === null) return null;
  const usage = isRecord(delta.usage) ? delta.usage : {};
  return {
    usage_available: true,
    no_usage_reason: null,
    tokens_source: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    tokens_used: total,
    tokens_total: total,
    input_tokens: tokenInteger(usage.input_tokens),
    output_tokens: tokenInteger(usage.output_tokens),
    reasoning_tokens: tokenInteger(usage.reasoning_tokens),
    cache_read_tokens: tokenInteger(usage.cache_read_tokens),
    cache_write_tokens: tokenInteger(usage.cache_write_tokens),
    source_type: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    worker_usage_status: "measured",
    worker_usage_unit: "tokens",
    worker_total_tokens: total,
    worker_usage_source_path: relativeWorkerUsagePath(join(dirname(outputPath), WORKER_USAGE_LEDGER_NAME), outputPath),
    worker_usage_capture_method: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    worker_usage_ledger_status: "appended",
    worker_usage_isolation_required: true,
    measured_app_backed_tokens: true,
    before_max_rowid: tokenInteger(delta.before_max_rowid),
    after_max_rowid: tokenInteger(delta.after_max_rowid),
    row_ids: dbDeltaRowIds(delta),
    row_count: tokenInteger(delta.row_count),
    rows: dbDeltaRows(delta),
    total_tokens: total,
    usage,
    db_delta_status: "measured",
  };
}

function unavailableDbDeltaUsage({ before = null, after = null, outputPath }) {
  const source = after ?? before ?? {};
  return {
    usage_available: false,
    no_usage_reason: source.no_usage_reason ?? "app_cdp_db_delta_unavailable",
    tokens_source: null,
    tokens_used: null,
    tokens_total: null,
    input_tokens: null,
    output_tokens: null,
    reasoning_tokens: null,
    cache_read_tokens: null,
    cache_write_tokens: null,
    source_type: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    worker_usage_status: "unavailable",
    worker_usage_unit: "unknown",
    worker_total_tokens: null,
    worker_usage_source_path: null,
    worker_usage_capture_method: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    worker_usage_ledger_status: source.status ?? "unavailable",
    worker_usage_isolation_required: true,
    measured_app_backed_tokens: false,
    before_max_rowid: tokenInteger(source.before_max_rowid ?? before?.before_max_rowid),
    after_max_rowid: tokenInteger(source.after_max_rowid),
    row_ids: dbDeltaRowIds(source),
    row_count: tokenInteger(source.row_count),
    rows: dbDeltaRows(source),
    total_tokens: null,
    usage: null,
    db_delta_status: source.status ?? "unavailable",
    db_delta_output_path: after ? relativeWorkerUsagePath(join(dirname(outputPath), MODEL_USAGE_DB_DELTA_AFTER_NAME), outputPath) : null,
  };
}

function appRunPacketUsageAccounting({ packetPath, args, before = null, after = null } = {}) {
  const outputPath = outputPathForAppRunPacket(args ?? {}, packetPath);
  const measured = measuredDbDeltaUsage(after, outputPath);
  const dbDelta = measured ?? unavailableDbDeltaUsage({ before, after, outputPath });
  return {
    ...dbDelta,
    quota_source: null,
    quota_provider: null,
    quota_percent_direction: null,
    quota_percent_before: null,
    quota_percent_after: null,
    quota_percent_used: null,
    quota_percent_status: "not_measured",
    quota_percent_unavailable_reason: "app_cdp_scaffold_does_not_call_provider",
    quota_windows: {},
  };
}

function appRunPacketWorkerUsageFields(usageAccounting) {
  return {
    source_type: MODEL_USAGE_DB_DELTA_SOURCE_TYPE,
    worker_usage_status: usageAccounting.worker_usage_status,
    worker_usage_unit: usageAccounting.worker_usage_unit,
    worker_total_tokens: usageAccounting.worker_total_tokens,
    worker_usage_source_path: usageAccounting.worker_usage_source_path,
    worker_usage_capture_method: usageAccounting.worker_usage_capture_method,
  };
}

function appRunPacketExpectedWorkspace(args, packet) {
  return resolve(args.expectedWorkspace ?? packet.workspace);
}

function workspaceBindingExpression(expectedWorkspace) {
  return `(() => {
    // workspace binding preflight
    const expected = ${JSON.stringify(expectedWorkspace)};
    const pathLike = (value) => typeof value === 'string' && (
      value.startsWith('/') ||
      /^[A-Za-z]:[\\\\/]/.test(value) ||
      value.includes('/workspaces/') ||
      value.includes('\\\\workspaces\\\\')
    );
    const clean = (value) => typeof value === 'string' ? value.trim() : null;
    const direct = window.__ZCODE_APP_WORKSPACE_BINDING__;
    if (direct && typeof direct === 'object') {
      return { ...direct, expected_workspace: expected, source: direct.source || 'window.__ZCODE_APP_WORKSPACE_BINDING__' };
    }
    const candidates = [];
    const add = (source, value, confidence = 'weak') => {
      const text = clean(value);
      if (!text || !pathLike(text)) return;
      candidates.push({ source, value: text, confidence });
    };
    for (const [name, value] of [
      ['window.__ZCODE_ACTIVE_WORKSPACE__', window.__ZCODE_ACTIVE_WORKSPACE__],
      ['window.__ZCODE_CURRENT_WORKSPACE__', window.__ZCODE_CURRENT_WORKSPACE__],
      ['window.__ZCODE_WORKSPACE__', window.__ZCODE_WORKSPACE__],
      ['window.zcode.workspace', window.zcode?.workspace],
      ['window.zcode.currentWorkspace', window.zcode?.currentWorkspace],
      ['window.zcode.workspacePath', window.zcode?.workspacePath],
      ['window.zcode.cwd', window.zcode?.cwd],
    ]) {
      add(name, value, 'strong');
    }
    for (const selector of [
      '[data-active="true"][data-workspace-path]',
      '[aria-current="page"][data-workspace-path]',
      '[data-current-workspace]',
      '[data-active-workspace]',
    ]) {
      for (const node of Array.from(document.querySelectorAll(selector))) {
        add(selector, node.getAttribute('data-workspace-path') || node.getAttribute('data-current-workspace') || node.getAttribute('data-active-workspace'), 'strong');
      }
    }
    for (const store of [window.localStorage, window.sessionStorage]) {
      if (!store) continue;
      for (let index = 0; index < store.length; index += 1) {
        const key = store.key(index) || '';
        if (!/(active|current|workspace|project|cwd|root)/i.test(key)) continue;
        const raw = store.getItem(key);
        add(\`\${store === window.localStorage ? 'localStorage' : 'sessionStorage'}:\${key}\`, raw, /active|current|cwd|root/i.test(key) ? 'strong' : 'weak');
        try {
          const parsed = JSON.parse(raw);
          const stack = [parsed];
          while (stack.length) {
            const item = stack.pop();
            if (typeof item === 'string') add(\`\${store === window.localStorage ? 'localStorage' : 'sessionStorage'}:\${key}\`, item, 'strong');
            else if (item && typeof item === 'object') {
              for (const [childKey, childValue] of Object.entries(item)) {
                if (/(active|current|workspace|path|cwd|root)/i.test(childKey)) stack.push(childValue);
              }
            }
          }
        } catch {}
      }
    }
    const strong = candidates.filter((candidate) => candidate.confidence === 'strong');
    const exact = strong.find((candidate) => candidate.value === expected);
    if (exact) {
      return {
        ok: true,
        bound: true,
        status: 'app_cdp_workspace_bound',
        expected_workspace: expected,
        detected_workspace: exact.value,
        evidence_source: exact.source,
        candidates: strong.slice(0, 5),
      };
    }
    if (strong.length) {
      return {
        ok: false,
        bound: false,
        status: 'app_cdp_workspace_mismatch',
        expected_workspace: expected,
        detected_workspace: strong[0].value,
        evidence_source: strong[0].source,
        candidates: strong.slice(0, 5),
      };
    }
    return {
      ok: false,
      bound: null,
      status: 'app_cdp_workspace_unknown',
      expected_workspace: expected,
      detected_workspace: null,
      evidence_source: null,
      candidates: candidates.slice(0, 5),
    };
  })()`;
}

function normalizeWorkspaceEvidencePath(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  if (isAbsolute(trimmed) || /^[A-Za-z]:[\\/]/.test(trimmed)) return resolve(trimmed);
  return trimmed;
}

function normalizeAppWorkspaceBinding(raw, expectedWorkspace) {
  const expected = resolve(expectedWorkspace);
  const detected = normalizeWorkspaceEvidencePath(
    raw?.detected_workspace ?? raw?.detectedWorkspace ?? raw?.workspace ?? raw?.cwd ?? raw?.path,
  );
  const rawStatus = typeof raw?.status === "string" ? raw.status : null;
  const detectedMatches = detected !== null && resolve(detected) === expected;
  let status;
  if (detectedMatches && raw?.bound !== false) {
    status = "app_cdp_workspace_bound";
  } else if (rawStatus === "app_cdp_workspace_mismatch") {
    status = "app_cdp_workspace_mismatch";
  } else if (rawStatus === "app_cdp_workspace_not_bound" || rawStatus === "not_bound" || raw?.bound === false) {
    status = "app_cdp_workspace_not_bound";
  } else if (detected !== null) {
    status = "app_cdp_workspace_mismatch";
  } else {
    status = "app_cdp_workspace_unknown";
  }
  return {
    ok: status === "app_cdp_workspace_bound",
    status,
    expected_workspace: expected,
    detected_workspace: detected,
    raw_status: rawStatus,
    reason: raw?.reason ?? null,
    evidence_source: raw?.evidence_source ?? raw?.source ?? null,
    candidates: Array.isArray(raw?.candidates) ? raw.candidates.slice(0, 5) : [],
  };
}

async function inspectAppWorkspaceBinding(port, expectedWorkspace) {
  try {
    const result = await runtimeEvaluate(port, workspaceBindingExpression(expectedWorkspace));
    return normalizeAppWorkspaceBinding(result.value ?? {}, expectedWorkspace);
  } catch (error) {
    return {
      ok: false,
      status: "app_cdp_workspace_unknown",
      expected_workspace: resolve(expectedWorkspace),
      detected_workspace: null,
      raw_status: null,
      reason: error.message,
      evidence_source: "cdp_runtime_evaluate",
      candidates: [],
    };
  }
}

function emptyChangedFiles(changedFiles) {
  if (!isRecord(changedFiles)) return true;
  return Object.values(changedFiles).every((value) => !Array.isArray(value) || value.length === 0);
}

function appRunPacketNoWorkspaceChangeWithNoDbRows(basePayload, audit) {
  const changedCount = tokenInteger(audit?.changed_count);
  const noWorkspaceChange = changedCount === 0 || (
    changedCount === null && emptyChangedFiles(audit?.changed_files)
  );
  const rowIds = Array.isArray(basePayload.row_ids) ? basePayload.row_ids : [];
  const noDbRows = rowIds.length === 0
    && basePayload.usage_accounting?.no_usage_reason === "zcode_model_usage_no_new_rows";
  return Boolean(noWorkspaceChange && noDbRows);
}

function appRunPacketWorkspaceBlockedPayload(basePayload, workspaceBinding) {
  return {
    ...basePayload,
    status: workspaceBinding.status,
    supervisor_state: workspaceBinding.status,
    exit_code: 1,
    expected_workspace: workspaceBinding.expected_workspace,
    detected_workspace: workspaceBinding.detected_workspace,
    workspace_binding_status: workspaceBinding.status,
    workspace_binding_ok: false,
    app_cdp: {
      ...basePayload.app_cdp,
      submit_allowed: true,
      submit_blocked_reason: workspaceBinding.status,
      workspace_binding_required: true,
      expected_workspace: workspaceBinding.expected_workspace,
      detected_workspace: workspaceBinding.detected_workspace,
      workspace_binding_status: workspaceBinding.status,
      workspace_binding: workspaceBinding,
    },
  };
}

function appRunPacketWorkspacePreflightPayload(basePayload, workspaceBinding) {
  return {
    ...basePayload,
    ok: workspaceBinding.ok,
    exit_code: workspaceBinding.ok ? 0 : 1,
    status: workspaceBinding.status,
    supervisor_state: workspaceBinding.status,
    expected_workspace: workspaceBinding.expected_workspace,
    detected_workspace: workspaceBinding.detected_workspace,
    workspace_binding_status: workspaceBinding.status,
    workspace_binding_ok: workspaceBinding.ok,
    app_cdp: {
      ...basePayload.app_cdp,
      submit_allowed: false,
      submit_blocked_reason: "preflight_only",
      preflight_only: true,
      workspace_binding_required: true,
      expected_workspace: workspaceBinding.expected_workspace,
      detected_workspace: workspaceBinding.detected_workspace,
      workspace_binding_status: workspaceBinding.status,
      workspace_binding: workspaceBinding,
    },
  };
}

function appRunPacketBasePayload({
  packetPath,
  packet,
  workspace,
  expectedWorkspace,
  workspaceBinding = null,
  args,
  status,
  before = null,
  after = null,
}) {
  const usageAccounting = appRunPacketUsageAccounting({ packetPath, args, before, after });
  const expected = expectedWorkspace ?? workspace;
  const detected = workspaceBinding?.detected_workspace ?? null;
  return {
    ok: false,
    cli_ok: false,
    exit_code: 1,
    status,
    supervisor_state: status,
    packet: packetPath,
    workspace,
    expected_workspace: expected,
    detected_workspace: detected,
    workspace_binding_status: workspaceBinding?.status ?? null,
    workspace_binding_ok: workspaceBinding?.ok ?? null,
    mode: mapMode(args.mode ?? packet.mode),
    worker_execution_backend: "zcode_app_cdp",
    worker_finalization: packet.worker_finalization ?? "zcode_owned",
    prompt_chars: packet.prompt?.length ?? 0,
    attempts: 1,
    attempt_count: 1,
    current_attempt: 1,
    max_attempts: 1,
    timed_out: false,
    timeout_ms: args.timeoutMs ?? 300_000,
    validation: null,
    validation_ok: null,
    validation_rc: null,
    audit: null,
    audit_ok: null,
    changed_count: null,
    changed_files: null,
    route_rc: null,
    acceptance_rc: null,
    final_validation_rc: null,
    strict_accepted: null,
    provider_status: "not_invoked_by_app_runner_scaffold",
    provider_error: false,
    provider_error_kind: null,
    provider_code: null,
    provider_message: null,
    ...appRunPacketWorkerUsageFields(usageAccounting),
    before_max_rowid: usageAccounting.before_max_rowid,
    after_max_rowid: usageAccounting.after_max_rowid,
    row_ids: usageAccounting.row_ids,
    row_count: usageAccounting.row_count,
    rows: usageAccounting.rows,
    total_tokens: usageAccounting.total_tokens,
    usage_available: usageAccounting.usage_available,
    no_usage_reason: usageAccounting.no_usage_reason,
    usage: usageAccounting.usage,
    usage_normalized: usageAccounting.usage,
    usage_source_path: usageAccounting.worker_usage_source_path,
    usage_snapshots: {
      before,
      after,
    },
    usage_accounting: usageAccounting,
    safe_to_retry_later: false,
    app_cdp: {
      port: args.port,
      submit_allowed: false,
      submit_guard: "requires --allow-submit and ZCODE_APP_CDP_ALLOW_SUBMIT=1",
      workspace_binding_required: true,
      expected_workspace: expected,
      detected_workspace: detected,
      workspace_binding_status: workspaceBinding?.status ?? null,
      workspace_binding: workspaceBinding,
      commands: ["set-composer", "click Send", "wait-idle"],
    },
  };
}

function appRunPacketPayloadFromAudit({ basePayload, audit, waitResult, submitResult, sendResult }) {
  const validation = audit?.validation ?? null;
  const timedOut = waitResult?.reason === "timeout";
  const waitingApproval = waitResult?.reason === "awaiting_approval";
  const validationOk = validation?.ok ?? null;
  const validationRc = validation?.returncode ?? null;
  const auditOk = audit?.ok ?? null;
  const usageMeasured = basePayload.worker_usage_status === "measured"
    && basePayload.worker_usage_unit === "tokens"
    && tokenInteger(basePayload.worker_total_tokens, { positive: true }) !== null;
  const ok = Boolean(waitResult?.ok && auditOk && validationOk && usageMeasured);
  const workspaceBindingFailure = !ok && appRunPacketNoWorkspaceChangeWithNoDbRows(basePayload, audit);
  const strictAccepted = audit?.strict_contract?.accepted ?? (workspaceBindingFailure ? false : null);
  const status = ok
    ? "success"
    : timedOut
      ? "app_cdp_timeout"
      : waitingApproval
        ? "app_cdp_awaiting_approval"
        : workspaceBindingFailure
          ? "app_cdp_workspace_not_bound"
          : auditOk && validationOk && !usageMeasured
            ? "app_cdp_usage_unavailable"
            : "app_cdp_audit_failed";
  return {
    ...basePayload,
    ok,
    cli_ok: false,
    exit_code: ok ? 0 : 1,
    status,
    supervisor_state: status,
    timed_out: timedOut,
    validation,
    validation_ok: validationOk,
    validation_rc: validationRc,
    audit,
    audit_ok: auditOk,
    changed_count: audit?.changed_count ?? null,
    changed_files: audit?.changed_files ?? null,
    route_rc: null,
    acceptance_rc: null,
    final_validation_rc: validationRc,
    strict_accepted: strictAccepted,
    app_cdp: {
      ...basePayload.app_cdp,
      submit_allowed: true,
      set_composer_ok: submitResult?.value?.ok ?? null,
      send_ok: sendResult?.value?.ok ?? null,
      wait_idle_ok: waitResult?.ok ?? null,
      wait_idle_reason: waitResult?.reason ?? null,
      wait_idle_summary: waitResult?.summary ?? null,
      usage_required_for_success: true,
      workspace_binding_failure_inferred: workspaceBindingFailure,
    },
  };
}

async function appRunPacket(args) {
  if (!args.packet) throw new Error("--packet is required");
  const packetPath = resolve(args.packet);
  const packet = JSON.parse(await readFile(packetPath, "utf8"));
  if (!packet.prompt) throw new Error(`packet is missing prompt: ${packetPath}`);
  const workspace = resolve(packet.workspace);
  const expectedWorkspace = appRunPacketExpectedWorkspace(args, packet);
  let basePayload = appRunPacketBasePayload({
    packetPath,
    packet,
    workspace,
    expectedWorkspace,
    args,
    status: "app_cdp_submit_not_allowed",
  });
  const submitAllowed = args.allowSubmit === true && process.env.ZCODE_APP_CDP_ALLOW_SUBMIT === "1";
  let workspaceBinding = null;
  if (args.requireWorkspaceBound || submitAllowed) {
    workspaceBinding = await inspectAppWorkspaceBinding(args.port, expectedWorkspace);
    basePayload = appRunPacketBasePayload({
      packetPath,
      packet,
      workspace,
      expectedWorkspace,
      workspaceBinding,
      args,
      status: "app_cdp_submit_not_allowed",
    });
    if (!submitAllowed && args.requireWorkspaceBound) {
      await printJsonPayload(appRunPacketWorkspacePreflightPayload(basePayload, workspaceBinding), args.out);
      return;
    }
    if (!workspaceBinding.ok) {
      await printJsonPayload(appRunPacketWorkspaceBlockedPayload(basePayload, workspaceBinding), args.out);
      return;
    }
  }
  if (!submitAllowed) {
    await printJsonPayload(basePayload, args.out);
    return;
  }

  const dbDeltaBefore = await captureAppRunPacketDbDeltaBefore(args, packetPath);
  basePayload = appRunPacketBasePayload({
    packetPath,
    packet,
    workspace,
    expectedWorkspace,
    workspaceBinding,
    args,
    status: "app_cdp_submit_not_allowed",
    before: dbDeltaBefore.before,
  });
  if (dbDeltaBefore.before.status !== "available") {
    await printJsonPayload(
      {
        ...basePayload,
        status: "app_cdp_usage_unavailable",
        supervisor_state: "app_cdp_usage_unavailable",
        exit_code: 1,
        app_cdp: {
          ...basePayload.app_cdp,
          submit_allowed: true,
          submit_blocked_reason: "db_delta_before_unavailable",
        },
      },
      args.out,
    );
    return;
  }

  let snapshotPath = null;
  await writeRunPacketProgress(args, {
    ...basePayload,
    status: "app_cdp_running",
    supervisor_state: "app_cdp_running",
    app_cdp: { ...basePayload.app_cdp, submit_allowed: true },
  });
  try {
    snapshotPath = await createRunPacketSnapshot(workspace);
    const secretFiles = await snapshotSecretFiles(snapshotPath);
    if (secretFiles.length > 0) {
      throw new Error(
        `workspace contains secret-like paths; use a sanitized worktree before delegation: ${secretFiles.join(", ")}`,
      );
    }
    const submitResult = await setComposerValue(args.port, packet.prompt);
    if (!submitResult.value?.ok) {
      throw new Error(`set-composer failed: ${submitResult.value?.reason ?? "unknown"}`);
    }
    const sendResult = await clickMatchingTextValue(args.port, "Send", false);
    if (!sendResult.value?.ok) {
      throw new Error(`submit click failed: ${sendResult.value?.reason ?? "unknown"}`);
    }
    const waitResult = await waitIdleValue(args.port, args.timeoutMs ?? 300_000, args.intervalMs ?? 2_000);
    const audit = await auditRunPacketAttempt({
      workspace,
      packetPath,
      snapshotPath,
      validationTimeout: args.validationTimeout,
    });
    const dbDeltaAfter = await captureAppRunPacketDbDeltaAfter(args, packetPath);
    const finalBasePayload = appRunPacketBasePayload({
      packetPath,
      packet,
      workspace,
      expectedWorkspace,
      workspaceBinding,
      args,
      status: basePayload.status,
      before: dbDeltaBefore.before,
      after: dbDeltaAfter.after,
    });
    await printJsonPayload(
      appRunPacketPayloadFromAudit({ basePayload: finalBasePayload, audit, waitResult, submitResult, sendResult }),
      args.out,
    );
  } catch (error) {
    await printJsonPayload(
      {
        ...basePayload,
        status: "app_cdp_error",
        supervisor_state: "app_cdp_error",
        error: error.message,
        app_cdp: {
          ...basePayload.app_cdp,
          submit_allowed: true,
          error: error.message,
        },
      },
      args.out,
    );
  } finally {
    if (snapshotPath) await unlink(snapshotPath).catch(() => {});
  }
}

async function runPacket(args) {
  if (!args.packet) throw new Error("--packet is required");
  const packetPath = resolve(args.packet);
  const packet = JSON.parse(await readFile(packetPath, "utf8"));
  if (!packet.prompt) throw new Error(`packet is missing prompt: ${packetPath}`);
  const workspace = resolve(packet.workspace);
  let vision;
  try {
    vision = packetVision(packet, workspace);
  } catch (error) {
    await printJsonPayload(
      {
        ok: false,
        exit_code: 1,
        status: "vision_attachment_invalid",
        supervisor_state: "vision_attachment_invalid",
        packet: packetPath,
        workspace,
        error: error.message,
      },
      args.out,
    );
    return;
  }
  const missingAttachment = await missingVisionAttachment(vision);
  if (missingAttachment) {
    await printJsonPayload(
      {
        ok: false,
        exit_code: 1,
        status: "vision_attachment_missing",
        supervisor_state: "vision_attachment_missing",
        packet: packetPath,
        workspace,
        vision,
        error: `vision attachment does not exist: ${missingAttachment}`,
      },
      args.out,
    );
    return;
  }
  try {
    vision = await normalizeVisionAttachmentTargets(vision, workspace);
  } catch (error) {
    await printJsonPayload(
      {
        ok: false,
        exit_code: 1,
        status: "vision_attachment_invalid",
        supervisor_state: "vision_attachment_invalid",
        packet: packetPath,
        workspace,
        vision,
        error: error.message,
      },
      args.out,
    );
    return;
  }
  const invalidAttachment = await invalidVisionAttachmentContent(vision);
  if (invalidAttachment) {
    await printJsonPayload(
      {
        ok: false,
        exit_code: 1,
        status: "vision_attachment_invalid",
        supervisor_state: "vision_attachment_invalid",
        packet: packetPath,
        workspace,
        vision,
        error: invalidAttachment,
      },
      args.out,
    );
    return;
  }
  const visionPreflightMode = args.visionPreflight ?? "auto";
  if (!["auto", "required", "off"].includes(visionPreflightMode)) {
    throw new Error("--vision-preflight must be auto, required, or off");
  }
  const visionPreflight = shouldRunVisionPreflight(visionPreflightMode, vision)
    ? await inspectVisionServices(args, workspace, vision.service)
    : null;
  const visionPreflightRequired = vision.required || visionPreflightMode === "required";
  if (visionPreflightRequired && visionPreflightMode !== "off" && visionPreflight && !visionPreflight.ok) {
    await printJsonPayload(
      {
        ok: false,
        cli_ok: false,
        exit_code: 1,
        status: "vision_service_unavailable",
        supervisor_state: "vision_service_unavailable",
        packet: packetPath,
        workspace,
        vision,
        vision_preflight: visionPreflight,
        next_action: visionPreflight.next_action,
      },
      args.out,
    );
    return;
  }
  const visionRunEnv = await buildVisionRunEnv(args, vision, visionPreflight);
  if (vision.required && visionPreflight?.ok && visionRunEnv.error) {
    await printJsonPayload(
      {
        ok: false,
        cli_ok: false,
        exit_code: 1,
        status: "vision_service_credentials_unavailable",
        supervisor_state: "vision_service_credentials_unavailable",
        packet: packetPath,
        workspace,
        vision,
        vision_preflight: visionPreflight,
        error: visionRunEnv.error,
        next_action: "Set Z_AI_API_KEY or configure the Z.AI API key in the ZCode CLI config before required image-understanding tasks.",
      },
      args.out,
    );
    return;
  }
  await ensureCliPromptReady(args);
  const maxAttempts = positiveIntOrDefault(args.maxAttempts, DEFAULT_PROVIDER_MAX_ATTEMPTS);
  const retryDelayMs = nonNegativeIntOrDefault(args.retryDelayMs, DEFAULT_PROVIDER_RETRY_DELAY_MS);
  const providerRateLimitFailFastCount = args.providerRateLimitFailFast === false
    ? 0
    : positiveIntOrDefault(args.providerRateLimitFailFastCount, DEFAULT_PROVIDER_RATE_LIMIT_FAIL_FAST_COUNT);
  const snapshotPath = await createRunPacketSnapshot(workspace);
  const secretFiles = await snapshotSecretFiles(snapshotPath);
  if (secretFiles.length > 0) {
    await unlink(snapshotPath).catch(() => {});
    await printJsonPayload(
      {
        ok: false,
        cli_ok: false,
        exit_code: 1,
        status: "workspace_secret_paths_blocked",
        supervisor_state: "workspace_secret_paths_blocked",
        packet: packetPath,
        workspace,
        secret_files: secretFiles,
        next_action: "Remove secret-like files from the delegated workspace or use a sanitized worktree.",
      },
      args.out,
    );
    return;
  }
  const usageBefore = await captureUsageSnapshot(args, "before");
  const dbDeltaBefore = args.modelUsageDb
    ? await captureAppRunPacketDbDeltaBefore(args, packetPath)
    : null;
  const attempts = [];
  const retryDelaysMs = [];
  let finalResult = null;
  let promptText = packet.prompt;
  let currentAttempt = null;
  let terminalWritten = false;
  const writeTerminal = async (status, options = {}) => {
    if (terminalWritten) return;
    terminalWritten = true;
    await writeRunPacketProgress(args, runPacketTerminalPayload({
      status,
      packetPath,
      workspace,
      attempts,
      maxAttempts,
      currentAttempt,
      ...options,
    }));
    await unlink(snapshotPath).catch(() => {});
  };
  const removeSignalHandlers = installRunPacketSignalHandlers(writeTerminal);

  try {
    await writeRunPacketProgress(args, {
      ok: false,
      exit_code: 1,
      status: "running",
      supervisor_state: "running",
      packet: packetPath,
      workspace,
      attempts: 0,
      attempt_count: 0,
      max_attempts: maxAttempts,
      validation_ok: null,
    });
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      currentAttempt = attempt;
      await writeRunPacketProgress(args, {
        ok: false,
        exit_code: 1,
        status: "running",
        supervisor_state: "running",
        packet: packetPath,
        workspace,
        attempts: attempts.length,
        attempt_count: attempts.length,
        current_attempt: attempt,
        max_attempts: maxAttempts,
        validation_ok: null,
        attempt_results: attempts.map(compactAttemptRecord),
      });
      const cliArgs = buildPromptArgs(
        {
          ...args,
          mode: args.mode ?? packet.mode,
          text: promptText,
          textFile: undefined,
          attach: [...(args.attach ?? []), ...vision.attached_files],
        },
        promptText,
        workspace,
      );
      const result = await runZcodeCli(cliArgs, {
        cwd: workspace,
        timeoutMs: args.timeoutMs ?? PROMPT_TIMEOUT_MS,
        env: visionRunEnv.env,
        acceptValidatedArtifactAfterMs: args.acceptValidatedArtifactAfterMs ?? 0,
        providerRateLimitFailFastCount,
        auditValidatedArtifact: () => auditRunPacketAttempt({
          workspace,
          packetPath,
          snapshotPath,
          validationTimeout: args.validationTimeout,
        }),
      });
      const attemptUsage = normalizedZcodeUsageFromStdout(result.stdout ?? "");
      const providerUsageLedger = await appendProviderUsageLedger(attemptUsage, {
        packet,
        packetPath,
        attempt,
      }).catch((error) => ({
        appended: false,
        status: "write_failed",
        path: providerUsageLedgerTarget(process.env, packetPath).path,
        error: error.message,
      }));
      const audit = await auditRunPacketAttempt({
        workspace,
        packetPath,
        snapshotPath,
        validationTimeout: args.validationTimeout,
      });
      const state = classifyProviderRunState({ cliOk: result.cli_ok, provider: result, audit });
      const attemptResult = { ...result, provider_usage_ledger: providerUsageLedger, audit, ...state };
      attempts.push(attemptResult);
      if (state.supervisor_state === "run_timeout") {
        finalResult = attemptResult;
        break;
      }
      await writeRunPacketProgress(args, {
        ok: false,
        exit_code: 1,
        status: state.supervisor_state,
        supervisor_state: state.supervisor_state,
        packet: packetPath,
        workspace,
        attempts: attempts.length,
        attempt_count: attempts.length,
        current_attempt: attempt,
        max_attempts: maxAttempts,
        validation_ok: audit?.validation?.ok ?? null,
        audit_ok: audit?.ok ?? null,
        changed_count: audit?.changed_count ?? null,
        attempt_results: attempts.map(compactAttemptRecord),
      });
      const shouldRetry = (
        state.supervisor_state === "retryable_provider_error" &&
        attempt < maxAttempts
      );
      if (shouldRetry) {
        retryDelaysMs.push(retryDelayMs);
        if (retryDelayMs > 0) await sleep(retryDelayMs);
        continue;
      }
      const shouldRepairValidation = (
        args.repairValidation !== false &&
        ["audit_failed", "unsafe_partial"].includes(state.supervisor_state) &&
        isRepairableValidationFailure(audit) &&
        attempt < maxAttempts
      );
      if (shouldRepairValidation) {
        retryDelaysMs.push(0);
        promptText = validationRepairPrompt(packet, audit, attempt + 1);
        continue;
      }
      finalResult = attemptResult;
      break;
    }

    const usageAfter = await captureUsageSnapshot(args, "after");
    const dbDeltaAfter = dbDeltaBefore
      ? await captureAppRunPacketDbDeltaAfter(args, packetPath)
      : null;
    const runUsage = normalizedZcodeUsageFromStdout(finalResult.stdout ?? "");
    const outputPath = args.out ? resolve(args.out) : join(dirname(resolve(packetPath)), "zcode-run.json");
    const usageAccounting = buildUsageAccounting(finalResult, usageBefore, usageAfter, {
      outputPath,
      dbDeltaBefore: dbDeltaBefore?.before ?? null,
      dbDeltaAfter: dbDeltaAfter?.after ?? null,
    });
    const finalOk = ["success", "partial_success"].includes(finalResult.supervisor_state);
    terminalWritten = true;
    await printCliResult(
      {
        ...finalResult,
        ok: finalOk,
        packet: packetPath,
        workspace,
        mode: mapMode(args.mode ?? packet.mode),
        worker_finalization: packet.worker_finalization ?? "zcode_owned",
        prompt_chars: packet.prompt.length,
        vision,
        vision_preflight: visionPreflight,
        vision_service_credential_source: visionRunEnv.credential_source,
        status: finalResult.supervisor_state,
        attempts: attempts.length,
        attempt_count: attempts.length,
        retry_count: Math.max(0, attempts.length - 1),
        retry_delays_ms: retryDelaysMs,
        max_attempts: maxAttempts,
        safe_to_retry_later: finalResult.safe_to_retry_later && attempts.length >= maxAttempts,
        timed_out: Boolean(finalResult.timed_out),
        timeout_ms: finalResult.timeout_ms ?? null,
        accepted_validated_artifact: Boolean(finalResult.accepted_validated_artifact),
        attempt_results: attempts.map(compactAttemptRecord),
        provider_usage_ledger_records_appended: attempts.filter((attempt) => attempt.provider_usage_ledger?.appended).length,
        usage_available: usageAccounting.usage_available,
        no_usage_reason: usageAccounting.no_usage_reason,
        worker_usage_status: usageAccounting.worker_usage_status,
        worker_usage_unit: usageAccounting.worker_usage_unit,
        worker_total_tokens: usageAccounting.worker_total_tokens,
        worker_usage_source_path: usageAccounting.worker_usage_source_path,
        worker_usage_capture_method: usageAccounting.worker_usage_capture_method,
        row_ids: usageAccounting.row_ids,
        row_count: usageAccounting.row_count,
        rows: usageAccounting.rows,
        response: runUsage.response,
        audit: finalResult.audit,
        validation: finalResult.audit?.validation ?? null,
        validation_ok: finalResult.audit?.validation?.ok ?? null,
        usage: runUsage.usage,
        usage_normalized: runUsage.normalized,
        projection: runUsage.projection,
        usage_snapshots: {
          before: usageBefore,
          after: usageAfter,
        },
        usage_accounting: usageAccounting,
        quota_percent_status: usageAccounting.quota_percent_status,
        quota_percent_unavailable_reason: usageAccounting.quota_percent_unavailable_reason,
      },
      args.out,
    );
  } finally {
    removeSignalHandlers();
    await unlink(snapshotPath).catch(() => {});
  }
}

async function doctor() {
  const payload = await runJson(pythonCommand(), [
    "tools/zcode_eval/zcode_eval.py",
    "doctor",
    "--json",
  ]);
  console.log(JSON.stringify(payload, null, 2));
}

async function launch(port, options = {}) {
  const payload = await runJson("cua-driver", [
    "call",
    "launch_app",
    JSON.stringify({
      bundle_id: DEFAULT_BUNDLE_ID,
      electron_debugging_port: port,
      creates_new_application_instance: Boolean(options.newInstance),
    }),
  ]);
  payload.cdp = await probeCdp(port);
  console.log(JSON.stringify(payload, null, 2));
}

async function fetchTargets(port) {
  return getJson(port, "/json/list");
}

async function probeCdp(port) {
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const version = await getJson(port, "/json/version", { duringLaunch: true });
      return { ok: true, browser: version.Browser ?? null };
    } catch (error) {
      lastError = error;
      await new Promise((resolveWait) => setTimeout(resolveWait, 500));
    }
  }
  return { ok: false, error: lastError?.message ?? "unknown CDP error" };
}

function getJson(port, path, options = {}) {
  return new Promise((resolveJson, rejectJson) => {
    const unavailableMessage = (detail) =>
      options.duringLaunch
        ? `ZCode launched, but CDP did not become reachable on port ${port}. Confirm Electron remote debugging is enabled for ZCode, or retry with a different --port. ${detail}`
        : `ZCode CDP is not reachable on port ${port}. Run \`zcodectl launch --port ${port}\` first, then retry this command. ${detail}`;
    const request = http.get(
      {
        host: "127.0.0.1",
        port,
        path,
        timeout: 5000,
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if ((response.statusCode ?? 500) >= 400) {
            rejectJson(new Error(`CDP HTTP ${response.statusCode}: ${body.slice(0, 500)}`));
            return;
          }
          try {
            resolveJson(JSON.parse(body));
          } catch (error) {
            rejectJson(error);
          }
        });
      },
    );
    request.on("timeout", () => {
      request.destroy(new Error(unavailableMessage("Connection timed out.")));
    });
    request.on("error", (error) => {
      if (["ECONNREFUSED", "ECONNRESET", "EHOSTUNREACH", "ENOTFOUND"].includes(error.code)) {
        rejectJson(new Error(unavailableMessage(error.message)));
        return;
      }
      rejectJson(error);
    });
  });
}

function pickMainPage(targets) {
  const page = targets.find(
    (target) =>
      target.type === "page" &&
      target.title === "ZCode" &&
      target.webSocketDebuggerUrl,
  );
  if (!page) {
    throw new Error("No ZCode page target found. Run launch first.");
  }
  return page;
}

async function printTargets(port) {
  const targets = await fetchTargets(port);
  const slim = targets.map((target) => ({
    id: target.id,
    type: target.type,
    title: target.title,
    url: target.url,
    has_websocket: Boolean(target.webSocketDebuggerUrl),
  }));
  console.log(JSON.stringify(slim, null, 2));
}

async function cdpCommand(wsUrl, method, params = {}) {
  const socket = new WebSocket(wsUrl);
  const id = Math.floor(Math.random() * 1_000_000);
  await new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", rejectOpen, { once: true });
  });

  const result = await new Promise((resolveMessage, rejectMessage) => {
    const timeout = setTimeout(() => rejectMessage(new Error(`CDP timeout: ${method}`)), 15000);
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      clearTimeout(timeout);
      if (message.error) rejectMessage(new Error(JSON.stringify(message.error)));
      else resolveMessage(message.result);
    });
    socket.send(JSON.stringify({ id, method, params }));
  });
  socket.close();
  return result;
}

async function evaluate(port, expression) {
  const result = await runtimeEvaluate(port, expression);
  console.log(JSON.stringify(result, null, 2));
}

async function runtimeEvaluate(port, expression) {
  const target = pickMainPage(await fetchTargets(port));
  const result = await cdpCommand(target.webSocketDebuggerUrl, "Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  return result.result;
}

async function text(port, max = 4000) {
  const expression = `(() => {
    const text = document.body ? document.body.innerText : "";
    return text.slice(0, ${JSON.stringify(max)});
  })()`;
  await evaluate(port, expression);
}

async function textboxes(port) {
  const expression = `(() => Array.from(document.querySelectorAll('textarea,input,[contenteditable=true],[role=textbox]')).map((el, i) => ({
    i,
    tag: el.tagName,
    role: el.getAttribute('role'),
    contenteditable: el.getAttribute('contenteditable'),
    placeholder: el.getAttribute('placeholder'),
    aria: el.getAttribute('aria-label'),
    text: (el.innerText || el.value || el.textContent || '').slice(0, 160),
    rect: (() => {
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    })(),
  })))()`;
  await evaluate(port, expression);
}

async function buttons(port) {
  const expression = `(() => Array.from(document.querySelectorAll('button,[role=button]')).map((el, i) => ({
    i,
    text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 120),
    aria: el.getAttribute('aria-label'),
    disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
    rect: (() => {
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    })(),
  })))()`;
  await evaluate(port, expression);
}

async function summary(port) {
  await evaluate(port, summaryExpression());
}

async function openUsage(port) {
  await evaluate(port, openUsageExpression());
}

async function usageSnapshot(port, out) {
  const result = await runtimeEvaluate(port, usageSnapshotExpression());
  const payload = result.value ?? {};
  const textPayload = JSON.stringify(payload, null, 2);
  if (out) {
    const outputPath = resolve(out);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, `${textPayload}\n`);
  }
  console.log(textPayload);
}

async function newTask(port, workspace) {
  if (!workspace) throw new Error("--workspace is required");
  const expression = `(() => {
    const workspace = ${JSON.stringify(workspace)};
    const buttons = Array.from(document.querySelectorAll('button,[role=button]'));
    const workspaceRows = buttons
      .filter((node) => (node.innerText || node.textContent || '').trim() === workspace)
      .map((node) => node.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0)
      .sort((a, b) => a.y - b.y);
    if (!workspaceRows.length) return { ok: false, reason: 'workspace not found', workspace };
    const row = workspaceRows[workspaceRows.length - 1];
    const newTaskButton = buttons.find((node) => {
      const r = node.getBoundingClientRect();
      const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || '').trim();
      return text === 'New task' && Math.abs((r.y + r.height / 2) - (row.y + row.height / 2)) < 6;
    });
    if (!newTaskButton) return { ok: false, reason: 'workspace new task button not found', workspace };
    const r = newTaskButton.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2);
    const y = Math.round(r.y + r.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      newTaskButton.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
    }
    return { ok: true, workspace, x, y };
  })()`;
  await evaluate(port, expression);
}

async function setMode(port, mode) {
  if (!mode) throw new Error("--mode is required");
  const opened = await runtimeEvaluate(port, `(() => {
    const el = Array.from(document.querySelectorAll('button,[role=button]')).find((node) => node.getAttribute('aria-label') === 'Switch mode');
    if (!el) return { ok: false, reason: 'mode switch not found' };
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2);
    const y = Math.round(r.y + r.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
    }
    return { ok: true, current: (el.innerText || el.textContent || '').trim(), x, y };
  })()`);
  if (!opened.value?.ok) {
    console.log(JSON.stringify(opened, null, 2));
    return;
  }
  await new Promise((resolveWait) => setTimeout(resolveWait, 300));
  const expression = `(() => {
    const mode = ${JSON.stringify(mode)};
    const el = Array.from(document.querySelectorAll('[role=option],button,[role=button]'))
      .find((node) => (node.innerText || node.textContent || '').trim().includes(mode));
    if (!el) return { ok: false, reason: 'mode option not found', mode };
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2);
    const y = Math.round(r.y + r.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
    }
    return { ok: true, mode, x, y, text: (el.innerText || el.textContent || '').trim() };
  })()`;
  await evaluate(port, expression);
}

async function setComposerValue(port, promptText) {
  if (!promptText) throw new Error("--text is required");
  const expression = `(() => {
    const text = ${JSON.stringify(promptText)};
    const el = Array.from(document.querySelectorAll('[contenteditable=true],[role=textbox],textarea,input'))
      .find((node) => node.getBoundingClientRect().width > 100 && node.getBoundingClientRect().height > 10);
    if (!el) return { ok: false, reason: 'composer not found' };
    el.focus();
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      el.value = text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    } else {
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, text);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    }
    return {
      ok: true,
      text: (el.innerText || el.value || el.textContent || '').slice(0, 500),
    };
  })()`;
  return runtimeEvaluate(port, expression);
}

async function setComposer(port, promptText) {
  const result = await setComposerValue(port, promptText);
  console.log(JSON.stringify(result, null, 2));
}

async function submitTask(port, promptText) {
  if (!promptText) throw new Error("--text is required");
  await setComposer(port, promptText);
  await clickByText(port, "Send");
}

async function submitGoal(port, promptText) {
  const text = promptText.trimStart().startsWith("/goal")
    ? promptText
    : `/goal ${promptText}`;
  await submitTask(port, text);
}

async function waitIdleValue(port, timeoutMs = 300_000, intervalMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  let lastSummary = null;
  while (Date.now() <= deadline) {
    const result = await runtimeEvaluate(port, `(() => {
      const bodyText = document.body?.innerText || "";
      const buttons = Array.from(document.querySelectorAll('button,[role=button]')).map((el) => ({
        text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim(),
        aria: el.getAttribute('aria-label'),
        rect: (() => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; })(),
      })).filter((button) => button.rect.w > 0 && button.rect.h > 0);
      return {
        running: /\\bWorking for\\b/.test(bodyText) || buttons.some((button) => button.text === 'Stop' || button.aria === 'Stop'),
        awaitingApproval: /Awaiting approval|Permission required/.test(bodyText),
        workedFor: (bodyText.match(/Worked for\\s+([^\\n]+)/) || [])[1] || null,
        lastText: bodyText.slice(-3000),
      };
    })()`);
    lastSummary = result.value;
    if (lastSummary?.awaitingApproval) {
      return { ok: false, reason: "awaiting_approval", summary: lastSummary };
    }
    if (lastSummary && !lastSummary.running && lastSummary.workedFor) {
      return { ok: true, summary: lastSummary };
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, intervalMs));
  }
  return { ok: false, reason: "timeout", summary: lastSummary };
}

async function waitIdle(port, timeoutMs = 300_000, intervalMs = 2_000) {
  const result = await waitIdleValue(port, timeoutMs, intervalMs);
  console.log(JSON.stringify(result, null, 2));
}

async function clickByText(port, label) {
  if (!label) throw new Error("--text is required");
  await clickMatchingText(port, label, false);
}

async function clickByContainedText(port, needle) {
  if (!needle) throw new Error("--text is required");
  await clickMatchingText(port, needle, true);
}

async function clickMatchingTextValue(port, label, contains) {
  const expression = `(() => {
    const label = ${JSON.stringify(label)};
    const contains = ${JSON.stringify(contains)};
    const candidates = Array.from(document.querySelectorAll('button,[role=button]'));
    const el = candidates.find((node) => {
      const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || '').trim();
      const aria = node.getAttribute('aria-label') || '';
      return contains ? text.includes(label) || aria.includes(label) : text === label || aria === label;
    });
    if (!el) return { ok: false, reason: 'button not found', label };
    const disabled = Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true';
    if (disabled) return { ok: false, reason: 'button disabled', label };
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2);
    const y = Math.round(r.y + r.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
    }
    return { ok: true, label, contains, x, y };
  })()`;
  return runtimeEvaluate(port, expression);
}

async function clickMatchingText(port, label, contains) {
  const result = await clickMatchingTextValue(port, label, contains);
  console.log(JSON.stringify(result, null, 2));
}

async function screenshot(port, out) {
  if (!out) throw new Error("--out is required");
  const target = pickMainPage(await fetchTargets(port));
  await cdpCommand(target.webSocketDebuggerUrl, "Page.enable");
  const result = await cdpCommand(target.webSocketDebuggerUrl, "Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
  });
  const outputPath = resolve(out);
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, Buffer.from(result.data, "base64"));
  console.log(outputPath);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.command || args.command === "help" || args.command === "--help") {
    usage();
    return;
  }
  if (args.command === "doctor") await doctor();
  else if (args.command === "cli-path") await cliPath();
  else if (args.command === "cli-doctor") await cliDoctor(args.out);
  else if (args.command === "cli-preflight") await cliPreflight(args);
  else if (args.command === "cli-version") await cliVersion(args.out);
  else if (args.command === "api-profiles") await apiProfiles(args);
  else if (args.command === "bootstrap-cli-config") await bootstrapCliConfig(args);
  else if (args.command === "vision-preflight") await visionPreflightCommand(args);
  else if (args.command === "cli-prompt") await cliPrompt(args);
  else if (args.command === "run-packet") await runPacket(args);
  else if (args.command === "app-run-packet") await appRunPacket(args);
  else if (args.command === "launch") await launch(args.port, { newInstance: args.newInstance });
  else if (args.command === "targets") await printTargets(args.port);
  else if (args.command === "text") await text(args.port, args.max ?? 4000);
  else if (args.command === "eval") {
    if (!args.expr) throw new Error("--expr is required");
    await evaluate(args.port, args.expr);
  } else if (args.command === "textboxes") await textboxes(args.port);
  else if (args.command === "buttons") await buttons(args.port);
  else if (args.command === "summary") await summary(args.port);
  else if (args.command === "open-usage") await openUsage(args.port);
  else if (args.command === "usage") await usageSnapshot(args.port, args.out);
  else if (args.command === "new-task") await newTask(args.port, args.workspace);
  else if (args.command === "set-mode") await setMode(args.port, args.mode);
  else if (args.command === "set-composer") await setComposer(args.port, await readPrompt(args));
  else if (args.command === "submit-task") await submitTask(args.port, await readPrompt(args));
  else if (args.command === "goal") await submitGoal(args.port, await readPrompt(args));
  else if (args.command === "wait-idle") await waitIdle(args.port, args.timeoutMs ?? 300_000, args.intervalMs ?? 2_000);
  else if (args.command === "click") await clickByText(args.port, args.text);
  else if (args.command === "click-contains") await clickByContainedText(args.port, args.text);
  else if (args.command === "screenshot") await screenshot(args.port, args.out);
  else throw new Error(`Unknown command: ${args.command}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
