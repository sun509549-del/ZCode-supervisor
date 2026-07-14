# Billing Credit Contract Fixture

## Task

Fix `src/credits.js` so `npm test` passes.

## Contract

- Aggregate all credit fragments for each customer before rounding.
- Return dollar amounts rounded to the nearest cent.
- Do not floor intermediate credit fragments.
- Keep customer names stable.
- Do not edit tests.

The key regression is Cyra: the exact total is `447.655`, so the accepted final
amount is `447.66`.

## Validation

```bash
npm test
```
