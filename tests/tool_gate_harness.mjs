// Drives agent/tool_gate.js with a fake `pi`, so the extension's behavior is
// tested without spawning a real agent.  The extension is authored as ESM for
// Pi's jiti loader; this harness rewrites its single `export default` statement
// so plain Node can evaluate it, and injects `process` and `Buffer`.
import { readFileSync } from "node:fs";

const EXTENSION = new URL("../agent/tool_gate.js", import.meta.url);
const source = readFileSync(EXTENSION, "utf8").replace(
  "export default function",
  "module.exports = function",
);

let failures = 0;

function check(name, condition, detail) {
  if (condition) {
    console.log(`ok   ${name}`);
    return;
  }
  failures += 1;
  console.log(`FAIL ${name}${detail === undefined ? "" : ` -> ${detail}`}`);
}

/** Load the extension and return its registered handlers. */
function load(env) {
  const handlers = [];
  const pi = { on: (event, handler) => handlers.push({ event, handler }) };
  const module = { exports: {} };
  new Function("module", "process", "Buffer", source)(
    module,
    { env },
    Buffer,
  );
  module.exports(pi);
  return handlers;
}

function context(impl, { hasUI = true, cwd = "/repo" } = {}) {
  const calls = [];
  return {
    calls,
    hasUI,
    cwd,
    ui: {
      input: async (title, placeholder, opts) => {
        calls.push({ title, payload: JSON.parse(placeholder), opts });
        return impl ? impl(title, placeholder) : undefined;
      },
    },
  };
}

const gate = load({ HMS_TOOL_GATE_TIMEOUT_MS: "1234" }).find(
  (handler) => handler.event === "tool_call",
);
const nestedEvent = {
  toolName: "bash",
  toolCallId: "call_1/2",
  parentToolCallId: "call_1",
  input: { command: "ls" },
};

check("registers a tool_call handler", gate !== undefined);

// A verdict of "allow" lets the call through untouched.
{
  const event = { toolName: "bash", toolCallId: "call_1", input: { command: "ls" } };
  const ctx = context(() => JSON.stringify({ action: "allow" }));
  const result = await gate.handler(event, ctx);
  check("allow returns undefined", result === undefined, JSON.stringify(result));
  check("dialog carries the gate marker", ctx.calls[0].title === "hms-tool-gate");
  check(
    "request describes the call",
    ctx.calls[0].payload.v === 1 &&
      ctx.calls[0].payload.toolName === "bash" &&
      ctx.calls[0].payload.toolCallId === "call_1" &&
      ctx.calls[0].payload.cwd === "/repo" &&
      ctx.calls[0].payload.input.command === "ls",
    JSON.stringify(ctx.calls[0].payload),
  );
  check("extension waits the configured timeout", ctx.calls[0].opts.timeout === 1234);
  check(
    "nested calls report their parent",
    (await (() => {
      const nestedCtx = context(() => JSON.stringify({ action: "allow" }));
      return gate.handler(nestedEvent, nestedCtx).then(
        () => nestedCtx.calls[0].payload.parentToolCallId,
      );
    })()) === "call_1",
  );
}

// A verdict of "block" prevents execution and explains why.
{
  const event = { toolName: "write", toolCallId: "call_2", input: { path: "/etc/hosts" } };
  const ctx = context(() => JSON.stringify({ action: "block", "reason": "protected path" }));
  const result = await gate.handler(event, ctx);
  check(
    "block prevents the call with its reason",
    result && result.block === true && result.reason === "protected path",
    JSON.stringify(result),
  );
}

// A patch rewrites the arguments before the tool runs.
{
  const event = { toolName: "bash", toolCallId: "call_3", input: { command: "rm -rf /" } };
  const ctx = context(() =>
    JSON.stringify({ action: "allow", patch: { command: "ls /repo" } }),
  );
  const result = await gate.handler(event, ctx);
  check("patched call is allowed", result === undefined, JSON.stringify(result));
  check(
    "patch landed in the tool input",
    event.input.command === "ls /repo",
    JSON.stringify(event.input),
  );
}

// Everything that is not a usable verdict fails closed.
const unanswerable = [
  ["no answer at all", () => undefined],
  ["cancelled answer", () => ""],
  ["answer that is not JSON", () => "allow"],
  ["verdict that is not an object", () => "42"],
  ["unknown action", () => JSON.stringify({ action: "escalate" })],
];
for (const [name, impl] of unanswerable) {
  const event = { toolName: "bash", toolCallId: "call_4", input: { command: "ls" } };
  const ctx = context(impl);
  const result = await gate.handler(event, ctx);
  check(`${name} blocks`, result && result.block === true, JSON.stringify(result));
}

// A broken channel fails closed too.
{
  const event = { toolName: "bash", toolCallId: "call_5", input: { command: "ls" } };
  const ctx = {
    hasUI: true,
    cwd: "/repo",
    ui: {
      input: async () => {
        throw new Error("channel closed");
      },
    },
  };
  const result = await gate.handler(event, ctx);
  check("channel failure blocks", result && result.block === true);
}

// No decision channel means no execution.
{
  const event = { toolName: "bash", toolCallId: "call_6", input: { command: "ls" } };
  const ctx = context(undefined, { hasUI: false });
  const result = await gate.handler(event, ctx);
  check(
    "missing UI blocks without asking",
    result && result.block === true && ctx.calls.length === 0,
    JSON.stringify(result),
  );
}

// Oversized arguments are clipped so the pipe stays small.
{
  const event = {
    toolName: "write",
    toolCallId: "call_7",
    input: { path: "/repo/big.txt", content: "x".repeat(20000) },
  };
  const ctx = context(() => JSON.stringify({ action: "allow" }));
  await gate.handler(event, ctx);
  const payload = JSON.stringify(ctx.calls[0].payload);
  check("oversized arguments are clipped", payload.length < 8000, String(payload.length));
  check(
    "clipping is reported, not silent",
    ctx.calls[0].payload.input.clipped === true &&
      typeof ctx.calls[0].payload.input.preview === "string",
    JSON.stringify(ctx.calls[0].payload.input).slice(0, 120),
  );
}

// Without the environment variable the gate falls back to its default timeout.
{
  const fallback = load({}).find((handler) => handler.event === "tool_call");
  const ctx = context(() => JSON.stringify({ action: "allow" }));
  await fallback.handler(
    { toolName: "bash", toolCallId: "call_8", input: { command: "ls" } },
    ctx,
  );
  check("default timeout is 30s", ctx.calls[0].opts.timeout === 30000);
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
