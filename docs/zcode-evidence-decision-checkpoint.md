# ZCode Evidence Decision Checkpoint

This checkpoint summarizes the recorded ZCode/Codex worker measurements. It is
a decision document, not a new experiment.

## Decision Questions

1. Did we prove ZCode can implement tasks under Codex supervision?
   Yes, technically. The ZCode-required 20-task run completed with
   `task_count=20`, `strict_green_count=18`, `route_used=zcode_cli`, and
   `codex_fallback=0`. That proves bounded ZCode delegation can produce accepted
   implementations under Codex supervision for this benchmark shape.

2. Did we prove ZCode reduces Codex worker tokens?
   No. The same-packet Codex worker baseline used fewer worker tokens on this
   benchmark: `1,885,377` Codex worker tokens versus `9,729,458` ZCode worker
   tokens. ZCode used `7,844,081` more worker tokens, about `5.16x` the Codex
   worker total.

3. Did we prove total workflow savings?
   No. Codex orchestration usage is unavailable, so total workflow savings are
   not claimable. Unavailable orchestration usage must not be treated as zero.

4. What is the current best interpretation?
   ZCode delegation works technically, but this benchmark does not justify
   making ZCode the default implementation worker. ZCode had a lower strict
   green count than the Codex worker baseline and used more worker tokens. The
   strongest honest claim is that ZCode is a viable optional/experimental route,
   not a proven savings route.

5. Should we continue ZCode route, pause it, or keep it as optional fallback?
   Keep ZCode as an optional or experimental route. Do not make it the default
   implementation worker yet. The next useful work is either to measure Codex
   orchestration tokens to finish total accounting, or to pause ZCode
   optimization and use the operational gateway/Codex worker for practical work.

6. What exact evidence is still missing?
   Codex orchestration token usage is still missing, including compatible usage
   records for planning, packet generation, validation, repair decisions, final
   audit, and final reporting. Without that, total workflow cost and savings
   remain blocked.

## Proven Facts

- The measured ZCode-required 20-task run recorded:
  - `task_count=20`
  - `strict_green_count=18`
  - `worker_total_tokens=9,729,458`
  - `route_used=zcode_cli`
  - Codex fallback count was `0`.
- The orchestration accounting record shows:
  - Codex orchestration tokens are unavailable.
  - Total workflow savings are not claimable.
- The Codex worker same-packet baseline recorded:
  - `task_count=20`
  - `strict_green_count=20`
  - `worker_total_tokens=1,885,377`
  - Total workflow savings remain unclaimable because Codex orchestration tokens
    are unavailable.

## Not Yet Proven Claims

- ZCode does not have proven lower worker token usage than Codex on this
  benchmark.
- ZCode does not have proven higher strict-green quality than Codex on this
  benchmark.
- ZCode does not have proven total workflow token savings.
- ZCode should not be treated as the default implementation worker based on
  this benchmark alone.

## Current Comparison

| Route | Task count | Strict green count | Worker total tokens | Worker token result |
| --- | ---: | ---: | ---: | --- |
| ZCode worker | 20 | 18 | 9,729,458 | Higher token use |
| Codex worker | 20 | 20 | 1,885,377 | Lower token use |
| Codex orchestration | unavailable | unavailable | unavailable | Blocks total accounting |
| Total workflow savings | n/a | n/a | n/a | `claimable=false` |

The current blocker for total workflow savings is
`codex_orchestration_usage_unavailable`.

## Practical Recommendation

Do not continue optimizing ZCode as the default worker from this evidence alone.
Do not make it the default implementation worker yet.
Keep it available as an optional or experimental route when its operational
advantages matter, but prefer the operational gateway/Codex worker for practical
work unless a task has a specific reason to use ZCode.

This recommendation can change if future evidence shows either lower total
workflow cost or a strong non-token operational advantage that is worth the
extra worker tokens.

## Next Possible Actions

- Measure Codex orchestration tokens so the total accounting can be completed.
- Pause ZCode optimization and use the operational gateway/Codex worker for
  practical work.
- If ZCode remains experimental, run only narrowly approved follow-up
  measurements with compatible claim families and explicit orchestration
  accounting.

## What Not To Claim

- Do not claim total workflow savings.
- Do not claim unavailable Codex orchestration usage is zero.
- Do not claim ZCode used fewer worker tokens than Codex on this benchmark.
- Do not claim ZCode had better strict-green results than Codex on this
  benchmark.
- Do not mix worker-token claims with total-workflow claims.
