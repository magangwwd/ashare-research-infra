import { decideWatchdog } from "./decision.mjs";
import { isAuthorized } from "./auth.mjs";

const OWNER = "magangwwd";
const REPO = "ashare-research-infra";
const WORKFLOW = "task01.yml";
const ALERT_WORKFLOW = "watchdog-alert.yml";
const BRANCH = "main";

async function github(env, path, init = {}) {
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN secret is required");
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "ashare-task01-watchdog",
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`GitHub ${response.status}: ${body.slice(0, 500)}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function evaluate(env) {
  const data = await github(
    env,
    `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=50`,
  );
  const decision = decideWatchdog(data.workflow_runs || [], new Date(), "Asia/Shanghai");

  if (decision.action === "dispatch") {
    await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({
        ref: BRANCH,
        inputs: {
          verify_drive_auth: false,
          publish_drive_snapshot: false,
          force_drive_restore: false,
          watchdog_trigger: true,
        },
      }),
    });
  } else if (decision.action === "rerun") {
    await github(env, `/repos/${OWNER}/${REPO}/actions/runs/${decision.run_id}/rerun`, {
      method: "POST",
      body: JSON.stringify({ enable_debug_logging: false }),
    });
  } else if (decision.action === "alert") {
    await github(env, `/repos/${OWNER}/${REPO}/actions/workflows/${ALERT_WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({
        ref: BRANCH,
        inputs: {
          failed_run_id: String(decision.run_id),
          conclusion: decision.conclusion || "failure",
          attempt: String(decision.attempt || 2),
        },
      }),
    });
  }

  console.log(JSON.stringify({ watchdog: "task01", ...decision }));
  return decision;
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(evaluate(env));
  },
  async fetch(request, env) {
    // The HTTP path can dispatch workflows and re-run Actions, so it is closed
    // unless the caller presents WATCHDOG_HTTP_TOKEN. Rejections look like a
    // missing route and never reveal whether a token is configured.
    if (!isAuthorized(request, env)) {
      return new Response("Not found", { status: 404 });
    }
    const decision = await evaluate(env);
    return Response.json(decision);
  },
};
