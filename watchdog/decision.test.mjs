import test from "node:test";
import assert from "node:assert/strict";

import { decideWatchdog, localDay, lastScheduledSlot } from "./decision.mjs";

const now = new Date("2026-10-06T17:00:00Z"); // Oct 7 01:00 Asia/Shanghai, after Oct 6 21:17 slot

function run(overrides = {}) {
  return {
    id: 100,
    event: "schedule",
    display_title: "Task 01 scheduled",
    created_at: "2026-10-06T13:25:00Z",
    status: "completed",
    conclusion: "success",
    run_attempt: 1,
    ...overrides,
  };
}

test("localDay uses Asia/Shanghai boundary", () => {
  assert.equal(localDay("2026-10-05T16:30:00Z"), "2026-10-06");
});

test("dispatches when no managed run exists since last scheduled slot", () => {
  assert.equal(decideWatchdog([], now).action, "dispatch");
});

test("ignores ordinary manual runs", () => {
  const manual = run({ event: "workflow_dispatch", display_title: "Task 01 manual" });
  assert.equal(decideWatchdog([manual], now).action, "dispatch");
});

test("waits when scheduled run is active", () => {
  const decision = decideWatchdog([run({ status: "in_progress", conclusion: null })], now);
  assert.equal(decision.action, "wait");
});

test("stops when last slot's managed run succeeded", () => {
  const decision = decideWatchdog([run()], now);
  assert.equal(decision.action, "done");
});

test("reruns first terminal failure", () => {
  const decision = decideWatchdog([run({ conclusion: "cancelled", run_attempt: 1 })], now);
  assert.equal(decision.action, "rerun");
  assert.equal(decision.run_id, 100);
});

test("alerts after second terminal failure", () => {
  const decision = decideWatchdog([run({ conclusion: "failure", run_attempt: 2 })], now);
  assert.equal(decision.action, "alert");
});

test("older slot success cannot suppress latest slot", () => {
  const yesterday = run({ created_at: "2026-10-05T13:25:00Z" });
  assert.equal(decideWatchdog([yesterday], now).action, "dispatch");
});

test("next-day 03:23 backup belongs to preceding 21:17 slot", () => {
  assert.equal(lastScheduledSlot(now).toISOString(), "2026-10-06T13:17:00.000Z");
  assert.equal(decideWatchdog([run()], now).action, "done");
});

test("next evening cannot be suppressed by last night's acceptance", () => {
  const nextEvening = new Date("2026-10-07T15:55:00Z"); // Oct 7 23:55
  assert.equal(decideWatchdog([run()], nextEvening).action, "dispatch");
});

test("earlier-in-evening manual checks still watch previous slot", () => {
  const evening = new Date("2026-10-07T12:40:00Z"); // Oct 7 20:40
  assert.equal(decideWatchdog([run()], evening).action, "done");
});

test("21:17 primary opens a new session slot", () => {
  assert.equal(lastScheduledSlot(new Date("2026-10-07T13:16:00Z")).toISOString(),
               "2026-10-06T13:17:00.000Z"); // Oct 7 21:16
  assert.equal(lastScheduledSlot(new Date("2026-10-07T13:17:00Z")).toISOString(),
               "2026-10-07T13:17:00.000Z"); // Oct 7 21:17
});

test("23:30 and next-day 03:23 share one primary session", () => {
  assert.equal(lastScheduledSlot(new Date("2026-10-06T15:30:00Z")).toISOString(),
               "2026-10-06T13:17:00.000Z");
  assert.equal(lastScheduledSlot(new Date("2026-10-06T19:23:00Z")).toISOString(),
               "2026-10-06T13:17:00.000Z");
  assert.equal(decideWatchdog([run()], new Date("2026-10-06T19:23:00Z")).action, "done");
});

// Regression for the actual post-midnight fallback windows (2026-11-03).
// Other repositories/workflows may have scheduled runs; only Task01 managed
// runs in the current 21:17-to-next-day session may suppress dispatch.
test("post-midnight fallback checks dispatch if the Task01 primary and backup were absent", () => {
  const previousSession = run({
    id: 41359343597,
    created_at: "2026-11-01T22:53:05Z",
    conclusion: "cancelled",
  });
  for (const utc of [
    "2026-11-02T16:20:00Z", // Nov 3 00:20 Beijing
    "2026-11-02T17:20:00Z", // Nov 3 01:20 Beijing
    "2026-11-02T18:20:00Z", // Nov 3 02:20 Beijing
    "2026-11-02T22:20:00Z", // Nov 3 06:20 Beijing
  ]) {
    const decision = decideWatchdog([previousSession], new Date(utc));
    assert.equal(decision.action, "dispatch", utc);
    assert.equal(lastScheduledSlot(new Date(utc)).toISOString(), "2026-11-02T13:17:00.000Z");
  }
});
