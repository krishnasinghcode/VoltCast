# Modular AI-Build Methodology

Reusable process for building any multi-part project with AI coding assistance, where each part may be coded
by a **separate AI session with zero memory of the others.** Paste this doc as context at the start of any
new project to skip re-explaining the process.

## Why this process

AI sessions have limited context and no shared memory across sessions. If one session builds the whole
project, context fills up, quality drops on later parts, and one bug anywhere risks derailing everything. The
fix: split the project into independent parts, each documented well enough that a cold AI session can build it
correctly alone, then integrate the finished parts. This also makes debugging tractable — a broken part is
fixed by re-running its own doc against a fresh session, not by re-deriving the whole system's context.

## The process, in order

1. **Scope the project down first, before writing anything.** List everything the ideal version would need,
   then explicitly cut to what's "good enough" — necessary features only, declare the cuts openly rather than
   silently doing less than promised. Lock the cuts as decisions before moving on.

2. **Split into parts along natural data/ownership boundaries**, not arbitrary file counts. A good part is
   something with a clear single responsibility, a clear input, and a clear output — usually 3-6 parts for a
   normal project. Order the parts by dependency (what must exist before what), not by preference.

3. **Write CONTRACT.md first, before any part's own doc.** This is the single most important file. It contains
   everything every part must agree on: shared data schemas, API/event formats, ports/service names, env vars,
   naming conventions, and any shared formulas/constants. Every part's own doc later embeds a copy of the
   contract sections it needs, so it can be built with zero other files open — but CONTRACT.md is always the
   authority if two parts' embedded copies disagree.

4. **Write one master doc per part.** Each must be fully self-contained (a cold AI with only this file +
   CONTRACT.md should be able to build a complete, working deliverable). Use this fixed structure for every
   part's doc:
   - **Purpose** — one paragraph, what this part is for.
   - **Inner architecture** — folder structure, file list, dependencies/libraries, Dockerfile if relevant.
   - **Inputs** — exact APIs/commands/files/configs this part consumes, with request/response shapes.
   - **Configs** — every tunable value (seeds, thresholds, env vars), with defaults and what each does.
   - **Features & inner working** — the actual logic/algorithms, precise enough to implement without
     guessing (equations, pseudocode, state machines — not vague prose).
   - **Outputs** — exact APIs/data/files this part produces, with shapes.
   - **Its copy of the shared schema** — what this part currently believes the shared contract is, so a
     drift between this doc and CONTRACT.md is detectable instead of silent.
   - **Dependencies** — what this part needs to exist first, and what breaks downstream if this part's
     output shape changes.
   - **How to run and verify standalone** — exact commands, and how to test this part alone before the
     parts it depends on are finished (e.g. a small fixture/fake-input script).
   - **Tests to include** — the minimum real automated tests, named explicitly.

5. **Build parts in parallel**, one AI session per part, each session getting only CONTRACT.md + its own
   part's doc. Parts don't need to be built in dependency order — only *run/integration* order matters, not
   *build* order, as long as each part can be tested against a fixture/fake standing in for its dependencies.

6. **Write INTEGRATION.md last**, after all parts exist (or their docs are finalized). This is the human's
   own reference — not for an AI to build from, but for assembling and debugging the finished parts. Contains:
   - Exact bring-up order and commands.
   - Any known cross-part assumption mismatches caught while writing the docs (write these down the moment
     you notice them — they're the actual valuable output of doing this whole exercise carefully).
   - Full shared infra config (e.g. `docker-compose.yml`, CI workflow) — this glue doesn't belong to any one
     part, it belongs at the integration layer.
   - A final checklist against whatever the original requirements/deliverables were.

7. **When something breaks after integration**, don't re-explain the whole project to a new AI session. Give
   it CONTRACT.md + the one broken part's doc + the actual error, and let it fix that part alone. If the fix
   requires changing the contract, update CONTRACT.md and note in INTEGRATION.md that every other part must be
   re-checked against the new version — don't let the contract silently drift.

## Rules that make this actually work

- **A part's doc is authoritative over its own code, CONTRACT.md is authoritative over every part's doc.**
  Never let a part's builder silently deviate from the contract — if they must, they document it as a
  deviation, they don't just change it.
- **Every shared value (schema field, port, constant, formula) is defined in exactly one place** (CONTRACT.md)
  and referenced, never redefined, by the parts.
- **Every part must be independently runnable and testable** before it's ever combined with the others —
  if a part can only be verified by first building everything else, the split was drawn in the wrong place.
- **Write down cross-part assumption mismatches the moment you notice them while drafting the docs** — this
  is the main value of writing everything out carefully instead of just starting to code; catching these on
  paper is far cheaper than catching them after four separate AI sessions have already written code around a
  wrong assumption.
- **Be honest about scope cuts and known limitations in the docs themselves**, not just in your head — a
  future AI session (or reviewer) needs to know what was deliberately simplified vs. what's a bug.
