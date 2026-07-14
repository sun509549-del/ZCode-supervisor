# Policy Reason Contract Fixture

## Task

Fix `src/policy.js` so `npm test` passes.

## Contract

- Return exact human-readable reason labels.
- Preserve incident ids and input order.
- Do not collapse labels into implementation tags such as
  `high:security-review`.
- Do not edit tests.

Expected labels include:

- `default triage`
- `security review`
- `billing review`
- `trust and safety review`

## Validation

```bash
npm test
```
