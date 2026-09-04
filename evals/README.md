# evals/

The gate that decides whether a change may ship.

```bash
uv run arc-evals            # exit 0 = passed, exit 1 = blocked
uv run arc-evals --no-gate  # measure only, never blocks
```

**No API key. No network. No accounts.** Every suite here is deterministic and
free, because CI has no secrets and a gate nobody can run is not a gate. Paid
judgements (does the prose survive sentence-level grounding? does a revision
touch only what it claimed to?) belong to a release checklist run by a human,
not to this file.

## Layout

- `baselines.json` — committed floors and ceilings. Every asymmetric bound
  carries a `why`. Changing a number here is a decision, not a fix.
- `reports/` — committed results, one per release. `out/` is gitignored;
  reports are not.

## Tiers

| Tier | Meaning | What is here now |
| --- | --- | --- |
| **gated** | Fails the build | `mentions.*` — 3 metrics |
| **report-only** | Recorded, does not block. Joins the gate only after the bar has survived several cycles unchanged | none yet |
| **decision probe** | Run to choose between options, never a gate | `arc benchmark` (multi-provider), not wired here |

A new measurement does not go straight into the gate. If it is wrong on its
first bad day the build breaks, somebody lowers the bar to unblock themselves,
and the gate becomes decoration. It earns the gate by being boring first.

## What the gate does not cover

This is the important section. The suites here catch a narrow band of failure.

- **Retrieval.** When the chat picks the wrong report card to answer from, every
  downstream check passes: the sentences are grounded, the numbers are
  registered, the gate is happy — and the answer is about the wrong company.
  Nothing here measures that yet. It is the largest known hole.
- **Whether the prose is any good.** G0 proves a number is traceable. It cannot
  tell you the paragraph is worth reading. That needs human-labeled comparisons
  against real published notes, and those labels do not exist yet.
- **Production precision of the name classifier.** The name list comes from the
  repository corpus (~1,100 listed names), not the full DART registry (~2,800).
  More names produce more false positives. The number below is a **floor**, not
  an estimate.
- **Anything that costs money.** By construction.

## Current suites

### `mentions` — the stock-name classifier

`ingest/telegram_mentions.py` finds company names in free text. False positives
are the hard part: 「대상」 is a food company (001680) and also the ordinary word
for "target"; 「나노」, 「레이」, 「하림」, 「만도」 are all listed companies and all
common words.

Three defenses — longest-match first, boundary checking, and an evidence
requirement for names that collide with common words. **When those loosen,
nothing else notices.** The text is well-formed, no numbers appear, so G0 passes
it. That is why this is an eval and not a test.

| Metric | Bound | Why that bound |
| --- | --- | --- |
| `recall` | ≥ 0.995 | 14 of 7,077 labeled titles are missed and all 14 are reports *about* the common-word companies themselves. Known cost of the list. |
| `extras_per_title` | ≤ 0.01 | Companies matched beyond the labeled one. **Not precision** — a title naming two companies is correct. A trend tripwire. |
| `prose_false_positives` | **= 0** | Asymmetric on purpose. Without the common-word list the same corpus yields 54. One hit here puts a stock on the sentiment screen that nobody mentioned, and the analyst acts on a signal that does not exist. |

Labels come free: report titles carry their stock code, so stripping the code
and asking the classifier to find the company by name alone gives ground truth
with no annotation.

`tests/test_telegram_parse.py::TestMeasuredExtraction` pins the same corpus at
**exact equality** (`missed == 14`). Both belong here. The test says "right now
it is precisely this"; the eval says "below this we do not ship" and survives
honest corpus growth.
