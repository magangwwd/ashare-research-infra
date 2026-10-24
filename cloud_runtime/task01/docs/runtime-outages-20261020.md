# Task 01 repeated environment outages: evidence and mitigations

Checked on 2026-10-20. No live backfill, guard or WeChat process was stopped or restarted during this investigation. Existing PIDs were 22247, 22241 and 22259. The independent lifecycle observer was added as PID 23076.

## What the evidence establishes

| Previous last guard heartbeat (UTC) | New container PID 1 start (UTC) | Gap |
| --- | --- | --- |
| 2026-10-19 16:44:45.715 | 2026-10-19 16:50:01.995 | 316 seconds |
| 2026-10-20 06:09:01.541 | 2026-10-20 06:14:11.316 | 310 seconds |

All three old application PIDs were absent after these container changes. The old files still said RUNNING. This establishes loss of the previous process environment, rather than proof of a normal Python exception or a Drive rate-limit exit. The approximately five-minute gaps are an observation, not proof of a particular idle timeout or tunnel policy.

The current container has `memory.events` oom=0, oom_kill=0 and ample disk space. These counters belong to the current container and cannot exclude OOM, eviction or other failures in the prior container's final moments. No previous-boot journal, host kernel log or platform lifecycle audit was available. The platform status API reports only the current connection; its absence of a current error does not establish the past cause.

The known source-backup upload-session mismatch was a separate application startup failure: its watchdog and notifier stayed alive. It was fixed using SHA256-specific upload journals and regression-tested. It does not explain the disappearance of all three services across a new container.

## Measures now implemented

- `data_pipeline.runtime_lifecycle`: every 60 seconds checks actual /proc identities, records container fingerprint and cgroup memory events, and refreshes the runtime bridge. Only role, PID, PPID and process start identity are recorded; no environment values or raw arguments. It never restarts a service, calls Tushare/Drive or sends inactivity alerts.
- `data_pipeline.runtime_startup`: idempotent startup, invoked when an environment/task becomes available. It preserves a live worker/guard, refuses duplicate or foreign workers, respects STOP, COMPLETE and BLOCKED states, and leaves intentional cooling alone. A prior RUNNING heartbeat predating a new container can be resumed. This is an invocation-based recovery command, not a provider boot hook or a keepalive mechanism.
- Existing local WAL checkpoints, immutable Drive working checkpoints, hash-verified credential-free program backups and strict final acceptance remain in place.
- `data_pipeline.external_heartbeat`: optional passive probe every 120 seconds to an owned Healthchecks check. It validates a live guard and fresh heartbeat rather than download rate, so long verification or cooling does not produce a false outage alert. It is prepared but NOT CONNECTED without a securely configured HEALTHCHECKS_PING_URL and external notification integration.

Commands:

```
.venv/bin/python -m data_pipeline.runtime_startup --dry-run
.venv/bin/python -m data_pipeline.runtime_startup
```

Do not use `--resume` to override an intentional STOP/BLOCKED state unless explicitly requested. Do not add another backfill process alongside an orphan worker.

## Why internal watchdogs cannot prevent this class of outage

The cloud environment's published snapshot and its live processes are distinct. The platform contract does not promise to preserve processes when restoring or reconnecting a snapshot. A saved `start_skill` contains agent instructions; saving a draft does not execute it, publish a new snapshot, create an OS service, or guarantee startup without an agent invocation. Another local while-loop, cron or watchdog dies with the same container.

The exposed API has no supported always-on/idle-timeout setting or lifecycle history to change here. Preventing provider-initiated termination requires an actual persistent-runtime feature or a supported startup/restart hook from the provider. These cannot be fabricated by application code. Publishing/rebuilding the current environment to apply draft changes is deliberately deferred while backfill is active.

## External WeChat outage alert (pending user configuration)

1. Create a Healthchecks check in an account you control: period 5 minutes, grace 10 minutes. The sender pings every 2 minutes. An outage should be detected roughly 15 minutes after its last accepted ping; notification delivery latency is separate.
2. Configure a Healthchecks notification/webhook to PushPlus using the existing user's token entered privately in that service. Use HTTPS `https://www.pushplus.plus/send`, POST JSON, with fields token, title, content and template=txt. Do not put the actual token in source or chat.
3. Store the check's HTTPS hc-ping.com ping URL securely as HEALTHCHECKS_PING_URL in environment settings, not in the repository or chat. Add hc-ping.com to the existing restricted network policy through the supported environment settings. Plan a safe checkpoint window if those changes require a runtime restart or publication.
4. Invoke the startup helper after the setting is actually available. Confirm external-heartbeat.json says PING_ACCEPTED and the service dashboard receives heartbeats. The external check and WeChat delivery must be tested before calling the alert operational.
5. Pause the external check for planned environment maintenance. This mechanism alerts you; it cannot itself power on a stopped Codex environment.

PushPlus's cloud-monitoring documentation describes forwarding alerts from an external cloud monitoring system, not detecting the absence of messages by itself. An additional external monitor account or endpoint is therefore necessary.

## Provider support request

Use `runtime_bridge/platform-support-outages.md`. Ask for idle/suspend/recycle, instance eviction/OOM, publication/rebuild/restore and tunnel disconnect events for the two UTC windows. No support message has been sent automatically. Without that platform audit, the exact trigger remains UNKNOWN.
