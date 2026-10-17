// Authentication for the Worker's HTTP handler.
//
// The scheduled (Cron) path never goes through this module. The HTTP handler
// can dispatch workflows and re-run Actions with the GitHub token, so it is
// closed by default: with no WATCHDOG_HTTP_TOKEN secret configured, every HTTP
// request is rejected.

const encoder = new TextEncoder();

// Compare without returning early on the first differing byte. The length
// difference is folded into the result instead of short-circuiting.
export function timingSafeEqual(a, b) {
  const x = encoder.encode(String(a));
  const y = encoder.encode(String(b));
  let diff = x.length ^ y.length;
  const length = Math.max(x.length, y.length);
  for (let i = 0; i < length; i += 1) {
    diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  }
  return diff === 0;
}

export function isAuthorized(request, env) {
  const secret = env?.WATCHDOG_HTTP_TOKEN;
  if (typeof secret !== "string" || secret.length === 0) return false;
  const header = request.headers.get("Authorization") || "";
  const match = /^Bearer (.+)$/.exec(header);
  if (!match) return false;
  return timingSafeEqual(match[1], secret);
}
