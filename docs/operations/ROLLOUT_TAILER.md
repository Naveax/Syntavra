# Incremental rollout tailer

The tailer persists file identity, byte offset, incomplete trailing bytes, counters and recent event identities. It handles growth, truncation/rotation, partial JSONL records and duplicate events without rereading the entire session on every update.
