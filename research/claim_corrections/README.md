# claim_corrections

Corrections to claims this project had already published, each recording *why*
the wording was wrong rather than only what it now says.

These are not experiments. They collect no data and test no hypothesis, which is
why they live here rather than as `research/<experiment>/` directories — that
namespace means "an experiment lives here," and two documentation corrections
filed alongside real sweeps made the index harder to read, not richer.

| file | what was corrected |
|---|---|
| [`layer3-query-pinning.md`](layer3-query-pinning.md) | The README listed "does `loo_agreement` survive non-pinned queries?" as open after PR #7 had answered it. Narrowed to the axis still genuinely open — a different query *distribution*, not merely a non-pinned one. |
| [`qdrant-graph-quality-withdrawal.md`](qdrant-graph-quality-withdrawal.md) | The cross-system spec listed "Qdrant diverges in data completeness but not graph quality" under *establishes*. PR #11 showed that `index_recall` comparison ran over a corpus that was un-indexed for 60–84% of the measurement window, so it measured exact scans rather than a graph. Withdrawn in **both** directions. |

Both files keep their review addenda, and those are the reason this directory is
worth preserving at all. The corrections themselves are short; what is not
recorded anywhere else is the pattern the review rounds exposed — **every
overstatement found ran in the same direction, toward a tidier and more
confident claim than the evidence supported, including one that appeared inside
a correction of an over-claim, in the clause asserting that something had been
checked rather than assumed.**

That is a standing bias in how results get written up, not a set of one-off
slips, and in both cases the only thing that caught it was re-reading the raw
per-seed data instead of the prose describing it.
