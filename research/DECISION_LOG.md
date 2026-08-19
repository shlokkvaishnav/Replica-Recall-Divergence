# Research decision log

Why things are the way they are, so the project doesn't depend on anyone's memory of it. Newest first. Add an entry whenever a research decision is made — a baseline chosen, a metric changed, a hypothesis rejected, a branch merged or archived — not just when something goes wrong.

---

**Adopted the research-branch workflow in `GIT_WORKFLOW.md`.**
Prior work (the original recall-bug investigation, the replica-recall Layer 1 measurement, the catastrophic-disconnection investigation) was all done directly on `main` or on branches that were merged and deleted without a formal spec-first process. That work is real and stays in the historical record as-is — this decision is about *future* branches, not a retroactive relabeling of what already happened. See `GIT_WORKFLOW.md`.

**Restructured the repository to be research-first.**
The repository read as a systems/portfolio project with a research finding mentioned three paragraphs into the README. Restructured so the README leads with the research question and an ESTABLISHED/HYPOTHESIS/OPEN/DO NOT CLAIM breakdown; Nano-DB-the-database moved to an appendix. No research question, methodology, or claim changed — see the commit `restructure: research-first repository layout` and its message for the full rationale. Repository later renamed `nano-db-replica-recall` → `Nano-DB-Replica-Recall` per an explicit user naming convention (title-case per hyphenated segment, acronyms stay capitalized).

**Ruled out pure concurrency and the duplicate-insert bug as the mechanism behind the 58.7%-loss anomalous replica, rather than accepting either on inference.**
Both were plausible from reading the code. Both were tested with deterministic, isolated reproductions (16 concurrent writers / zero chaos for the first; a scripted duplicate-insert-then-bulk-insert for the second) rather than accepted because they were consistent with the observation. Both came back clean. The duplicate-insert bug was real anyway (silently overwrote a vector's data on re-insert) and was fixed regardless of not explaining the anomaly — a bug doesn't need to explain your headline result to be worth fixing. Root cause of the anomaly is still open. See `docs/postmortems/catastrophic-disconnection.md`.

**Fixed the duplicate-insert bug as "no-op if the incoming data is identical, reject if it differs" rather than always rejecting.**
`RemoveShard`'s rebalance path is documented as idempotent and can legitimately re-migrate a key that already landed on a shard — an unconditional reject would have broken that. See `cluster/shard_service_impl.hpp` and the fix commit.

**Changed `node_locks_` from a growing `std::vector` to a fixed 4,096-entry striped pool.**
The growing-vector design had the lock array's growth nested inside the storage-resize branch, so it silently stopped growing once storage pre-allocation covered enough nodes (the original recall-bug root cause). The fix wasn't "grow it correctly" — a fixed striped pool removes a second latent bug (reallocating a vector while other threads index into it is a race) at the same time. See `docs/postmortems/recall-bugs.md`, Bug 2.

**Chose real SIFT1M over uniform-random synthetic vectors as the corpus for the replica-recall experiment.**
Uniform-random 128-d vectors suffer distance concentration — the true top-k becomes close to arbitrary, and recall falls with N for reasons unrelated to replication. Measured directly: `index_recall` showed no separation between baseline and chaos on uniform data (p = 0.31) but separated cleanly on SIFT1M (p = 0.0079). This was tested, not assumed — a benchmark built on the wrong corpus would have concluded there was nothing to find. See `research/replica_recall/README.md`, "Choosing a corpus."

**Chose the exact two-sided Mann-Whitney U test over a t-test for baseline-vs-chaos comparison.**
Five runs per condition is too few to lean on a normality assumption, and the metrics (recall, completeness) are bounded proportions, not naturally normal. Rank-based avoids that assumption. Tradeoff accepted and documented: at n=5 vs n=5 the smallest attainable p-value is 2/252 ≈ 0.0079, so a "significant" result at that floor indicates the groups separate completely, not that the effect is necessarily large — judge magnitude from the means, significance from the p. See `research/replica_recall/README.md`, "The seed sweep."

**Probes bypass the coordinator and hit each replica's gRPC port directly.**
Going through the coordinator would merge replica responses via scatter-gather, which is exactly the mechanism that would hide the divergence under study. This is a deliberate deviation from how a real client would query the cluster, made because the research question is about per-replica state, not client-observed behavior.

**Score `loo_agreement` per-replica (leave-one-out) rather than using aggregate shard-level agreement as the detection signal.**
Aggregate cross-replica agreement is close to tautological: when every replica misses the same hard queries, agreement collapses onto recall whether or not anything is broken, and it reads ~1.0 on a healthy cluster too — it doesn't discriminate. Per-replica peer agreement asks the sharper, operationally relevant question: which replica should I distrust. See `research/replica_recall/README.md`, "What is measured."

**Judge the healing/quiesce result on absolute missing-write count, not on the `completeness` ratio.**
`completeness` is a ratio, and writes keep flowing during the post-chaos observation window — a growing denominator drags the ratio toward 1.0 even when zero missed writes have actually returned (documented concretely as "the dilution trap": completeness climbed 0.9607 → 0.9694 while the absolute missing count stayed flat at 592 across three post-chaos windows). Using the ratio would have reported false recovery. See commit `research: judge healing on absolute missing writes, not the ratio` and `research/replica_recall/README.md`, "The dilution trap."

**Never read graph-forensics data off a live cluster; always tear it down first (`forensics_experiment.py`).**
`element_count` in the on-disk header lags the actual node writes by design (the node body is persisted before the counter is bumped), so a mid-write snapshot of a live cluster looks exactly like corruption. The first forensics attempt measured a running cluster and reported 168 dangling edges that turned out to be nothing. See `docs/postmortems/catastrophic-disconnection.md`.

**Removed the ClickHouse #104674 citation from `RELATED_WORK.md` rather than softening it.**
It had been the lead production-evidence item for the silent-failure motivation. A verification pass reading the full issue thread found the reporter had retracted their own report — the ANN/brute-force disagreement was a bf16-quantization precision artifact reproducing on a clean server, not a durability bug, and there was no maintainer dismissal to cite. A citation that sounds too convenient was checked and found wrong; it was removed rather than reworded to sound less strong, because the underlying fact no longer supports any version of the claim. See `research/RELATED_WORK.md`, §6.

**Walked back the "ground-truth-free" description of Dimitropoulos et al.'s `1/Ratio@k` metric to "judge-free."**
An earlier draft of `RELATED_WORK.md` called it ground-truth-free; on rereading, the metric is defined over differences between retrieved and *true* nearest-neighbour distances, so it still requires exact ground truth to compute. "Judge-free" (no LLM/human judge in the loop) is a real but different property. Corrected rather than left, since the distinction matters for exactly the property this project's own detector needs. See `research/RELATED_WORK.md`, §5.

**Corrected a misattributed author on the STTT verification citation** (Zhang is a coauthor of Adhikari et al., not first author, in an earlier draft's citation of the quasi-linearizability verification work). See `research/RELATED_WORK.md`, §1.
