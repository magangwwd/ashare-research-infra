import test from "node:test";
import assert from "node:assert/strict";

import { decideWatchdog, localDay } from "./decision.mjs";

const now = new Date("2026-10-06T13:00:00Z"); // 21:00 Asia/Shanghai

function run(overrides = {}) {
  return {
    id: 100,
    event: "schedule",
    display_title: "Task 01 scheduled",
    created_at: "2026-10-06T12:17:00Z",
    status: "completed",
    conclusion: "success",
    run_attempt: 1,
    ...overrides,
  };
}

test("localDay uses Asia/Shanghai boundary", () => {
  assert.equal(localDay("2026-10-05T16:30:00Z"), "2026-10-06");
});

test("dispatches when no managed run exists today", () => {
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

test("stops when today's managed run succeeded", () => {
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

test("yesterday's success cannot suppress today", () => {
  const yesterday = run({ created_at: "2026-10-05T12:17:00Z" });
  assert.equal(decideWatchdog([yesterday], now).action, "dispatch");
});
