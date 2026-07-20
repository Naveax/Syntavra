# Installer and sandbox operations

## Installation

`signalcore install --all --dry-run` prints the planned host changes. A real install writes only host-specific managed blocks, MCP entries, hooks and skill locations, while preserving backups under `.signalcore/install/backups`. Repeated installation keeps the first pre-SignalCore backup. `signalcore uninstall` restores it.

## Sandbox selection

`auto` selects Docker, Podman, bubblewrap, then local-restricted. A strict `network=none` request fails if none of the isolating backends exists. Local-restricted execution applies portable environment filtering, timeout and process-tree control but explicitly reports unavailable network/filesystem isolation.
