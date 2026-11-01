#!/usr/bin/env node
const readline = require("node:readline");
readline.createInterface({input: process.stdin}).on("line", line => {
  const m = JSON.parse(line);
  if (m.id === undefined) return;
  let result;
  if (m.method === "initialize") result = {protocolVersion: 1,
    agentCapabilities: {sessionCapabilities: process.env.TEST_NO_RESUME ? {} : {resume: {}}}};
  else if (m.method === "session/new") result = {sessionId: "npm-fixture-session"};
  else if (m.method === "session/resume") {
    if (process.env.TEST_BLOCK_RESUME) return;
    if (process.env.TEST_FAIL_RESUME) {
      process.stdout.write(JSON.stringify({jsonrpc: "2.0", id: m.id,
        error: {code: -32603, message: "injected resume failure"}}) + "\n");
      return;
    }
    result = {};
  } else if (m.method === "session/prompt") result = {stopReason: "end_turn",
    usage: {totalTokens: 13, inputTokens: 10, outputTokens: 3}};
  else result = {};
  process.stdout.write(JSON.stringify({jsonrpc: "2.0", id: m.id, result}) + "\n");
});
