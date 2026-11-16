# Sample read-only provider

This is a minimal pure-Python provider package used by the provider SDK guide. It reports an empty VM/switch inventory and placeholder host metrics. It intentionally declares only `inventory.read` and `host.metrics.read`; create and lifecycle calls are rejected by the capability-gated worker adapter. Do not use it to manage real infrastructure.
