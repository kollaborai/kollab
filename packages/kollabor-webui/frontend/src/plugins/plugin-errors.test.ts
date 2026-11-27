import { describe, expect, it } from "vitest";

import { containsSensitiveText, redactPluginError } from "./plugin-errors";

const suffix = "A1b2C3d4E5f6G7h8I9j0";
const awsAccessKeySuffix = "A1B2C3D4E5F6G7H8";

const tokenCases = [
  ["Slack xoxa token", `xoxa-${suffix}`],
  ["Slack xoxb token", `xoxb-${suffix}`],
  ["Slack xoxp token", `xoxp-${suffix}`],
  ["Slack xoxr token", `xoxr-${suffix}`],
  ["Slack xoxs token", `xoxs-${suffix}`],
  ["Slack app token", `xapp-${suffix}`],
  ["GitHub classic mixed token", "ghp_SuperSecretGitToken"],
  ["GitHub ghp token", `ghp_${suffix}`],
  ["GitHub gho token", `gho_${suffix}`],
  ["GitHub ghu token", `ghu_${suffix}`],
  ["GitHub ghs token", `ghs_${suffix}`],
  ["GitHub ghr token", `ghr_${suffix}`],
  ["GitHub fine-grained token", `github_pat_${suffix}`],
  ["GitLab token", `glpat-${suffix}`],
  ["Stripe sk live key", `sk_live_${suffix}`],
  ["Stripe sk test key", `sk_test_${suffix}`],
  ["Stripe rk live key", `rk_live_${suffix}`],
  ["Stripe rk test key", `rk_test_${suffix}`],
  ["Stripe pk live key", `pk_live_${suffix}`],
  ["Stripe pk test key", `pk_test_${suffix}`],
  ["AWS access key", `AKIA${awsAccessKeySuffix}`],
  ["AWS session key", `ASIA${awsAccessKeySuffix}`],
  ["Google API key", `AIza${"A".repeat(35)}`],
  ["npm token", `npm_${suffix}`],
  ["Hugging Face token", `hf_${suffix}`],
  ["JWT", `eyJ${"A".repeat(12)}.${"B".repeat(20)}.${"C".repeat(20)}`],
  ["legacy sk key", `sk-${suffix}`],
] as const;

const shortLookalikes = [
  ["Slack xoxb", `xoxb-${"A".repeat(19)}`],
  ["Slack app", `xapp-${"A".repeat(19)}`],
  ["GitHub ghp", `ghp_${"A".repeat(19)}`],
  ["GitHub fine-grained", `github_pat_${"A".repeat(19)}`],
  ["GitLab", `glpat-${"A".repeat(19)}`],
  ["Stripe sk live", `sk_live_${"A".repeat(15)}`],
  ["Stripe sk test", `sk_test_${"A".repeat(15)}`],
  ["Stripe rk live", `rk_live_${"A".repeat(15)}`],
  ["Stripe rk test", `rk_test_${"A".repeat(15)}`],
  ["AWS access key", `AKIA${"A".repeat(15)}`],
  ["AWS session key", `ASIA${"A".repeat(15)}`],
  ["Google API key", `AIza${"A".repeat(33)}`],
  ["npm", `npm_${"A".repeat(19)}`],
  ["Hugging Face", `hf_${"A".repeat(19)}`],
  ["JWT", `eyJ${"A".repeat(9)}.${"B".repeat(19)}.${"C".repeat(20)}`],
  ["legacy sk key", `sk-${"A".repeat(19)}`],
  ["Slack words", "xoxb-not-a-token"],
  ["ordinary help text", "hf_development"],
] as const;

describe("unlabelled plugin error credentials", () => {
  it.each(tokenCases)("redacts %s", (_label, token) => {
    expect(containsSensitiveText(token)).toBe(true);
    expect(redactPluginError(`Failure: ${token}`)).toBe("Failure: [redacted]");
  });

  it.each(shortLookalikes)("preserves short %s lookalikes", (_label, value) => {
    expect(containsSensitiveText(value)).toBe(false);
    expect(redactPluginError(value)).toBe(value);
  });
});
