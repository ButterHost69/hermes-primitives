/**
 * HMS tool-call gate: a Pi extension that routes every tool call to a policy
 * decision made by the HMS process that spawned this one.
 *
 * `agent.gated_client.GatedPiClient` loads this file with
 * `pi --extension <path>`.  Pi compiles it with jiti, so it stays plain ESM
 * JavaScript with no build step and no dependencies.
 *
 * Wire protocol (see docs/rpc-extension-ui.md in the Pi package):
 *
 *   pi  -> hms  {"type":"extension_ui_request","method":"input",
 *                "title":"hms-tool-gate","placeholder":"<request JSON>"}
 *   hms -> pi   {"type":"extension_ui_response","id":"<same id>",
 *                "value":"<verdict JSON>"}        (or "cancelled": true)
 *
 * The request carries the tool name, the call id, the (possibly nested) parent
 * call id, the validated arguments, and the working directory.  The verdict is
 * one of:
 *
 *   {"action":"block","reason":"..."}  execution is prevented, the model sees
 *                                      the reason as the tool result
 *   {"action":"allow"}                 execution continues unchanged
 *   {"action":"allow","patch":{...}}   arguments are patched in place, then
 *                                      execution continues
 *
 * Anything that is not a usable verdict blocks the call (fail closed), so a
 * missing, slow, or broken HMS process can never let a call through.
 *
 * Never raise this dialog from a startup handler such as `session_start`: Pi
 * awaits those handlers before it starts reading commands, so a dialog there
 * deadlocks until its timeout.  `tool_call` fires mid-turn and is safe.
 */

const GATE_TITLE = "hms-tool-gate";
const REQUEST_VERSION = 1;

/** Cap on the serialized request: `write`/`edit` arguments are unbounded. */
const MAX_REQUEST_BYTES = 4096;

function readTimeout() {
  const raw = Number(process.env.HMS_TOOL_GATE_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : 30000;
}

function clip(value) {
  let text;
  try {
    text = JSON.stringify(value);
  } catch {
    return { clipped: true, reason: "arguments are not JSON-serializable" };
  }
  const bytes = Buffer.byteLength(text, "utf8");
  if (bytes <= MAX_REQUEST_BYTES) return value;
  return { clipped: true, bytes, preview: text.slice(0, MAX_REQUEST_BYTES) };
}

function buildRequest(event, cwd) {
  const request = {
    v: REQUEST_VERSION,
    toolName: event.toolName,
    toolCallId: event.toolCallId,
    cwd,
    input: clip(event.input),
  };
  if (event.parentToolCallId) request.parentToolCallId = event.parentToolCallId;
  return request;
}

function parseVerdict(answer) {
  if (typeof answer !== "string" || answer.length === 0) return null;
  let verdict;
  try {
    verdict = JSON.parse(answer);
  } catch {
    return null;
  }
  if (!verdict || typeof verdict !== "object") return null;
  if (verdict.action !== "allow" && verdict.action !== "block") return null;
  return verdict;
}

export default function (pi) {
  const timeout = readTimeout();

  pi.on("tool_call", async (event, ctx) => {
    // Ask on every call, including the nested calls a codemode script makes
    // through ctx.executeTool(): those reach this handler too, with
    // parentToolCallId set, so the policy sees the whole call tree.
    const request = buildRequest(event, ctx.cwd);

    if (!ctx.hasUI) {
      return { block: true, reason: "tool gate: no decision channel available" };
    }

    let answer;
    try {
      answer = await ctx.ui.input(GATE_TITLE, JSON.stringify(request), { timeout });
    } catch {
      return { block: true, reason: "tool gate: the decision channel failed" };
    }

    const verdict = parseVerdict(answer);
    if (!verdict) {
      return { block: true, reason: "tool gate: no usable verdict (fail closed)" };
    }
    if (verdict.action === "block") {
      return { block: true, reason: verdict.reason || "tool gate: blocked by policy" };
    }
    // Pi does not re-validate mutated input, so a patch must be schema-valid.
    if (verdict.patch && typeof verdict.patch === "object") {
      Object.assign(event.input, verdict.patch);
    }
    return undefined;
  });
}
