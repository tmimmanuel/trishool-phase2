// Rebuild the tri-claw gateway's verbatim system prompt for white-box GCG.
import { loadWorkspaceBootstrapFiles } from "./src/agents/workspace.js";
import {
  buildBootstrapContextFiles,
  resolveBootstrapMaxChars,
  resolveBootstrapTotalMaxChars,
} from "./src/agents/pi-embedded-helpers.js";
import { buildAgentSystemPrompt } from "./src/agents/system-prompt.js";
import { buildModelAliasLines } from "./src/agents/model-alias-lines.js";
import { readFileSync } from "node:fs";

const cfg = JSON.parse(readFileSync("./docker/openclaw.lean.json", "utf8"));
const workspaceDir = "/home/node/.openclaw/workspace-main"; // display path (container)
const files = await loadWorkspaceBootstrapFiles("/tmp/triclaw_ws");
const contextFiles = buildBootstrapContextFiles(files, {
  maxChars: resolveBootstrapMaxChars(cfg),
  totalMaxChars: resolveBootstrapTotalMaxChars(cfg),
});
const prompt = buildAgentSystemPrompt({
  workspaceDir,
  defaultThinkLevel: "off",
  reasoningLevel: "off",
  toolNames: ["sessions_send", "trishool-eval-pii"],
  modelAliasLines: buildModelAliasLines(cfg),
  docsPath: "/app/docs",
  heartbeatPrompt: undefined,
  contextFiles,
  runtimeInfo: {
    host: "43bf6f3041f5",
    os: "Linux 6.8.0-138-generic",
    arch: "x64",
    node: "v22.22.0",
    model: "chutes/Qwen/Qwen3.8-27B-TEE",
    defaultModel: "chutes/Qwen/Qwen3.8-27B-TEE",
    shell: "sh",
  },
});
await Bun.write("/tmp/triclaw_system_prompt.txt", prompt);
console.log("chars:", prompt.length, "| files:", files.filter(f=>!f.missing).map(f=>f.name).join(","));
