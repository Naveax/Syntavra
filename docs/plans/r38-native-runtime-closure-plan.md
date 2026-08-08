# R38 Native Runtime Closure Plan

## Baseline

- Public Python command paths: **245**
- Independent native Rust paths: **128**
- Missing native paths: **117**
- Declared native coverage: **52.2448%**
- Python launcher bridge paths counted as native: **0**
- Baseline branch head before regression closure: `d51e5075ff0616135e6b7eb3ca77fac1828f2c74`

Coverage counts remain declared/source-level until the exact branch head passes every required workflow.

## Mandatory pre-phase: close regressions in the existing 128 routes

The full package suite exposed 62 regressions in routes already counted as native. No new route may be certified while this debt is non-zero.

1. Repair malformed Rust SQL continuation boundaries and re-run the full package/pre-release suites.
2. Resolve remaining parity clusters: analytics, claim, context governor, evidence graph, host registry, scheduler/job schema, benchmark config, stats, structural index and verifier.
3. Add a regression test for every root cause, not only every observed symptom.
4. Recompute the exact-head workflow matrix and require zero failed required checks.

## Route implementation phases

### Phase 1: Product bootstrap and operator lifecycle

Kullanıcı kurulum/onarım/yönetim yüzeyini native hale getirir. Bu faz, sonraki servis ve ajan route'larının güvenli kurulum sözleşmesine bağımlıdır.

Routes: **9**

- `doctor`
- `init`
- `setup`
- `install`
- `uninstall`
- `repair`
- `status`
- `hook`
- `mcp`

Exit criteria:

- Fresh install, repair, uninstall and repeated execution are idempotent; project/user scope cannot cross-contaminate.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 2: Engine, fabric, platform and adapter control plane

Engine seçimi, fabric kurulumu, adapter konfigürasyonu ve platform sağlık yüzeylerini kapatır.

Routes: **20**

- `engine route`
- `fabric cache-align`
- `fabric compact`
- `fabric doctor`
- `fabric insights`
- `fabric install`
- `fabric installations`
- `fabric platform-plan`
- `fabric profile`
- `fabric rollback-install`
- `fabric route`
- `fabric verify-install`
- `run adapter-certify`
- `run adapter-configure`
- `run adapter-conformance`
- `run adapters`
- `run competitive-doctor`
- `run competitive-status`
- `run platform-doctor`
- `run platform-status`

Exit criteria:

- All control-plane manifests, installation receipts and adapter/platform health results match Python exactly.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 3: Durable state, evidence, artifacts, backup and compression

Kalıcı veri, anahtar rotasyonu, artifact bütünlüğü, yedekleme ve güncelleme rollback zincirini kapatır.

Routes: **18**

- `backup create`
- `backup restore`
- `backup verify`
- `compress describe`
- `compress get`
- `compress put`
- `compress verify`
- `evidence get`
- `evidence rotate-key`
- `run artifact-put`
- `run artifact-query`
- `run artifact-stats`
- `run artifact-verify`
- `run cache-plan`
- `run context-compile`
- `run output-capture`
- `run update-install`
- `run update-rollback`

Exit criteria:

- Round-trip restore, digest verification, key rotation, rollback and crash/retry behavior are deterministic and fail closed.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 4: Memory lifecycle and intelligence

Memory yaşam döngüsünün tamamını aynı SQLite/hash-chain sözleşmesi üzerinde native hale getirir.

Routes: **15**

- `run memory-add`
- `run memory-append`
- `run memory-backfill`
- `run memory-checkpoint`
- `run memory-compact`
- `run memory-export`
- `run memory-extract`
- `run memory-fork`
- `run memory-intelligence-status`
- `run memory-merge`
- `run memory-open`
- `run memory-restore`
- `run memory-retrieve`
- `run memory-search`
- `run memory-verify`

Exit criteria:

- Open→append→checkpoint→compact→fork/merge→export/restore→verify produces the same IDs, hashes, ordering and quarantine behavior.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 5: Code graph, language and semantic services

Repository indeksleme, dil envanteri, grafik sorguları ve semantic servisleri kapatır.

Routes: **14**

