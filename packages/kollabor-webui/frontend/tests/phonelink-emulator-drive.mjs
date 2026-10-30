// The phone app in the Android emulator, driven like a person: paste the PhoneLink link, approve it on the
// computer, wait for the web UI, then let the web UI ask for the microphone and answer Android's prompt.
// Run by tests/live/phonelink_emulator.sh. Args: <adb serial> <link> <control url> <screenshot dir>.
import { execFileSync } from "node:child_process";
import { _android as android } from "playwright-core";

const [serial, link, control, out] = process.argv.slice(2);
const device = (await android.devices()).find((d) => d.serial() === serial);
const webview = await device.webView({ pkg: "ai.kollabor.kollab" });
const page = await webview.page();
const failed = [];
const shot = async (name) => {
  await page.screenshot({ path: `${out}/${name}.png` }).catch((e) => console.log(`screenshot ${name} failed: ${e.message}`));
  console.log(`${name}: ${page.url()}`);
};
const step = async (what, run) => {
  try {
    await run();
  } catch (error) {
    failed.push(what);
    console.log(`${what} failed: ${String(error.message).split("\n")[0]}`);
  }
};

await shot("1-link-screen");
// A link whose computer the phone cannot reach comes back to the paste screen with the reason.
await step("say when the computer cannot be reached", async () => {
  await page.getByRole("button", { name: "Paste Link Instead" }).click();
  await page.locator("#link").fill(link.replace(/:\d+\//, ":9/"));
  await page.getByRole("button", { name: "Link This Phone" }).click();
  await page.getByText("Kollab can't reach relay.localhost:9.").waitFor({ timeout: 20000 });
  await shot("1b-cannot-reach");
});
await step("paste the link", async () => {
  await page.locator("#link").fill(link);
  await shot("2-pasted");
  await page.getByRole("button", { name: "Link This Phone" }).click();
});
await step("open the relay's pairing page", () => page.waitForURL(/\/relay\/v1\/phone\//, { timeout: 30000 }));
await step("show the pairing code", () => page.getByText("Your Computer's Fingerprint").waitFor({ timeout: 30000 }));
await shot("3-pairing");
await step("approve on the computer", async () => {
  for (let i = 0; i < 45; i++) {
    const reply = await fetch(`${control}/approve`, { method: "POST" });
    if (reply.ok) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("nothing pending after 45 s");
});
// The loader also renders into #root; only the web UI has a textarea (its composer).
await step("open the web UI", () => page.locator("textarea").first().waitFor({ timeout: 60000 }));
await shot("4-web-ui");

// The web UI asks for the microphone; Android shows its own prompt, answered here as a person would.
await page.evaluate(() => {
  window.__mic = navigator.mediaDevices.getUserMedia({ audio: true }).then(
    (stream) => {
      const track = stream.getAudioTracks()[0];
      const state = track.readyState;
      track.stop();
      return state;
    },
    (error) => `${error.name}: ${error.message}`
  );
});
await step("answer Android's microphone prompt", async () => {
  for (let i = 0; i < 20; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    execFileSync("adb", ["shell", "uiautomator", "dump", "/sdcard/ui.xml"], { stdio: "ignore" });
    const xml = execFileSync("adb", ["shell", "cat", "/sdcard/ui.xml"]).toString();
    const button = xml.match(/text="While using the app"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/);
    if (button) {
      execFileSync("sh", ["-c", `adb exec-out screencap -p > "${out}/5-microphone-prompt.png"`]);
      const [x1, y1, x2, y2] = button.slice(1).map(Number);
      execFileSync("adb", ["shell", "input", "tap", String((x1 + x2) >> 1), String((y1 + y2) >> 1)]);
      return;
    }
  }
  throw new Error("no prompt in 20 s");
});
// An unanswered prompt never settles getUserMedia: give up after 10 s so the run still ends and fails.
const microphone = await page.evaluate(() =>
  Promise.race([window.__mic, new Promise((resolve) => setTimeout(() => resolve("no answer in 10 s"), 10000))])
);
console.log(`microphone: ${microphone}`);
if (microphone !== "live") failed.push("record from the microphone");
await shot("6-after-microphone");

const status = await (await fetch(`${control}/status`)).json();
const phone = status.phones?.[0] ?? {};
console.log(`computer: linked ${phone.linked}, connected ${phone.connected}`);
if (!phone.linked || !phone.connected) failed.push("the computer lists the phone as linked and connected");
await device.close();
console.log(failed.length ? `FAILED: ${failed.join("; ")}` : "passed: linked, web UI, microphone prompt, live microphone");
process.exit(failed.length ? 1 : 0);
