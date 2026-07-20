# Incremental rollout tailer

The tailer persists file identity, byte offset, incomplete trailing bytes, counters and recent event identities. It handles file growth, truncation/rotation, partial JSONL records and duplicates without rereading the complete session.

When a provider reports total input and cached input, fresh input is calculated as `max(0, total - cached)`. Explicit fresh/uncached counters take precedence. The tailer also reports fresh-token fraction and wait-calls per model turn.