- `run code-intel`
- `run graph-impact`
- `run graph-index`
- `run graph-query`
- `run language detect`
- `run language doctor`
- `run language import-index`
- `run language index`
- `run language inventory`
- `run language query`
- `run language remove-index`
- `run semantic-import`
- `run semantic-services`
- `run transcript-mine`

Exit criteria:

- Index creation/import/removal and graph/language/semantic queries match Python on empty, populated, stale and corrupt state.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 6: Provider, gateway, proxy and sandbox isolation

Haricî sağlayıcı sınırı, capability yetkilendirmesi, proxy servisi ve sandbox izolasyonunu kapatır.

Routes: **20**

- `provider capture`
- `provider prepare`
- `provider proxy`
- `provider replay`
- `provider stats`
- `provider verify`
- `run capability-decide`
- `run capability-issue`
- `run capability-verify`
- `run gateway-plan`
- `run provider-pool`
- `run proxy-service install`
- `run proxy-service plan`
- `run proxy-service uninstall`
- `run proxy-service verify`
- `run sandbox-run`
- `run sandbox-status`
- `sandbox backends`
- `sandbox execute`
- `sandbox plan`

Exit criteria:

- Capabilities, provider receipts, proxy lifecycle and sandbox execution enforce identical authorization, redaction and teardown boundaries.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 7: Agent, headless orchestration and interactive operations

Ajan çalıştırma, headless job yaşam döngüsü, console/dashboard ve worker otomasyonunu kapatır.

Routes: **19**

- `agent replay`
- `agent run`
- `run agent-execute`
- `run agent-plan`
- `run console`
- `run dashboard`
- `run headless-cancel`
- `run headless-events`
- `run headless-export`
- `run headless-import`
- `run headless-resume`
- `run headless-run`
- `run headless-status`
- `run headless-submit`
- `run notify`
- `run reliability-run`
- `run rewrite`
- `run watch`
- `run worker`

Exit criteria:

- Submit/run/events/status/cancel/resume/import/export and agent replay are restart-safe, bounded and mutation-equivalent.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

### Phase 8: Final parity proof and comparative gate

Tüm route'lar kapandıktan sonra benchmark karşılaştırması ve entegrasyon kanıtını son kapı olarak çalıştırır.

Routes: **2**

- `benchmark compare`
- `prove integrations`

Exit criteria:

- All 245 paths are native, bridge count is zero, missing inventory is empty and comparative proof passes on all supported platforms.
- Each route has real-binary positive, negative, repeated-option and corruption/adversarial tests.

## Universal certification gates

- Python ve Rust gerçek binary'leriyle exit code eşitliği.
- Canonical JSON/JSONL byte veya normalize edilmiş yapısal eşitlik.
- SQLite şema, transaction, hash-chain ve dosya izinleri için mutation parity.
- Linux, Windows ve macOS differential matrisi.
- Rustfmt, workspace build/test, Clippy `-D warnings`, package ve pre-release tam test paketi.
- `python_launcher_bridge_command_count == 0`; gizli fallback yasak.
- Generated contract, missing-route inventory ve `MANIFEST.sha256` idempotent.

## Count checkpoints

| Checkpoint | Native | Missing | Coverage |
|---|---:|---:|---:|
| Baseline | 128 | 117 | 52.2448% |
| After phase 1 | 137 | 108 | 55.9184% |
| After phase 2 | 157 | 88 | 64.0816% |
| After phase 3 | 175 | 70 | 71.4286% |
| After phase 4 | 190 | 55 | 77.5510% |
| After phase 5 | 204 | 41 | 83.2653% |
| After phase 6 | 224 | 21 | 91.4286% |
| After phase 7 | 243 | 2 | 99.1837% |
| After phase 8 | 245 | 0 | 100.0000% |

## Execution policy

- Work in phase order; a later phase may start only when shared lower-layer contracts are green.
- Route counts change only through the generated metadata synchronizer.
- A route is not closed by parser recognition alone: it requires independent Rust execution and differential evidence.
- Any full-suite regression reopens the owning route and blocks the parity claim.
- PR #107 remains draft until the exact final head passes all required checks.
