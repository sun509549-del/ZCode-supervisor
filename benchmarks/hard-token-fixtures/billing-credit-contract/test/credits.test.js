import assert from "node:assert/strict";
import { test } from "node:test";

import { reconcileCredits } from "../src/credits.js";

test("reconcileCredits rounds final customer totals to cents", () => {
  const result = reconcileCredits([
    { customer: "Cyra", creditDollars: "120.125" },
    { customer: "Cyra", creditDollars: "327.530" },
    { customer: "Ivo", creditDollars: "19.995" },
    { customer: "Ivo", creditDollars: "5.005" }
  ]);

  assert.deepEqual(result, [
    { customer: "Cyra", credit: 447.66 },
    { customer: "Ivo", credit: 25.0 }
  ]);
});

test("reconcileCredits keeps small fractional credits instead of flooring lines", () => {
  const result = reconcileCredits([
    { customer: "Mina", creditDollars: "0.335" },
    { customer: "Mina", creditDollars: "0.335" },
    { customer: "Mina", creditDollars: "0.335" }
  ]);

  assert.deepEqual(result, [{ customer: "Mina", credit: 1.01 }]);
});
