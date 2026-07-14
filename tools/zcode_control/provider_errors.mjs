const OVERLOAD_RE = /temporarily overloaded|try again later|overloaded_error/i;
const RATE_LIMIT_1302_RE = /\[1302\]\[Rate limit reached for requests\]|\bproviderCode:\s*['"]?1302['"]?|"providerCode"\s*:\s*"1302"|\brate_limit_error\b/i;

export const DEFAULT_PROVIDER_MAX_ATTEMPTS = 2;
export const DEFAULT_PROVIDER_RETRY_DELAY_MS = 60_000;
export const DEFAULT_PROVIDER_RATE_LIMIT_FAIL_FAST_COUNT = 3;

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match?.[1]) return match[1];
  }
  return null;
}

function firstProviderLine(text) {
  return text.split(/\r?\n/).find((line) => /ProviderBusinessError|PROVIDER_BUSINESS_ERROR/i.test(line)) ?? null;
}

export function providerRateLimit1302Count(text = "") {
  const errorLines = text.match(/ProviderBusinessError:\s*\[1302\]\[Rate limit reached for requests\]/gi);
  if (errorLines?.length) return errorLines.length;
  return RATE_LIMIT_1302_RE.test(text) ? 1 : 0;
}

export function classifyProviderError({ stdout = "", stderr = "", exitCode = null } = {}) {
  const text = `${stderr}\n${stdout}`;
  const exitCodeNumber = Number(exitCode);
  const exit143 = Number.isFinite(exitCodeNumber) && exitCodeNumber === 143;
  const providerBusiness = /ProviderBusinessError|PROVIDER_BUSINESS_ERROR|isProviderBusinessError:\s*true/i.test(text);
  const providerCode = firstMatch(text, [
    /providerCode:\s*['"]?(\d+)['"]?/,
    /"providerCode"\s*:\s*"(\d+)"/,
    /\[(\d{3,})\]\[/,
    /\bcode:\s*['"]?(\d{3,})['"]?/,
    /"code"\s*:\s*"(\d{3,})"/,
  ]);
  const rateLimit1302Count = providerRateLimit1302Count(text);
  const providerRateLimit1302 = providerCode === "1302" || rateLimit1302Count > 0;
  const temporary = OVERLOAD_RE.test(text) || providerCode === "1305" || providerRateLimit1302;
  const providerError = providerBusiness || temporary || providerCode === "1305" || providerRateLimit1302 || exit143;
  const providerErrorKind = providerCode === "1305" || (providerError && OVERLOAD_RE.test(text))
    ? "provider_overload"
    : providerRateLimit1302
      ? "provider_rate_limit_1302"
    : providerError
      ? "provider_error"
      : null;
  const blockerKind = ["provider_overload", "provider_rate_limit_1302"].includes(providerErrorKind) ? "infrastructure_blocker" : null;
  const providerMessage = firstMatch(text, [
    /providerMessage:\s*'([^']+)'/,
    /providerMessage:\s*"([^"]+)"/,
    /"providerMessage"\s*:\s*"([^"]+)"/,
    /ProviderBusinessError:\s*([^\n]+)/,
  ]) ?? (exit143 ? "ZCode CLI exited with code 143" : null);

  return {
    provider_error: providerError,
    provider_error_kind: providerErrorKind,
    blocker_kind: blockerKind,
    infrastructure_blocker: blockerKind === "infrastructure_blocker",
    provider_code: providerCode,
    provider_message: providerMessage,
    provider_id: firstMatch(text, [/providerId:\s*'([^']+)'/, /providerId:\s*"([^"]+)"/, /"providerId"\s*:\s*"([^"]+)"/]),
    provider_kind: firstMatch(text, [/providerKind:\s*'([^']+)'/, /providerKind:\s*"([^"]+)"/, /"providerKind"\s*:\s*"([^"]+)"/]),
    provider_request_id: firstMatch(text, [
      /providerRequestId:\s*'([^']+)'/,
      /providerRequestId:\s*"([^"]+)"/,
      /"providerRequestId"\s*:\s*"([^"]+)"/,
      /request_id:\s*'([^']+)'/,
      /"request_id"\s*:\s*"([^"]+)"/,
    ]),
    provider_error_line: firstProviderLine(text),
    provider_rate_limit_1302: providerRateLimit1302,
    provider_rate_limit_1302_count: rateLimit1302Count,
    provider_error_temporary: temporary,
    retryable_provider_error: providerError && (temporary || exit143),
  };
}

