import assert from "node:assert/strict";
import { test } from "node:test";

import { routeIncidents } from "../src/policy.js";

test("routeIncidents emits exact human-readable reason labels", () => {
  const result = routeIncidents([
    { id: "inc-101", severity: "low", category: "general" },
    { id: "inc-102", severity: "high", category: "security" },
    { id: "inc-103", severity: "high", category: "billing" },
    { id: "inc-104", severity: "high", category: "trust" }
  ]);

  assert.deepEqual(result, [
    { id: "inc-101", queue: "triage", reason: "default triage" },
    { id: "inc-102", queue: "review", reason: "security review" },
    { id: "inc-103", queue: "review", reason: "billing review" },
    { id: "inc-104", queue: "review", reason: "trust and safety review" }
  ]);
});

test("routeIncidents preserves input order and routes normal incidents to triage", () => {
  const result = routeIncidents([
    { id: "inc-201", severity: "medium", category: "billing" },
    { id: "inc-202", severity: "low", category: "security" }
  ]);

  assert.deepEqual(result, [
    { id: "inc-201", queue: "triage", reason: "default triage" },
    { id: "inc-202", queue: "triage", reason: "default triage" }
  ]);
});
