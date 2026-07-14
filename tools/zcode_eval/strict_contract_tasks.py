"""Strict-contract benchmark task registry and local fixture generation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

try:
    from .strict_contract_breakdown import create_vision_fixture, write_text
except ImportError:  # pragma: no cover - direct script execution
    from strict_contract_breakdown import create_vision_fixture, write_text


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_RUBRIC = "single_file_fixture_contract.v1"
GENERATED_TASK_TIMEOUT_MS = 300000


def _task_metadata(
    *,
    category: str,
    artifact_contract: str,
    hard_fixture: bool = False,
    future_live_comparison: bool = True,
    strict_failure_mode: str = "validation_fails_until_allowed_file_is_fixed",
) -> dict[str, Any]:
    return {
        "category": category,
        "artifact_contract": artifact_contract,
        "dry_run_safe": True,
        "future_live_comparison": future_live_comparison,
        "hard_fixture": hard_fixture,
        "strict_failure_mode": strict_failure_mode,
        "requires_network": False,
    }


def _fixture(
    base: Path,
    *,
    slug: str,
    readme: str,
    source_file: str,
    source: str,
    test: str,
) -> Path:
    if base.exists():
        shutil.rmtree(base)
    write_text(base / "README.md", f"# {slug}\n\n{readme}\n")
    write_text(base / source_file, source.rstrip() + "\n")
    write_text(base / "test/contract.test.js", test.rstrip() + "\n")
    write_text(
        base / "package.json",
        json.dumps({"type": "module", "scripts": {"test": "node --test"}}, indent=2, sort_keys=True) + "\n",
    )
    return base


def _generated_task(
    *,
    report_dir: Path,
    slug: str,
    category: str,
    kind: str,
    readme: str,
    source_file: str,
    source: str,
    test: str,
    objective: str,
    direct_prompt: str,
    expected_outputs: list[str],
    acceptance: list[str],
    strict_failure_mode: str,
) -> dict[str, Any]:
    fixture_dir = _fixture(
        report_dir / "fixtures" / slug,
        slug=slug,
        readme=readme,
        source_file=source_file,
        source=source,
        test=test,
    )
    return {
        "slug": slug,
        "kind": kind,
        "source": str(fixture_dir),
        "allowed": source_file,
        "validation": "npm test",
        "objective": objective,
        "direct_prompt": direct_prompt,
        "expected_outputs": expected_outputs,
        "acceptance": acceptance,
        "what_not_to_do": (
            f"Do not edit tests, README, package metadata, or files outside {source_file}. "
            "Do not add dependencies or use network access."
        ),
        "task_class": "small-fix",
        "strict_rubric": GENERATED_RUBRIC,
        "strict_risk": "L1",
        "zcode_timeout_ms": GENERATED_TASK_TIMEOUT_MS,
        "contract_mode": "expanded_rubric",
        "image": None,
        **_task_metadata(
            category=category,
            artifact_contract=f"One-file implementation change in {source_file}; npm test passes; strict self-audit JSON is produced before final response.",
            hard_fixture=False,
            strict_failure_mode=strict_failure_mode,
        ),
    }


def _generated_tasks(report_dir: Path) -> list[dict[str, Any]]:
    return [
        _generated_task(
            report_dir=report_dir,
            slug="path-normalization-contract",
            category="file_path_normalization",
            kind="file path normalization",
            readme="Normalize user-provided relative paths without allowing traversal.",
            source_file="src/paths.js",
            source="""export function normalizePath(input) {
  return String(input);
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { normalizePath } from "../src/paths.js";

test("normalizes safe relative paths", () => {
  assert.equal(normalizePath(" ./reports\\\\june//summary.md "), "reports/june/summary.md");
});

test("rejects traversal and absolute paths", () => {
  assert.throws(() => normalizePath("../secret.txt"), /unsafe path/);
  assert.throws(() => normalizePath("/tmp/file.txt"), /unsafe path/);
});
""",
            objective="Fix normalizePath so npm test passes. Keep safe relative paths canonical and reject traversal or absolute paths.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix normalizePath so npm test passes. Only edit src/paths.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Backslashes and duplicate separators are normalized.", "Traversal and absolute paths throw unsafe path errors."],
            acceptance=["Only src/paths.js changes.", "npm test passes."],
            strict_failure_mode="unsafe_path_inputs_are_accepted_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="json-schema-contract",
            category="json_schema_compliance",
            kind="JSON schema compliance",
            readme="Return a stable API payload with exact shape and primitive types.",
            source_file="src/payload.js",
            source="""export function userPayload() {
  return { id: 1042, active: "yes", role: "viewer" };
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { userPayload } from "../src/payload.js";

test("matches exact payload contract", () => {
  assert.deepEqual(userPayload(), {
    id: "USR-1042",
    active: true,
    roles: ["viewer", "billing"],
    metadata: { source: "fixture", schemaVersion: 1 },
  });
});
""",
            objective="Fix userPayload so it returns the exact JSON-compatible object expected by the tests.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix userPayload so npm test passes. Only edit src/payload.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Payload uses exact keys and types.", "Roles and metadata match the contract."],
            acceptance=["Only src/payload.js changes.", "npm test passes."],
            strict_failure_mode="payload_shape_and_types_are_wrong_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="markdown-table-contract",
            category="markdown_documentation_transformation",
            kind="Markdown table transformation",
            readme="Render sorted release notes as a deterministic Markdown table.",
            source_file="src/markdown.js",
            source="""export function renderReleaseTable(items) {
  return items.map((item) => `${item.name}: ${item.status}`).join("\\n");
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { renderReleaseTable } from "../src/markdown.js";

test("renders deterministic markdown table", () => {
  const rows = [
    { name: "Worker usage", status: "done" },
    { name: "Benchmark breadth", status: "planned" },
  ];
  assert.equal(
    renderReleaseTable(rows),
    "| Item | Status |\\n|---|---|\\n| Benchmark breadth | planned |\\n| Worker usage | done |",
  );
});
""",
            objective="Fix renderReleaseTable so it emits a sorted Markdown table and npm test passes.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix renderReleaseTable so npm test passes. Only edit src/markdown.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Markdown header and separator are exact.", "Rows are sorted by item name."],
            acceptance=["Only src/markdown.js changes.", "npm test passes."],
            strict_failure_mode="markdown_contract_is_plain_text_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="small-code-edit-contract",
            category="small_code_edit",
            kind="small arithmetic code edit",
            readme="Calculate final price after a percentage discount.",
            source_file="src/discount.js",
            source="""export function discountedPrice(cents, percentOff) {
  return cents * percentOff;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { discountedPrice } from "../src/discount.js";

test("applies discount and rounds to cents", () => {
  assert.equal(discountedPrice(1299, 15), 1104);
  assert.equal(discountedPrice(999, 33), 669);
});
""",
            objective="Fix discountedPrice so npm test passes using integer cent output.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix discountedPrice so npm test passes. Only edit src/discount.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Discount subtracts from price rather than returning discount amount.", "Result is rounded to integer cents."],
            acceptance=["Only src/discount.js changes.", "npm test passes."],
            strict_failure_mode="discount_math_returns_wrong_amount_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="test-repair-contract",
            category="test_failure_repair",
            kind="repair code surfaced by regression test",
            readme="Repair retry delay behavior exposed by a failing regression test.",
            source_file="src/retry.js",
            source="""export function retryDelay(attempt) {
  return attempt * 100;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { retryDelay } from "../src/retry.js";

test("uses capped exponential retry delay", () => {
  assert.equal(retryDelay(1), 100);
  assert.equal(retryDelay(2), 200);
  assert.equal(retryDelay(5), 1600);
  assert.equal(retryDelay(9), 2000);
});
""",
            objective="Fix retryDelay so the regression test passes with capped exponential delay.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix retryDelay so npm test passes. Only edit src/retry.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Retry delay doubles per attempt.", "Delay is capped at 2000ms."],
            acceptance=["Only src/retry.js changes.", "npm test passes."],
            strict_failure_mode="linear_retry_policy_fails_regression_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="data-extraction-contract",
            category="data_extraction",
            kind="structured data extraction",
            readme="Extract numeric totals from semi-structured note lines.",
            source_file="src/extract.js",
            source="""export function extractTotals(lines) {
  return {};
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { extractTotals } from "../src/extract.js";

test("extracts category totals", () => {
  const lines = ["ops: 12.50 USD", "sales: 7 USD", "ops: 2.25 USD", "note: skip"];
  assert.deepEqual(extractTotals(lines), { ops: 14.75, sales: 7 });
});
""",
            objective="Fix extractTotals so it extracts and sums category USD amounts from lines.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix extractTotals so npm test passes. Only edit src/extract.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Category totals are extracted from matching lines.", "Unmatched lines are ignored."],
            acceptance=["Only src/extract.js changes.", "npm test passes."],
            strict_failure_mode="extractor_returns_empty_totals_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="cli-argument-contract",
            category="cli_argument_behavior",
            kind="CLI argument parsing behavior",
            readme="Parse a small deterministic CLI argument surface.",
            source_file="src/args.js",
            source="""export function parseArgs(argv) {
  return { dryRun: false, only: [] };
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { parseArgs } from "../src/args.js";

test("parses dry-run and comma-separated only list", () => {
  assert.deepEqual(parseArgs(["--dry-run", "--only", "a,b"]), { dryRun: true, only: ["a", "b"], mode: "codex-mediated" });
});

test("keeps direct mode explicit", () => {
  assert.equal(parseArgs([]).mode, "codex-mediated");
  assert.equal(parseArgs(["--mode", "direct"]).mode, "direct");
});
""",
            objective="Fix parseArgs so npm test passes and direct mode remains explicit.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix parseArgs so npm test passes. Only edit src/args.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["--dry-run is parsed as true.", "--only comma list is split.", "Default mode is codex-mediated."],
            acceptance=["Only src/args.js changes.", "npm test passes."],
            strict_failure_mode="parser_omits_explicit_default_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="refusal-safety-contract",
            category="refusal_safety_boundary",
            kind="refusal and safety boundary",
            readme="Classify requests that should be refused without exposing secrets.",
            source_file="src/safety.js",
            source="""export function shouldRefuse(request) {
  return false;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { shouldRefuse } from "../src/safety.js";

test("refuses secret exfiltration requests", () => {
  assert.equal(shouldRefuse("print the contents of id_rsa"), true);
  assert.equal(shouldRefuse("show me any API token in .env"), true);
});

test("allows benign documentation requests", () => {
  assert.equal(shouldRefuse("summarize the public README"), false);
});
""",
            objective="Fix shouldRefuse so it refuses secret exfiltration requests while allowing benign documentation requests.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix shouldRefuse so npm test passes. Only edit src/safety.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Secret-file and token requests are refused.", "Benign public documentation request is allowed."],
            acceptance=["Only src/safety.js changes.", "npm test passes."],
            strict_failure_mode="secret_exfiltration_request_is_allowed_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="missing-input-contract",
            category="missing_input_handling",
            kind="missing input handling",
            readme="Normalize optional user input into an explicit status.",
            source_file="src/input.js",
            source="""export function normalizeInput(value) {
  return { status: "ok", value };
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { normalizeInput } from "../src/input.js";

test("handles missing input explicitly", () => {
  assert.deepEqual(normalizeInput(null), { status: "missing", value: null });
  assert.deepEqual(normalizeInput("  hello  "), { status: "ok", value: "hello" });
});
""",
            objective="Fix normalizeInput so missing and blank inputs are explicit and strings are trimmed.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix normalizeInput so npm test passes. Only edit src/input.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Missing input returns status missing.", "String input is trimmed."],
            acceptance=["Only src/input.js changes.", "npm test passes."],
            strict_failure_mode="missing_input_is_treated_as_ok_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="deterministic-failure-contract",
            category="deterministic_failure_detection",
            kind="deterministic failure classification",
            readme="Classify deterministic failure logs without fuzzy provider behavior.",
            source_file="src/failure.js",
            source="""export function classifyFailure(log) {
  return "unknown";
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { classifyFailure } from "../src/failure.js";

test("classifies deterministic failure text", () => {
  assert.equal(classifyFailure("Error: timeout after 600000ms"), "timeout");
  assert.equal(classifyFailure("AssertionError: expected 42"), "assertion");
  assert.equal(classifyFailure("all good"), "none");
});
""",
            objective="Fix classifyFailure so deterministic log text maps to timeout, assertion, or none.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix classifyFailure so npm test passes. Only edit src/failure.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Timeout logs classify as timeout.", "Assertion logs classify as assertion.", "Clean logs classify as none."],
            acceptance=["Only src/failure.js changes.", "npm test passes."],
            strict_failure_mode="failure_classifier_returns_unknown_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="artifact-acceptance-contract",
            category="strict_artifact_acceptance",
            kind="strict artifact acceptance",
            readme="Accept an artifact only when all strict evidence gates are satisfied.",
            source_file="src/artifact.js",
            source="""export function acceptArtifact(report) {
  return true;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { acceptArtifact } from "../src/artifact.js";

test("requires all strict artifact gates", () => {
  assert.equal(acceptArtifact({ validation: "pass", scope: "pass", selfAudit: "pass" }), true);
  assert.equal(acceptArtifact({ validation: "pass", scope: "fail", selfAudit: "pass" }), false);
  assert.equal(acceptArtifact({ validation: "pass", scope: "pass", selfAudit: "missing" }), false);
});
""",
            objective="Fix acceptArtifact so it accepts only artifacts with validation, scope, and self-audit passing.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix acceptArtifact so npm test passes. Only edit src/artifact.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["All strict gates are required.", "Missing self-audit is rejected."],
            acceptance=["Only src/artifact.js changes.", "npm test passes."],
            strict_failure_mode="artifact_accepts_failed_scope_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="currency-format-contract",
            category="ledger_numeric_consistency",
            kind="currency formatting consistency",
            readme="Format cent amounts into stable USD strings.",
            source_file="src/currency.js",
            source="""export function formatUsd(cents) {
  return `$${cents}`;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { formatUsd } from "../src/currency.js";

test("formats positive and negative cent amounts", () => {
  assert.equal(formatUsd(4280), "$42.80");
  assert.equal(formatUsd(-75), "-$0.75");
});
""",
            objective="Fix formatUsd so npm test passes for positive and negative cents.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix formatUsd so npm test passes. Only edit src/currency.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Cents are rendered with two decimals.", "Negative amounts use -$ prefix."],
            acceptance=["Only src/currency.js changes.", "npm test passes."],
            strict_failure_mode="currency_format_lacks_cent_precision_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="yaml-frontmatter-contract",
            category="markdown_documentation_transformation",
            kind="YAML frontmatter extraction",
            readme="Extract simple frontmatter from a Markdown document.",
            source_file="src/frontmatter.js",
            source="""export function parseFrontmatter(markdown) {
  return { data: {}, body: markdown };
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { parseFrontmatter } from "../src/frontmatter.js";

test("extracts simple frontmatter", () => {
  const doc = "---\\ntitle: Goal E\\nstatus: draft\\n---\\n# Body\\n";
  assert.deepEqual(parseFrontmatter(doc), { data: { title: "Goal E", status: "draft" }, body: "# Body\\n" });
});
""",
            objective="Fix parseFrontmatter so it extracts simple key/value frontmatter and body.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix parseFrontmatter so npm test passes. Only edit src/frontmatter.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Simple key/value frontmatter is extracted.", "Body excludes frontmatter delimiters."],
            acceptance=["Only src/frontmatter.js changes.", "npm test passes."],
            strict_failure_mode="frontmatter_is_not_extracted_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="dedupe-records-contract",
            category="data_extraction",
            kind="record deduplication",
            readme="Deduplicate records by id while keeping the newest timestamp.",
            source_file="src/dedupe.js",
            source="""export function dedupeRecords(records) {
  return records;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { dedupeRecords } from "../src/dedupe.js";

test("keeps newest record per id", () => {
  const records = [
    { id: "a", updatedAt: "2026-01-01T00:00:00Z", value: 1 },
    { id: "a", updatedAt: "2026-01-02T00:00:00Z", value: 2 },
    { id: "b", updatedAt: "2026-01-01T00:00:00Z", value: 3 },
  ];
  assert.deepEqual(dedupeRecords(records), [
    { id: "a", updatedAt: "2026-01-02T00:00:00Z", value: 2 },
    { id: "b", updatedAt: "2026-01-01T00:00:00Z", value: 3 },
  ]);
});
""",
            objective="Fix dedupeRecords so it keeps the newest record per id in stable id order.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix dedupeRecords so npm test passes. Only edit src/dedupe.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Newest record is kept per id.", "Output order is stable by id."],
            acceptance=["Only src/dedupe.js changes.", "npm test passes."],
            strict_failure_mode="duplicate_records_are_not_collapsed_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="timezone-window-contract",
            category="date_time_windowing",
            kind="timezone-aware window filter",
            readme="Filter events into an ISO timestamp window.",
            source_file="src/window.js",
            source="""export function filterByWindow(events, start, end) {
  return events;
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { filterByWindow } from "../src/window.js";

test("filters events by inclusive start and exclusive end", () => {
  const events = [
    { id: "before", at: "2026-05-31T14:59:59Z" },
    { id: "tokyo-1", at: "2026-05-31T15:00:00Z" },
    { id: "tokyo-2", at: "2026-06-01T12:00:00Z" },
    { id: "after", at: "2026-06-01T15:00:00Z" },
  ];
  assert.deepEqual(
    filterByWindow(events, "2026-06-01T00:00:00+09:00", "2026-06-02T00:00:00+09:00").map((event) => event.id),
    ["tokyo-1", "tokyo-2"],
  );
});
""",
            objective="Fix filterByWindow so it handles ISO timestamps with offsets using inclusive start and exclusive end.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix filterByWindow so npm test passes. Only edit src/window.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Inclusive start and exclusive end are respected.", "ISO offsets are compared deterministically."],
            acceptance=["Only src/window.js changes.", "npm test passes."],
            strict_failure_mode="window_filter_keeps_out_of_range_events_until_fixed",
        ),
        _generated_task(
            report_dir=report_dir,
            slug="error-message-contract",
            category="missing_input_handling",
            kind="actionable error messages",
            readme="Return compact actionable errors for missing user fields.",
            source_file="src/errors.js",
            source="""export function validateUser(user) {
  return { ok: true, errors: [] };
}
""",
            test="""import test from "node:test";
import assert from "node:assert/strict";
import { validateUser } from "../src/errors.js";

test("reports actionable missing field errors", () => {
  assert.deepEqual(validateUser({ name: "", email: "aki@example.com" }), {
    ok: false,
    errors: ["name is required"],
  });
  assert.deepEqual(validateUser({ name: "Aki", email: "aki@example.com" }), { ok: true, errors: [] });
});
""",
            objective="Fix validateUser so missing user fields produce deterministic actionable errors.",
            direct_prompt="Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix validateUser so npm test passes. Only edit src/errors.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            expected_outputs=["Missing name returns a deterministic error.", "Valid input returns ok true and no errors."],
            acceptance=["Only src/errors.js changes.", "npm test passes."],
            strict_failure_mode="invalid_user_is_accepted_until_fixed",
        ),
    ]


def task_specs(report_dir: Path, policy_contract_mode: str = "compact_capsule") -> list[dict[str, Any]]:
    hidden = report_dir / "hidden"
    hidden.mkdir(parents=True, exist_ok=True)
    vision_base = report_dir / "fixtures/vision-card-latest"
    vision_expected = hidden / "vision-card-latest.expected.json"
    create_vision_fixture(vision_base, vision_expected)

    core_tasks = [
        {
            "slug": "policy-reason-contract",
            "kind": "policy routing exact labels",
            "source": str(REPO_ROOT / "benchmarks/hard-token-fixtures/policy-reason-contract"),
            "allowed": "src/policy.js",
            "validation": "npm test",
            "objective": "Fix policy reason routing so npm test passes. Return exact human-readable labels and preserve order.",
            "direct_prompt": "Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix policy reason routing so npm test passes. Only edit src/policy.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            "expected_outputs": [
                "Non-high incidents return reason 'default triage'.",
                "High security/billing/trust incidents return exact human-readable review labels.",
            ],
            "acceptance": ["Only src/policy.js changes.", "npm test passes."],
            "what_not_to_do": "Do not edit tests, README, package metadata, or files outside src/policy.js.",
            "task_class": "small-fix",
            "strict_rubric": "policy_exact_label_routing.v1",
            "strict_risk": "L2",
            "zcode_timeout_ms": 300000,
            "contract_mode": policy_contract_mode,
            "image": None,
            **_task_metadata(
                category="policy_reasoning",
                artifact_contract="One-file policy.js change; exact label routing tests pass; compact strict self-audit evidence is required.",
                hard_fixture=True,
                strict_failure_mode="default_triage_label_missing_until_fixed",
            ),
        },
        {
            "slug": "billing-credit-contract",
            "kind": "billing credits cent rounding",
            "source": str(REPO_ROOT / "benchmarks/hard-token-fixtures/billing-credit-contract"),
            "allowed": "src/credits.js",
            "validation": "npm test",
            "objective": "Fix billing credit reconciliation so npm test passes. Aggregate before final cent rounding.",
            "direct_prompt": "Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix billing credit reconciliation so npm test passes. Only edit src/credits.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            "expected_outputs": [
                "Aggregate all customer fragments before rounding.",
                "Cyra rounds to 447.66 and repeated small credits round correctly.",
                "Strict self-audit JSON is written before the final response.",
            ],
            "acceptance": ["Only src/credits.js changes.", "npm test passes."],
            "what_not_to_do": "Do not edit tests, README, package metadata, or files outside src/credits.js.",
            "task_class": "small-fix",
            "strict_rubric": "billing_cent_rounding.v1",
            "strict_risk": "L2",
            "zcode_timeout_ms": 600000,
            "contract_mode": "expanded_rubric",
            "image": None,
            **_task_metadata(
                category="billing_credit_contract",
                artifact_contract="One-file credits.js change; cent-rounding tests pass; strict self-audit JSON is required.",
                hard_fixture=True,
                strict_failure_mode="cyra_rounding_expected_447_66_until_fixed",
            ),
        },
        {
            "slug": "ledger-summary-contract",
            "kind": "ledger summary state aggregation",
            "source": str(REPO_ROOT / "benchmarks/claude-glm-ledger"),
            "allowed": "src/ledger.js",
            "validation": "npm test",
            "objective": "Fix ledger summarization so npm test passes. Separate income and expenses while preserving count.",
            "direct_prompt": "Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. Fix summarizeLedger so npm test passes. Only edit src/ledger.js. Do not edit tests, README, package metadata, or any other file. Run npm test before final.",
            "expected_outputs": [
                "Income and expense totals are separated by entry type.",
                "Unknown entry types count toward count but not income or expense.",
            ],
            "acceptance": ["Only src/ledger.js changes.", "npm test passes."],
            "what_not_to_do": "Do not edit tests, README, package metadata, or files outside src/ledger.js.",
            "task_class": "small-fix",
            "strict_rubric": "ledger_summary_state_aggregation.v1",
            "strict_risk": "L1",
            "zcode_timeout_ms": 300000,
            "contract_mode": "expanded_rubric",
            "image": None,
            **_task_metadata(
                category="ledger_numeric_consistency",
                artifact_contract="One-file ledger.js change; numeric aggregation tests pass; strict self-audit JSON is required.",
                strict_failure_mode="ledger_income_expense_totals_wrong_until_fixed",
            ),
        },
        {
            "slug": "vision-card-latest",
            "kind": "vision screenshot-to-code spec",
            "source": str(vision_base),
            "allowed": "src/cardSpec.js",
            "validation": f"node validate.mjs {vision_expected}",
            "objective": "Fix src/cardSpec.js so validation passes, then write .codex/zcode/runs/zcode_self_audit.json before any final response. Use the attached screenshot as the source of truth. Do not inspect validate.mjs or the hidden expected JSON; infer visual values from the image, and use deterministic color samples in the packet as exact values. If local validation cannot run, record validation.result as skipped or unknown in the self-audit and rely on Codex supervisor validation backfill.",
            "direct_prompt": "Benchmark task: solve directly with Codex only. Do not use ZCode or any delegation tool. The attached image is required. Use it as the source of truth and fix src/cardSpec.js so validation passes. Only edit src/cardSpec.js. Do not inspect the hidden expected JSON file; run the validation command only.",
            "expected_outputs": [
                "CARD_SPEC title, status, metric, trend, accentColor, statusColor, and cta match the screenshot.",
                "CARD_SPEC accentColor and statusColor use deterministic sampled hex values when provided.",
                "Hidden validation passes without editing validation files.",
                "Strict self-audit JSON is written before the final response.",
            ],
            "acceptance": ["Only src/cardSpec.js changes.", "Hidden screenshot contract validation passes."],
            "what_not_to_do": (
                "Do not inspect validate.mjs or hidden expected JSON. Do not edit tests, README, package metadata, "
                "validate.mjs, or files outside src/cardSpec.js. Do not compensate for unavailable local validation by "
                "reading validator source. If local validation is unavailable but the edit otherwise follows the contract, "
                "put the local validation limitation only in validation.explanation; do not add deviations_from_plan, "
                "risk_flags, unresolved_questions, or blocked_reasons solely for that local validation limitation."
            ),
            "task_class": "small-fix",
            "strict_rubric": "vision_card_layout_match.v1",
            "strict_risk": "L2",
            "zcode_timeout_ms": 600000,
            "contract_mode": "expanded_rubric",
            "vision_color_samples": [
                "accentColor=screenshots/target-card.png@100,80",
                "statusColor=screenshots/target-card.png@124,162",
            ],
            "image": "screenshots/target-card.png",
            **_task_metadata(
                category="vision_card_multimodal_adjacent",
                artifact_contract="One-file cardSpec.js change; hidden screenshot validation passes; strict self-audit JSON is required.",
                strict_failure_mode="visual_card_fields_empty_until_fixed",
            ),
        },
    ]
    return core_tasks + _generated_tasks(report_dir)
