# Agent pipeline: researcher → implementer → reviewer

Three roles, three separate sessions, coordinated entirely through GitHub state (issues, labels, PRs, comments) — no direct messaging between them. This makes the process auditable after the fact and means the three don't have to run at the same time. This document assumes `research/GIT_WORKFLOW.md` — read that first; this is the GitHub-specific layer on top of it, not a replacement for it.

The human (you) remains the merge gate throughout. No session in this pipeline merges a PR.

## Roles

### Researcher

Finds the next real gap, writes it up as a pre-registered spec, files it as an issue. Does not implement anything.

**Where to look for the next question:**
1. The top-level `README.md`'s **Open research questions / next experiments** section — the standing, curated list.
2. `research/README.md`'s experiment index — what's already open, in progress, or closed.
3. `research/DECISION_LOG.md` — so a question already investigated and ruled out doesn't get re-proposed as new.
4. `research/RELATED_WORK.md`, if the question might already be answered by existing literature rather than needing a new experiment.

**What makes an issue correctly scoped:** one answerable question. "Does this generalize to Qdrant?" is scoped; "does this generalize?" (to everything, unboundedly) is not — that's the whole research thesis, not a branch. Similarly, a question with an obvious, already-known answer isn't worth a branch — that's what makes step 1-3 above load-bearing, not a formality.

**Filing:** use the `research-question.yml` issue form (New Issue → Research question). Every field maps directly onto `research/SPEC_TEMPLATE.md` — that's deliberate, so the issue and the spec never diverge into two documents saying different things. Label with the correct `type:*` and leave `stage:proposed` (the template sets this automatically).

**Do not:** implement anything, open a branch, or write code. If you're tempted to prototype "just to check feasibility" first, that's a signal the issue needs a feasibility/confound note in its own fields, not code.

### Implementer

Claims one open issue, builds it on a branch, opens a PR.

**Claiming:** find an issue labeled `stage:proposed`, self-assign it, swap the label to `stage:claimed`. This is the whole anti-duplication mechanism — if it's not labeled `proposed`, someone else already has it or it's not ready.

```bash
gh issue list --repo shlokkvaishnav/Replica-Recall-Divergence --label stage:proposed
gh issue edit <N> --add-assignee @me --add-label stage:claimed --remove-label stage:proposed
```

**Branch naming:** follow `research/GIT_WORKFLOW.md`'s existing prefixes exactly (`research/<topic>`, `experiment/<name>`, `analysis/<name>`, `method/<name>`, `reproduction/<target>`) — do not append the issue number to the branch name, that's noise. The link to the issue lives in two places instead: the first commit's `SPEC.md` (copy the issue body in verbatim, don't paraphrase it), and the PR's `Closes #<N>`.

**Implementing:** per `GIT_WORKFLOW.md` — spec first (already have it, from the issue), then implementation, then validation, then the actual experiment. Update `SPEC.md`'s Results/Interpretation/Decision sections once there's something to put there — the issue and `SPEC.md` are the same content at every stage, not just at the start.

**Opening the PR:** `.github/PULL_REQUEST_TEMPLATE.md` auto-populates — fill in every section honestly, including a self-assessed MERGE/ARCHIVE/REVISE/ABANDON/REPRODUCE decision. Label the PR `stage:in-review`.

**Do not:** merge, invent scope beyond what the issue specified (if the work reveals a genuinely different, better question, that's a new issue, not silent scope creep on this one), or skip filling in a section of the template because the answer is inconvenient (an honest "confounds remain: X" is the point, not a failure).

### Reviewer

Reviews the PR against `research/GIT_WORKFLOW.md`'s actual merge criteria — scientific relevance, correctness, experimental validity, reproducibility, documentation, interpretation, research integrity, integration, evidence. Comments, doesn't push code, doesn't merge.

**Process:**
1. Read the issue, the PR template's filled-in answers, and the diff.
2. Check the mini-peer-review questions were actually answered, not just present — "what does this NOT establish" filled in with "N/A" is a red flag, not a passing answer.
3. Check `GIT_WORKFLOW.md`'s "when not to merge" list explicitly — irreproducible, uncontrolled confound, multiple variables changed at once, cherry-picked runs, unsupported claims.
4. Post a PR comment with your own MERGE / ARCHIVE / REVISE / ABANDON / REPRODUCE recommendation and why, using the same nine dimensions `GIT_WORKFLOW.md` lists. If REVISE or CHANGES REQUESTED, be specific enough that the implementer session (which won't remember this conversation) can act on it standalone.
5. Label accordingly: `stage:changes-requested` if more work is needed, `stage:approved-pending-merge` if you'd merge it.

**Do not:** merge (that's the user's call), rewrite the implementer's code, or approve because the numbers look good without checking whether the numbers answer the actual question.

## What "discuss and refine" looks like without live messaging

All of it happens as PR comments and re-pushes, since the three sessions don't talk to each other directly:

1. Reviewer posts specific, actionable comments + `stage:changes-requested`.
2. A later implementer session (could be the same branch resumed, or literally a fresh session pointed at the same PR) reads the PR comments, addresses them, pushes, and posts a reply comment summarizing what changed and why.
3. Reviewer re-reviews only what changed, not the whole PR from scratch, and updates the label.
4. Repeat until `stage:approved-pending-merge` (or the reviewer's recommendation becomes ARCHIVE/ABANDON, in which case the user decides what happens to the branch per `GIT_WORKFLOW.md` — preserved, not deleted, either way).

The user merges when they choose to act on `stage:approved-pending-merge` — this can happen immediately or much later; the label is a durable state, not a live signal.

## Loop-readiness (design note, not built yet)

The stage labels are ordered specifically so a future automated loop (`/loop`, or a scheduled cron session) could run one role repeatedly without any redesign:

- A **researcher loop** would run periodically, check whether any `stage:proposed` issues already exist (skip if so — don't flood the queue), and file at most one new one per run if the standing question list has room.
- An **implementer loop** would poll `gh issue list --label stage:proposed`, claim the oldest one, and run to a PR.
- A **reviewer loop** would poll `gh pr list --label stage:in-review`, review, and update state.

None of this is wired up now — the pipeline is manually triggered per stage. This section exists so building that later doesn't require renaming labels or restructuring the templates.
