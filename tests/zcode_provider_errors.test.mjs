import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyProviderError,
  classifyProviderRunState,
  usageAvailableFromStdout,
} from "../tools/zcode_control/provider_errors.mjs";

const overloadStderr = `ProviderBusinessError: [1305][The service may be temporarily overloaded, please try again later][req-1]
  code: 'PROVIDER_BUSINESS_ERROR',
  isProviderBusinessError: true,
  providerCode: '1305',
  providerId: 'zai',
  providerKind: 'anthropic',
  providerMessage: '[1305][The service may be temporarily overloaded, please try again later][req-1]'`;

const rateLimit1302Stderr = `ProviderBusinessError: [1302][Rate limit reached for requests][req-1]
  code: 'PROVIDER_BUSINESS_ERROR',
  isProviderBusinessError: true,
  providerCode: '1302',
  providerId: 'zai',
  providerKind: 'anthropic',
  providerMessage: '[1302][Rate limit reached for requests][req-1]'
ProviderBusinessError: [1302][Rate limit reached for requests][req-2]
  code: 'PROVIDER_BUSINESS_ERROR',
  isProviderBusinessError: true,
  providerCode: '1302',
  providerId: 'zai',
  providerKind: 'anthropic',
  providerMessage: '[1302][Rate limit reached for requests][req-2]'`;

test("classifies ZCode provider overload stderr", () => {
  const provider = classifyProviderError({ stderr: overloadStderr, exitCode: 143 });

  assert.equal(provider.provider_error, true);
  assert.equal(provider.provider_code, "1305");
  assert.equal(provider.provider_id, "zai");
  assert.equal(provider.provider_kind, "anthropic");
  assert.equal(provider.provider_error_kind, "provider_overload");
  assert.equal(provider.blocker_kind, "infrastructure_blocker");
  assert.equal(provider.infrastructure_blocker, true);
  assert.equal(provider.provider_error_temporary, true);
  assert.equal(provider.retryable_provider_error, true);
});

test("exit code 143 is classified for supervisor handling", () => {
  const provider = classifyProviderError({ stderr: "", exitCode: 143 });

  assert.equal(provider.provider_error, true);
  assert.equal(provider.provider_code, null);
  assert.equal(provider.retryable_provider_error, true);
});

test("classifies repeated ZCode provider 1302 rate limit distinctly", () => {
  const provider = classifyProviderError({ stderr: rateLimit1302Stderr, exitCode: 143 });

  assert.equal(provider.provider_error, true);
  assert.equal(provider.provider_code, "1302");
  assert.equal(provider.provider_error_kind, "provider_rate_limit_1302");
  assert.equal(provider.blocker_kind, "infrastructure_blocker");
  assert.equal(provider.infrastructure_blocker, true);
  assert.equal(provider.provider_rate_limit_1302, true);
  assert.equal(provider.provider_rate_limit_1302_count, 2);
  assert.equal(provider.provider_error_temporary, true);
  assert.equal(provider.retryable_provider_error, true);
});

test("no-change provider error is retryable while valid changed artifacts are partial success", () => {
  const provider = classifyProviderError({ stderr: overloadStderr, exitCode: 143 });

  assert.equal(
    classifyProviderRunState({ cliOk: false, provider, audit: { changed_count: 0, ok: false } }).supervisor_state,
    "retryable_provider_error",
  );
  assert.equal(
    classifyProviderRunState({ cliOk: false, provider, audit: { changed_count: 2, ok: true } }).supervisor_state,
    "partial_success",
  );
});

test("successful CLI result is blocked when supervisor audit fails", () => {
  assert.equal(
    classifyProviderRunState({
      cliOk: true,
      provider: { provider_error: false },
      audit: { changed_count: 1, ok: false },
    }).supervisor_state,
    "audit_failed",
  );
  assert.equal(
    classifyProviderRunState({
      cliOk: true,
      provider: { provider_error: false },
      audit: { changed_count: 0, ok: true },
    }).supervisor_state,
    "success",
  );
});

test("detects usage JSON only when token usage is present", () => {
  assert.equal(usageAvailableFromStdout('{"usage":{"input_tokens":10,"output_tokens":2}}\n'), true);
  assert.equal(usageAvailableFromStdout('progress\n{"usage_accounting":{"tokens_used":12}}\n'), true);
  assert.equal(usageAvailableFromStdout('{"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n'), true);
  assert.equal(
    usageAvailableFromStdout(
      '{"type":"response.completed","response":{"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}\n',
    ),
    true,
  );
  assert.equal(usageAvailableFromStdout("plain final answer\n"), false);
});
