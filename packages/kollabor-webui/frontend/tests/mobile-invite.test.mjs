import { describe, expect, it } from "vitest";
import { isDirectory, launchDirectory, parseInvite, PHONE_PATH } from "../../../../desktop/mobile/invite.js";

const NOW = 1_800_000_000;
const ID = "a".repeat(32);
const KEY = "b".repeat(64);

/** A PhoneLink invite link; fields in `over` replace the valid defaults. */
function link(over = {}, base = "https://kollabor.ai/relay/v1/phone/") {
  const params = { v: "1", id: ID, room: KEY, host: KEY, expires: String(NOW + 300), ...over };
  return base + "#" + new URLSearchParams(params);
}

describe("parseInvite", () => {
  it("accepts a fresh invite", () => {
    expect(parseInvite(link(), NOW)).toEqual({
      url: link(),
      directory: "https://kollabor.ai/relay/v1/phone/",
    });
  });

  it("trims surrounding spaces and a newline", () => {
    expect(parseInvite(`\u00a0${link()}\n`, NOW)).toEqual({
      url: link(),
      directory: "https://kollabor.ai/relay/v1/phone/",
    });
  });

  it("keeps the port in the directory", () => {
    const base = "https://relay.example.com:8443/relay/v1/phone/";
    expect(parseInvite(link({}, base), NOW)).toEqual({ url: link({}, base), directory: base });
  });

  it("rejects text that is not a URL", () => {
    expect(parseInvite("not a url", NOW)).toBeNull();
  });

  it("rejects empty text", () => {
    expect(parseInvite("", NOW)).toBeNull();
  });

  it("rejects http", () => {
    expect(parseInvite(link({}, "http://kollabor.ai/relay/v1/phone/"), NOW)).toBeNull();
  });

  it("rejects a path without the trailing slash", () => {
    expect(parseInvite(link({}, "https://kollabor.ai/relay/v1/phone"), NOW)).toBeNull();
  });

  it("rejects a query before the fragment", () => {
    expect(parseInvite(link({}, "https://kollabor.ai/relay/v1/phone/?x=1"), NOW)).toBeNull();
    expect(parseInvite(link({}, "https://kollabor.ai/relay/v1/phone/?"), NOW)).toBeNull();
  });

  it("rejects credentials in the URL", () => {
    expect(parseInvite(link({}, "https://u:p@kollabor.ai/relay/v1/phone/"), NOW)).toBeNull();
  });

  it("rejects a version other than 1", () => {
    expect(parseInvite(link({ v: "2" }), NOW)).toBeNull();
  });

  it("rejects an empty room key", () => {
    expect(parseInvite(link({ room: "" }), NOW)).toBeNull();
  });

  it("rejects an id that is not 32 hex characters", () => {
    expect(parseInvite(link({ id: "a".repeat(31) }), NOW)).toBeNull();
    expect(parseInvite(link({ id: "g".repeat(32) }), NOW)).toBeNull();
  });

  it("rejects an uppercase host key", () => {
    expect(parseInvite(link({ host: "B".repeat(64) }), NOW)).toBeNull();
  });

  it("rejects an expiry outside the next 330 seconds", () => {
    expect(parseInvite(link({ expires: String(NOW) }), NOW)).toBeNull();
    expect(parseInvite(link({ expires: String(NOW + 331) }), NOW)).toBeNull();
  });
});

describe("isDirectory", () => {
  it("accepts only the bare https directory", () => {
    const directory = "https://kollabor.ai" + PHONE_PATH;
    expect(isDirectory(directory)).toBe(true);
    expect(isDirectory(directory + "#x")).toBe(false);
    expect(isDirectory(directory + "?x")).toBe(false);
    expect(isDirectory("http://kollabor.ai" + PHONE_PATH)).toBe(false);
    expect(isDirectory("https://kollabor.ai/other/")).toBe(false);
    expect(isDirectory("garbage")).toBe(false);
  });
});

describe("expiry window edges", () => {
  it("accepts an invite that expires in 1 s or in exactly 330 s", () => {
    expect(parseInvite(link({ expires: String(NOW + 1) }), NOW)).not.toBeNull();
    expect(parseInvite(link({ expires: String(NOW + 330) }), NOW)).not.toBeNull();
  });
});

describe("launchDirectory", () => {
  const saved = "https://kollabor.ai" + PHONE_PATH;
  it("opens a valid saved directory", () => {
    expect(launchDirectory(saved, "")).toBe(saved);
  });
  it("never opens it with ?relink", () => {
    expect(launchDirectory(saved, "?relink")).toBeNull();
  });
  it("ignores nothing saved or an invalid saved value", () => {
    expect(launchDirectory(null, "")).toBeNull();
    expect(launchDirectory("https://evil.example/other/", "")).toBeNull();
  });
});
