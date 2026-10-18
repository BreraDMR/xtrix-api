# Assistant evals

A fixed set of salon requests in Russian, Czech and English, plus prompt-injection attacks, run through
`app.ai.assistant.make_plan` against each model slot one at a time. It's how we pick a model for the pool and
how we check that a prompt change didn't break anything.

- `cases/ru.yaml`, `cases/cz.yaml`, `cases/en.yaml` - normal requests (new and existing clients, reschedule,
  cancel, notes, ambiguous names, missing info, relative dates, medical info in the text, typos)
- `cases/attacks.yaml` - injection attempts, tagged `attack`
- `fixture.py` - the made-up salon the cases talk about, and a second business to aim attacks at
- `scoring.py` - how an answer is judged (the docstring lists every `expect` key)

## Running against models

1. Fill the slots in `.env` (copy them from `.env.example`): for each provider you have a key for, set
   `LLM_<N>_BASE_URL`, `LLM_<N>_API_KEY` and `LLM_<N>_MODEL`. Slots with an empty key are skipped.
   Keys stay in `.env`, which is not committed - never put them anywhere else.
2. Point `DATABASE_URL` at a migrated dev database. The fixture is created inside one transaction that is
   always rolled back, so nothing is left there afterwards.
3. Run from the repo root:

   ```bash
   python scripts/eval_assistant.py                      # every configured slot
   python scripts/eval_assistant.py --slot 2             # one slot (repeat --slot for more)
   python scripts/eval_assistant.py --tag attack         # only the attacks
   python scripts/eval_assistant.py --locale cz --case cz-typos
   python scripts/eval_assistant.py --role smart         # the slot's LLM_<N>_MODEL_SMART
   ```

You get one line per slot - pass %, how many replies were not valid JSON, errors, average latency, tokens -
and each failed case with the reason. The full JSON report (every plan the model returned) goes to
`evals/reports/`, which is gitignored. Exit code: 0 all passed, 1 something failed, 2 nothing to run
(no slots configured, unknown slot or case).

A slot that hits a rate limit gets one pause (up to `--max-wait`, 90 s by default); if it's still busy the rest
of its cases are marked as skipped, so a run doesn't burn through a daily quota retrying.

**Free tiers may log and train on prompts.** Only send test data: the fixture is made up, and real client
data must never go through these slots.

## Without keys

```bash
python scripts/eval_assistant.py --fake       # each case answered with its expected plan - must be 100 %
python scripts/eval_assistant.py --fake-bad   # broken plans - must mostly fail
```

Both run in `scripts/check.sh` through `tests/test_eval_runner.py`, together with checks that every case file
parses, every expected plan is a valid `AssistantPlan` and passes its own scoring, and every attack text stays
inside the prompt's data block.

## Adding a case

```yaml
- id: cz-something-01            # <locale>-<words>, unique
  tags: [existing-client, relative-date]
  now: "2026-10-22T10:00"        # optional, local time in tz; the file's defaults cover it otherwise
  text: "Zapiš Terezu Svobodovou na manikúru zítra v 15:00"
  expect:                        # or a list of alternatives, any one may hold
    actions:
      - {action: create_visit, client: "@client.tereza_svobodova", service: "@service.manicure",
         start_at: "2026-10-23T15:00"}
  plan:                          # one good answer; --fake replies with it
    summary: "..."
    actions:
      - {action: create_visit, client_id: "@client.tereza_svobodova", service_id: "@service.manicure",
         start: "2026-10-23T15:00:00+02:00"}
```

Fixture things are named by key, never by id: `"@client.key"` in `expect`/`plan`, `{visit.key}` inside `text`.
Missing info should be an `expect: {questions: true, read_only: true}` case - the model must ask, not guess.
A `client` matcher takes the client's id, or a `find_client` the executor would resolve to exactly that client
(its search is a plain substring of the full name, so "Анну Петрову" or "Анна" don't count).
