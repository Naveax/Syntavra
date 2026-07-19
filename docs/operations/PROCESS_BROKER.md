# Process broker

Jobs are persisted in SQLite WAL, stdout and stderr stream directly to per-job files, and completion is written to an append-only queue. Background submission returns once with `JOB_ACCEPTED`; no model-mediated wait loop is required. Timeout and cancellation terminate the process tree where the operating system permits it.
