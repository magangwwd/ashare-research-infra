export const FAILURE_CONCLUSIONS = new Set([
  "failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"
]);

export function localDay(value, timeZone = "Asia/Shanghai") {
  const date = value instanceof Date ? value : new Date(value);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const obj = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${obj.year}-${obj.month}-${obj.day}`;
}

// The Task 01 primary scheduled slot belongs to the 21:17 local trading day.
// Backup at 23:30 / next-day 03:23 and watchdog recovery share this slot.
// The project timezone is Asia/Shanghai (UTC+08:00, no DST).
export function lastScheduledSlot(now = new Date(), timeZone = "Asia/Shanghai") {
  if (timeZone !== "Asia/Shanghai" && timeZone !== "Asia/Singapore") {
    throw new Error("Unsupported watchdog schedule timezone");
  }
  const clock = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  }).formatToParts(now).map(part => [part.type, part.value]));
  const beforeScheduledSlot = Number(clock.hour) * 60 + Number(clock.minute) < 21 * 60 + 17;
  const day = localDay(new Date(now.getTime() - (beforeScheduledSlot ? 86400000 : 0)), timeZone);
  return new Date(`${day}T13:17:00Z`);
}

export function isManagedRun(run) {
  return run?.event === "schedule"
    || (run?.event === "workflow_dispatch" && run?.display_title === "Task 01 watchdog");
}

export function decideWatchdog(runs, now = new Date(), timeZone = "Asia/Shanghai") {
  const slot = lastScheduledSlot(now, timeZone);
  const candidates = runs
    .filter(isManagedRun)
    .filter((run) => new Date(run.created_at) >= slot)
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

  if (!candidates.length) {
    return { action: "dispatch", reason: "no managed Task 01 run exists since the latest 21:17 slot" };
  }

  const active = candidates.find((run) => ["queued", "in_progress", "pending", "waiting", "requested"].includes(run.status));
  if (active) {
    return { action: "wait", run_id: active.id, reason: `run ${active.id} is ${active.status}` };
  }

  const successful = candidates.find((run) => run.conclusion === "success");
  if (successful) {
    return { action: "done", run_id: successful.id, reason: `run ${successful.id} already succeeded today` };
  }

  const latest = candidates[0];
  if (latest && FAILURE_CONCLUSIONS.has(latest.conclusion)) {
    const attempt = Number(latest.run_attempt || 1);
    if (attempt < 2) {
      return { action: "rerun", run_id: latest.id, reason: `run ${latest.id} failed on attempt ${attempt}` };
    }
    return {
      action: "alert",
      run_id: latest.id,
      conclusion: latest.conclusion,
      attempt,
      reason: `run ${latest.id} failed after ${attempt} attempts`,
    };
  }

  return { action: "wait", run_id: latest?.id ?? null, reason: "latest managed run has a non-terminal state" };
}
