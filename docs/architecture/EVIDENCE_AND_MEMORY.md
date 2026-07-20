# Exact evidence and scoped memory

## Evidence

- Objects are addressed by SHA-256 handles: `sc://sha256/<digest>`.
- Large output is streamed into a temporary file, hashed incrementally and atomically installed.
- Metadata records project scope, byte length, kind and provenance.
- Reads verify the object digest and reject scope mismatches or corruption.
- Bounded summaries never replace the exact object; they reference it.

## Memory

- Records are scoped by project and user.
- FTS5 retrieval is combined with confidence, recency and relation weight.
- Records support tags, expiry, provenance, deduplication and supersession.
- Weighted directed relations represent support, contradiction, dependency and other typed links.
- Expired and superseded records are excluded by default.
