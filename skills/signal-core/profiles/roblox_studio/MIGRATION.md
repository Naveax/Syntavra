# Migration Notes

The clean materialized profile replaces the documentation-only/payload branch. Existing version remains `0.0.1`.

Task-state schema version 1 can be migrated to version 2 with `migrate_task_state`. Activation envelope schema version 1 is not accepted because security-sensitive unknown or omitted identity fields must fail closed.