function parsedJsonObjects(stdout) {
  const trimmed = stdout.trim();
  if (!trimmed) return [];
  const candidates = [trimmed, ...trimmed.split(/\r?\n/).filter((line) => line.trim().startsWith("{"))];
  const objects = [];
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) objects.push(parsed);
    } catch {
      // CLI output may be prose, JSONL, or empty; non-JSON chunks are ignored.
    }
  }
  return objects;
}

function hasUsageShape(value) {
  if (!value || typeof value !== "object") return false;
  const tokenKeys = [
    "totalTokens",
    "tokensTotal",
    "tokensUsed",
    "tokens_used",
    "tokens_total",
    "total_tokens",
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
  ];
  return tokenKeys.some((key) => {
    const item = value[key];
    return typeof item === "number" || (
      typeof item === "string" &&
      item.trim() !== "" &&
      Number.isFinite(Number(item))
    );
  });
}

function nestedUsageAvailable(value, seen = new Set(), depth = 0) {
  if (depth > 5 || !value || typeof value !== "object" || Array.isArray(value) || seen.has(value)) return false;
  seen.add(value);
  if (hasUsageShape(value)) return true;
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
    if (nestedUsageAvailable(nested, seen, depth + 1)) return true;
    if (Array.isArray(nested) && nested.some((item) => nestedUsageAvailable(item, seen, depth + 1))) return true;
  }
  return false;
}

export function usageAvailableFromStdout(stdout = "") {
  return parsedJsonObjects(stdout).some((payload) => nestedUsageAvailable(payload));
}

export function classifyProviderRunState({ cliOk, provider, audit }) {
  if (cliOk) {
    if (audit?.ok === false) {
      const changedCount = Number.isFinite(Number(audit?.changed_count)) ? Number(audit.changed_count) : null;
      return {
        supervisor_state: "audit_failed",
        partial_artifacts_possible: changedCount === null ? true : changedCount > 0,
        safe_to_retry_later: false,
      };
    }
    return {
      supervisor_state: "success",
      partial_artifacts_possible: false,
      safe_to_retry_later: false,
    };
  }
  if (provider?.timed_out) {
    const changedCount = Number.isFinite(Number(audit?.changed_count)) ? Number(audit.changed_count) : null;
    return {
      supervisor_state: "run_timeout",
      partial_artifacts_possible: changedCount === null ? true : changedCount > 0,
      safe_to_retry_later: changedCount === 0,
    };
  }
  if (!provider?.provider_error) {
    return {
      supervisor_state: "cli_error",
      partial_artifacts_possible: false,
      safe_to_retry_later: false,
    };
  }

  const changedCount = Number.isFinite(Number(audit?.changed_count)) ? Number(audit.changed_count) : null;
  if (changedCount === 0 && provider.retryable_provider_error) {
    return {
      supervisor_state: "retryable_provider_error",
      partial_artifacts_possible: false,
      safe_to_retry_later: true,
    };
  }
  if (changedCount !== null && changedCount > 0 && audit?.ok === true) {
    return {
      supervisor_state: "partial_success",
      partial_artifacts_possible: true,
      safe_to_retry_later: false,
    };
  }
  return {
    supervisor_state: "unsafe_partial",
    partial_artifacts_possible: changedCount === null ? true : changedCount > 0,
    safe_to_retry_later: false,
  };
}
