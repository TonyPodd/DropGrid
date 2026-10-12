# Community Discovery human validation

Discovery recommends candidates; human review labels them; explicit confirmed Add creates a Grid
membership. None of these operations posts to VK or creates a Submission.

## Review and snapshots

`/discovery/review/:batchId` shows one existing Candidate at a time. Batch membership references the
Candidate and originating Run; it does not clone analysis. Review reads frozen originating-run evidence
so subsequent expanded searches cannot alter the cohort being validated. Tony and Tima retain equal access, trusted
proxy attribution, independent server cursors, filters and decisions. Reload resumes that cursor.

A = suitable, R = reject, U = uncertain, G = open VK, Left = previous, Right = next pending.
Typing/modifiers/repeated key events do not trigger actions. The optional rejection reason picker
supports wrong topic/region, inactivity, too broad, commercial/irrelevant, duplicate, other; digits
1–6 select the first six. A decision advances automatically; previous decisions can be revised.

New labels freeze the scores, activity/region evidence, queries, seed count/IDs/names, closest-community
similarities, originating cluster and preview identities seen by the reviewer. Analysis-version and
expected-revision checks reject stale submissions. Later discovery cannot rewrite label snapshots.
Latest decisions are authoritative; append-only events preserve every revision and original snapshot.

Previews use the existing analysis cache, never a fresh wall scan when opening review. Images use
`object-fit: contain` and open enlarged. Eviction shows explicit placeholders; the VK link stays usable.
Discovery cache remains capped at 300 MiB / 30 days. Diagnostics include bytes, entries, oldest and
cumulative evictions since this migration. Production media/reference namespaces are not pruned.

## Metrics

Without labels precision and precision@K are **N/A**, not zero. Overall approval rate is approved/all
reviewed. Binary precision is approved/(approved+rejected); uncertain is separate and never a negative
training example. One latest label per canonical candidate avoids duplicate counting across revisions,
reviewers and batches. Agreement uses the latest per-reviewer votes on overlapping candidates.

Breakdowns cover confidence, cluster, score/visual buckets, region, activity, topic and seed/query
support. Seed memberships are contextual provenance; distinct matching queries are independent query
support. Reports distinguish multi-seed from multi-query. Precision@5/10/20 requires decisive labels
for every candidate of the frozen original top-K; it never substitutes arbitrary reviewed rows.

Threshold simulations at 0.90/0.85/0.80 apply **only to the labeled sample**, reporting false additions
and uncertainties. Fewer than 20 decisive labels is a small sample. No learned ranker, automatic weight
change, auto-add, scheduled discovery, or publication-outcome inference is enabled.

Older runs had no per-run snapshots. Migration explicitly marks their frozen current evidence as
`migration_current_analysis`: available analysis, not reconstructed historical scores. New run evidence
freezes on completion. Existing labels are never recomputed.

## Controlled membership and safety

Candidate `human_status` is separate from membership; legacy `status=approved` means Grid-added.
Human approval leaves the candidate outside the Grid. Add requires the latest actual approved label
and confirmation of category/region/content hint. Provenance includes candidate/run IDs, human reviewer,
decision timestamp/revision, added_at, suggested vs final category/region. It creates no Submission.
Later review revisions never silently delete an explicitly added Grid row.

Rejection suppresses future searches. Explicit Inbox Restore returns a candidate to the queue without
fabricating a positive label or deleting prior events. A new manual approval is required before Add.

The real `Discovery validation v1` batch captures the initial 66 candidates before expanded pilots.
Automation never clicks real decisions. Mutation QA uses isolated PostgreSQL, mock VK and browser
fixtures. Additional pilots run sequentially within existing budgets, writing only discovery metadata
and cache. Photo-review fingerprints must match before/after. VK writes remain false, allowlist empty,
sender stopped, schedules disabled; the regional own-archive minimum stays 365 days.
