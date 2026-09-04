# The role-rotating research pipeline

This describes how `research/GIT_WORKFLOW.md`'s process runs in practice when
**one** Claude Code session carries it out, picking up whichever of three
roles — researcher, implementer, reviewer — GitHub state says is next, and
coordinating only through that state (issues, branches, PRs, labels), never
through in-session memory of "what I decided as researcher an hour ago."
Nothing here changes `GIT_WORKFLOW.md`'s actual policy; it says how that
policy gets executed by one party that plays three parts in rotation.

This document originally described three separate sessions running these
roles concurrently. That model is retired — see "Why one session now"
below — but the role definitions and the loop-readiness decision logic were
written to be role-agnostic from the start, so nothing about them changed.

## Why GitHub state instead of live coordination

Every handoff between roles has to be a durable, auditable artifact — the
same reason `DECISION_LOG.md` exists ("so the project doesn't depend on
anyone's memory of it"). This matters even more now that one session plays
every role: without a hard rule to re-derive each role's state from GitHub
rather than from what the session remembers writing as a different role
minutes earlier, the roles quietly merge into one voice and the separation
of concerns (proposer / builder / independent checker) stops meaning
anything. Issues and PR comments are the coordination layer, full stop —
not context carried over in the conversation.

A consequence worth being explicit about: this also means no role needs the
others to have "just run." The pipeline degrades gracefully to "whichever
role GitHub state says is next," which is what makes the single-session
rotation in this document possible, and what made the old three-session
version possible before it.

## Why one session now

Three independent sessions were the safest way to get real role separation:
a researcher session literally could not see the implementer's reasoning,
so its issue-filing couldn't be biased by knowing how the fix would land.
Running one session through all three roles trades that hard separation for
operational simplicity — one thing to schedule, one context window, one
place to look. The role instructions below are unchanged; what changes is
**how the session decides which role to play this tick**, and an explicit
rule against a role trusting its own prior-role reasoning (see "Guarding
role separation within one session").

## Roles

### Researcher

Finds the next real gap, writes it up as a pre-registered spec, files it as
an issue. Does not implement anything.

**Where to look for the next question:**
1. The top-level `README.md`'s **Open research questions / next
   experiments** section — the standing, curated list.
2. `research/README.md`'s experiment index — what's already open, in
   progress, or closed.
3. `research/DECISION_LOG.md` — so a question already investigated and
   ruled out doesn't get re-proposed as new.
4. `research/RELATED_WORK.md`, if the question might already be answered by
   existing literature rather than needing a new experiment.

**What makes an issue correctly scoped:** one answerable question. "Does
this generalize to Qdrant?" is scoped; "does this generalize?" (to
everything, unboundedly) is not — that's the whole research thesis, not a
branch. Similarly, a question with an obvious, already-known answer isn't
worth a branch — that's what makes step 1-3 above load-bearing, not a
formality.

**Filing:** use the `research-question.yml` issue form (New Issue →
Research question). Every field maps directly onto
`research/SPEC_TEMPLATE.md` — that's deliberate, so the issue and the spec
never diverge into two documents saying different things. Label with the
correct `type:*` and leave `stage:proposed` (the template sets this
automatically).

**Do not:** implement anything, open a branch, or write code. If you're
tempted to prototype "just to check feasibility" first, that's a signal the
issue needs a feasibility/confound note in its own fields, not code.

### Implementer

Claims one open issue, builds it on a branch, opens a PR.

**Claiming:** find an issue labeled `stage:proposed`, self-assign it, swap
the label to `stage:claimed`. This is the whole anti-duplication mechanism —
if it's not labeled `proposed`, someone else already has it or it's not
ready.

```bash
gh issue list --repo shlokkvaishnav/Replica-Recall-Divergence --label stage:proposed
gh issue edit <N> --add-assignee @me --add-label stage:claimed --remove-label stage:proposed
```

**Branch naming:** follow `research/GIT_WORKFLOW.md`'s existing prefixes
exactly (`research/<topic>`, `experiment/<name>`, `analysis/<name>`,
`method/<name>`, `reproduction/<target>`) — do not append the issue number
to the branch name, that's noise. The link to the issue lives in two places
instead: the first commit's `SPEC.md` (copy the issue body in verbatim,
don't paraphrase it), and the PR's `Closes #<N>`.

**Implementing:** per `GIT_WORKFLOW.md` — spec first (already have it, from
the issue), then implementation, then validation, then the actual
experiment. Update `SPEC.md`'s Results/Interpretation/Decision sections once
there's something to put there — the issue and `SPEC.md` are the same
content at every stage, not just at the start.

**If the branch adds a `research/<name>/` directory,** add its row to
`research/README.md`'s experiment index in the same PR — investigation,
location, type, status. `research/check_index.py` enforces this in CI and runs
in seconds locally. It checks only that the directory is *named*: whether the
row's status is still true is a reviewer's job, since a row reading "Not
started" for finished work passes the check and was a real defect (#15).

**Opening the PR:** `.github/PULL_REQUEST_TEMPLATE.md` auto-populates — fill
in every section honestly, including a self-assessed
MERGE/ARCHIVE/REVISE/ABANDON/REPRODUCE decision. Label the PR
`stage:in-review`, and swap the linked issue to `stage:in-review` too — the
role-selection query below reads the PR's label, not the issue's, but
keeping both current is what lets `gh issue list` still describe reality.

**Do not:** merge, invent scope beyond what the issue specified (if the work
reveals a genuinely different, better question, that's a new issue, not
silent scope creep on this one), or skip filling in a section of the
template because the answer is inconvenient (an honest "confounds remain:
X" is the point, not a failure).

### Reviewer

Reviews the PR against `research/GIT_WORKFLOW.md`'s actual merge criteria —
scientific relevance, correctness, experimental validity, reproducibility,
documentation, interpretation, research integrity, integration, evidence.
Comments, doesn't push code, doesn't merge.

**Process:**
0. Count the review rounds already on this PR (its comment history is the
   record; don't rely on remembering them). At **three**, do not open a
   fourth: post the findings, leave the PR `stage:changes-requested`, and say
   explicitly that the round cap was reached and a human should look. Do
   **not** approve at the cap — see "Bounding the review/revision cycle" for
   why that direction is the wrong one. Rounds that each find a real,
   independently-verifiable defect are the cycle working, not looping; a
   round that only verifies a previous round's fix and reaches a decision is
   not a findings round and does not count against the cap.
1. **Audit the diff mechanically before reading any prose.** Run

   ```bash
   H=$(gh pr view <N> --json headRefOid -q .headRefOid)     # the head you are reviewing
   git fetch origin && git diff --name-status origin/main...$H
   git diff --name-status origin/main...$H | grep -E '^(D|R)'          # deletions, renames
   git diff --name-only  origin/main...$H | grep -E '(^|/)results?(_|/)' # result data touched
   ```

   and compare the file list against the PR body's "Did the implementation
   introduce any unintended changes elsewhere?" answer. Any file the body
   does not account for, any deletion, and any change under a `results*/`
   directory is a finding before a word of the writeup has been read. This
   step exists because PR #27's body said "One file" while its diff deleted
   three tracked result files, and the two merges that had put those files
   on `main` (#18, #24) were never asked the question. A body that
   misdescribes its own diff has failed research integrity regardless of
   how good the code is; a script can catch that where a reader will not.
   (`gh pr diff` has no `--name-status`; the first version of this step
   said it did, and failed the first time it was run, on #29.)
2. Read the issue, the PR template's filled-in answers, and the diff, as if
   seeing them for the first time — see "Guarding role separation," below,
   for why that matters more now than it did with three sessions.
3. Check the mini-peer-review questions were actually answered, not just
   present — "what does this NOT establish" filled in with "N/A" is a red
   flag, not a passing answer.
4. Check `GIT_WORKFLOW.md`'s "when not to merge" list explicitly —
   irreproducible, uncontrolled confound, multiple variables changed at
   once, cherry-picked runs, unsupported claims.
5. Post a PR comment with your own MERGE / ARCHIVE / REVISE / ABANDON /
   REPRODUCE recommendation and why, using the same nine dimensions
   `GIT_WORKFLOW.md` lists. If REVISE or CHANGES REQUESTED, be specific
   enough that the implementer role (which, per "Guarding role separation,"
   is not allowed to lean on what it remembers deciding) can act on it from
   the comment alone. **A MERGE decision must name the head SHA it
   reviewed** (from step 1) — that SHA is what query 0 below checks against
   `main` after the human merges. PR #18 merged a head other than the one
   reviewed and lost two review fixes (re-landed in #19); a decision that
   does not say which commit it approved cannot be verified afterwards.
6. Label accordingly: `stage:changes-requested` if more work is needed,
   `stage:approved-pending-merge` if you'd merge it — label **both** the PR
   and its linked issue.
7. If ARCHIVE / ABANDON / REPRODUCE — **or if the decision says to stop a
   line of work** — label accordingly (PR and issue both), and add an entry
   to `research/DECISION_LOG.md` — the reviewer role is the one that has
   just read the full evidence, so this is the right point to record why,
   not something to leave for later. A decision that lives only in a PR
   comment is not recorded: PR #25's round 2 said "STOP this line of work"
   and nothing in the log said so until 2026-09-03.

**Do not:** merge (that's the user's call), rewrite the implementer's code,
or approve because the numbers look good without checking whether the
numbers answer the actual question.

## Role selection (chain until idle, not one-role-per-tick)

Run these `gh` queries in order and play the **first** role whose condition
is true. A single loop iteration **chains**: the moment a role's one unit of
work finishes (a PR reviewed, an issue implemented, an issue filed),
immediately re-run this same query list from the top and play whatever
fires next — do not wait for the next scheduled wakeup. Only stop once
condition 5 (idle) is reached. This priority order clears the pipeline
downstream-first, so work already in flight finishes before new work
starts:

0. **`gh pr list --state merged --label stage:approved-pending-merge`**
   returns anything → **post-merge verification**, before any role plays.
   For each such PR, take the head SHA named in the reviewer's MERGE comment
   and check `git merge-base --is-ancestor <sha> origin/main`. If it holds:
   relabel PR and linked issue `stage:merged`, and update the affected row
   in `research/README.md`'s experiment index (a row still reading "In
   progress" after its PR merged is the defect #15 found and #25
   re-created). If it does not hold, the human merged a different head than
   was reviewed — or merged a stacked PR into its stack base instead of
   `main`: comment on the PR naming both SHAs, label it
   `stage:changes-requested`, and re-land as #19 did for #18. Fired for
   real on 2026-09-04: #31 and #33 were merged 13 and 32 seconds after
   #29, into `method/qdrant-index-gate` and
   `experiment/qdrant-gated-index-recall`; `main` got #29 only. GitHub
   retargets a stacked PR to the next base **only when the merged base
   branch is deleted**. So the instruction to the human for a stack is:
   merge bottom-up, **tick "delete branch" on each merge**, and wait for
   the next PR's base to read `main` before merging it — or the reviewer
   re-lands the whole stack as one PR from its top (#34). This query exists because the pipeline
   otherwise has no check that what reached `main` is what was reviewed,
   which #19 pointed out and which stayed unimplemented for a day.
1. **`gh pr list --label stage:in-review`** returns anything → play
   **Reviewer** on the oldest PR.
2. Else, **`gh pr list --label stage:changes-requested`** returns anything
   → play **Implementer** on the oldest such PR: act on the review comment
   (from the comment alone — see "Guarding role separation"), push the
   fixes, reply saying what was accepted and what was not, and relabel the
   PR and its linked issue back to `stage:in-review`. Revision work is
   downstream of new work in exactly the same sense a review is, so it sits
   above queries 3 and 4, not below them.
2b. Else, **`gh issue list --label stage:claimed --assignee @me`** returns
   anything → play **Implementer** on it: that is work this account claimed
   and has not yet turned into a PR — a sweep still running, a spec whose
   runs have not started. Continue it; do not file new work on top of it.
   Added 2026-09-03 when a loop wakeup mid-way through #28's sweep found
   queries 0–3 empty and would have fired query 4 (Researcher) with an
   implementation in flight — the "downstream-first" order was silently
   skipping the most downstream thing there was. If the claimed issue is
   stale (no branch, no commits, no running process for it), un-assign it
   and relabel `stage:proposed` so query 3 picks it up honestly.
2c. Else, if **`gh pr list --label stage:approved-pending-merge`** returns
   **more than one** PR → idle for this tick (log it as condition 5, with the
   PR numbers). Merge is manual, so every branch started while PRs wait has
   to be stacked on the newest of them — #31 on #29, #33 on #31 happened in
   one night — and each human merge then retargets the whole stack. Two deep
   is the most that has been shown to retarget cleanly; the queries below
   resume the moment the human merges. This does not stop query 0, 1, 2 or
   2b: reviews and revisions of work already in flight continue.
3. Else, **`gh issue list --label stage:proposed --json number,assignees -q '.[] | select(.assignees | length == 0) | .number'`** returns
   anything → play **Implementer** on the oldest unclaimed issue. (An
   earlier version used `--assignee ""`, which in current `gh` returns
   nothing rather than "unassigned" — the query silently never fired, found
   on #30 when the filed issue did not show up.)
4. Else, **`gh issue list --label stage:proposed`**'s count is below
   threshold N (default N = 3 — enough runway that the implementer role
   never stalls waiting on the researcher role's next tick, small enough
   that stale proposals don't pile up unimplemented) → play **Researcher**
   and file exactly one issue.
5. Else, idle: log which condition was checked and why nothing fired, then
   stop this iteration.

Follow the matching role's numbered instructions above exactly — the role
selection only decides *which* numbered list to execute, it does not change
what's in them.

**Bounding the review/revision cycle.** Queries 1 and 2 hand a PR back and
forth between the same session's two roles, so nothing in the loop itself
stops that pair from cycling. The cap that bounds it is **step 0 of the
Reviewer's instructions**, not a rule of role selection — role selection
only picks which numbered list to execute, it does not add to what is in
them, and a rule filed outside the list of the role that must obey it is a
rule that does not run. The rationale lives here, where the risk it guards
is explained: the failure mode is two roles converging on each other, so
the cap escalates to a human rather than approving — auto-approving at the
cap would resolve that convergence in the one direction the manual-merge
gate exists to prevent, which is worse than a PR waiting.

**Chaining stops being safe to auto-continue past a MERGE-decided PR.**
Reviewer step 5 labels a MERGE-approved PR `stage:approved-pending-merge`,
which removes it from query 1 above — the chain correctly moves on to
Implementer/Researcher instead of looping on the same PR. But nothing
downstream of that label fires again until a human actually merges (see
below). That's intentional, not a gap: it's what keeps "chain until idle"
from quietly becoming "chain through a merge with no human ever looking."

## Why merge stays manual

Every other step in this pipeline collapsed into one auto-chaining loop
because each one is independently checkable — CI is green or it isn't, the
nine merge criteria are met or they're written up as not met, a spec was
copied verbatim or it wasn't. Merge is different: it's the one step whose
entire purpose is a check *external* to the process that produced the thing
being checked. `GIT_WORKFLOW.md` already halves this problem by requiring
the researcher/implementer/reviewer split in the first place, but this
pipeline runs all three roles from one account now, and GitHub's own
`gh pr review --approve` refuses a PR opened by the same account
("Can not approve your own pull request") — a mechanical reminder that
reviewer and implementer share one identity here. A human clicking merge is
the last checkpoint that isn't also this same account grading its own
homework. Auto-merging on a MERGE decision would remove it — and because
this loop is meant to run near-continuously and unattended, a wrong MERGE
wouldn't wait for anyone to notice before it compounded into `main`, which
this project's own README calls the validated research state. Keeping merge
manual is a deliberate, revisited decision, not an oversight left over from
the three-session design, made explicitly so the loop can run everywhere
else without that risk.

## Guarding role separation within one session

The three roles used to be enforced by being different sessions that
literally could not see each other's reasoning. One session rotating roles
has to enforce that separation by rule instead of by architecture:

- **Re-derive state from GitHub, not from this conversation's memory.**
  When playing Reviewer, read the PR, the linked issue, and `SPEC.md` on the
  branch as if seeing them for the first time — do not reuse
  implementer-role reasoning from earlier in the same session as a
  shortcut. If the implementer-role work happened this session, that is
  exactly the case most likely to produce a rubber-stamp review, because
  the reviewer role already "knows" the conclusion is right.
- **Never review or approve your own PR**, regardless of which session
  wrote it. If the PR under review was opened by this same session's
  implementer-role turn, the review still has to independently re-derive
  MERGE / ARCHIVE / REVISE / ABANDON / REPRODUCE from the nine merge
  criteria — an approval that amounts to "I already decided this was good
  when I wrote it" is not a review.
- **Never let the researcher role scope an issue around a solution the
  session already has in mind.** The researcher role's job is to find a
  real gap per `README.md` / `DECISION_LOG.md`, not to pre-stage easy work
  for the implementer role it's about to become.
- If a tick's role selection would have this session review its own
  immediately-prior work (Implementer this tick, and the next tick's
  Reviewer query would pick up that same PR), that's expected and fine —
  the rules above are what keep it honest, not avoiding the sequence.

## Running this as a loop

Role selection above is exactly what a scheduled loop polls each tick — the
harness's `/loop` skill (dynamic pacing, no fixed interval needed) or a
cron. Nothing about the label taxonomy, templates, or role instructions
needs to change to run this way; only the three separate per-role loops
originally sketched here collapsed into the single decision tree above.

**The loop is on by default.** `.claude/settings.json` registers a
`SessionStart` hook (`.claude/hooks/pipeline-autostart.sh`) that tells every
Claude Code session opened in this repository to start `/loop` on this
document's role selection on its first turn, unless the user's message says
not to. Before 2026-09-03 the loop only ran when someone typed the command,
and PRs sat at `stage:in-review` until someone remembered — the hook makes
the pipeline the default state of a session rather than an opt-in. Disable
it by removing the `SessionStart` entry.

**Changes to this pipeline itself go straight to `main`.** The owner decided
on 2026-09-03 that edits to the roles, role selection, templates, and hooks —
the process, not the research — are committed to `main` directly, the way
`GIT_WORKFLOW.md` already treats editorial changes. Research branches keep
the full spec → PR → review → manual-merge path; the merge rule below is
about those.
