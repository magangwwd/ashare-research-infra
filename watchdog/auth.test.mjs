import test from "node:test";
import assert from "node:assert/strict";

import { isAuthorized, timingSafeEqual } from "./auth.mjs";

function request(authorization) {
  const headers = authorization === undefined ? {} : { Authorization: authorization };
  return new Request("https://watchdog.example/", { headers });
}

test("rejects every request when no HTTP token is configured", () => {
  assert.equal(isAuthorized(request("Bearer anything"), {}), false);
  assert.equal(isAuthorized(request("Bearer "), { WATCHDOG_HTTP_TOKEN: "" }), false);
  assert.equal(isAuthorized(request(undefined), undefined), false);
});

test("rejects missing or malformed Authorization headers", () => {
  const env = { WATCHDOG_HTTP_TOKEN: "s3cret" };
  assert.equal(isAuthorized(request(undefined), env), false);
  assert.equal(isAuthorized(request("s3cret"), env), false);
  assert.equal(isAuthorized(request("Basic s3cret"), env), false);
  assert.equal(isAuthorized(request("Bearer"), env), false);
});

test("rejects a wrong token, including prefixes and extensions of the real one", () => {
  const env = { WATCHDOG_HTTP_TOKEN: "s3cret" };
  assert.equal(isAuthorized(request("Bearer wrong"), env), false);
  assert.equal(isAuthorized(request("Bearer s3cre"), env), false);
  assert.equal(isAuthorized(request("Bearer s3cret!"), env), false);
});

test("accepts the configured bearer token", () => {
  assert.equal(isAuthorized(request("Bearer s3cret"), { WATCHDOG_HTTP_TOKEN: "s3cret" }), true);
});

test("timingSafeEqual compares by value and handles unequal lengths", () => {
  assert.equal(timingSafeEqual("abc", "abc"), true);
  assert.equal(timingSafeEqual("abc", "abd"), false);
  assert.equal(timingSafeEqual("abc", "abcd"), false);
  assert.equal(timingSafeEqual("", ""), true);
});
