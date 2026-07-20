# Durable process broker

The broker persists jobs in SQLite WAL and streams stdout/stderr directly to files. Background submission returns one `JOB_ACCEPTED` response. A detached worker executes the command, maintains a heartbeat, enforces timeout/cancellation and records one durable completion event.

Completion events use an increasing SQLite sequence. Consumers call `job completions --after <cursor>` and advance the cursor; they do not ask a model to poll a running process. A compatibility JSONL queue is retained, but SQLite is authoritative.

Every job stores project identity, repository tree and environment hash. The detached worker reconstructs the evidence store with the persisted project identity, preventing cross-project evidence scope mismatches.
