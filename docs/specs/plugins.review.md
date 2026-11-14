# Review log: plugins for the web UI and the backend

How `plugins.md` changed under review. Each round is one commit on PR #1030, so `git diff` between two
round commits shows exactly what a round changed. Reviewers were read-only and did not write the spec.

| Round | Reviewers | Findings | Verdicts | Commit |
|---|---|---|---|---|
| 0 | role panel, 5 Haiku roles, run twice | 25, then 15 | all CHANGES | `e31c7de5` (first commit) |
| 1 | 12 Haiku lanes (3 security, 3 design, 3 data, 3 other), 1 Codex adversarial, 3 brutal Haiku critics | 66 + 4 + 24 | all FAIL | `6a358476` |
| 2 | 12 Haiku lanes, 1 Codex adversarial | about 70 + 1 | all FAIL | `860a949e` |
| 3 | 12 Haiku lanes, 1 Codex adversarial | 51 + 3 | all FAIL | the round 3 commit |

## Round 0: role panel

The first draft was not committed; the panel's findings are what changed it before `e31c7de5`.

- Security: the add sheet showed declared-only access (`runs gh`) as if enforced, and hid hooks, MCP
  servers and the code that runs in every agent. Unattended agent runs and plugin-chosen environment
  could leak `GITHUB_TOKEN`. There was no prompt-injection failure mode. A plugin token could update its
  own plugin. The install source could inject git options.
- SRE: disable and update left agent runs orphaned; engine restarts lost run state; there was no default
  spend cap.
- Writer: strings were missing (purge prompt, run states, cap reset), plurals broke at 1, and one action
  had two names.
- Owner proxy: the live proof could merge real PRs; the thread cap lived in two stores; a notice showed
  an issue number.
- Owner direction folded in: plugin settings are discovered, not hard-coded; `/config` and Settings →
  Plugins read one list (D12).
- Rejected: cutting chat cards, custom settings and the token cap (the owner asked for plugins that can
  add anything, and an uncapped loop once drained a week of usage).

## Round 1: twelve lanes at once

What each lane caught, and what changed:

- **Security, install and trust.** Update took no commit, so it could install code the sheet never
  showed; folders had no pin; refs could start with `-` and `release/1.2` did not parse. Changed: update
  is a job that confirms `{commit, manifest_sha256}`; folders run only through `dev`; refs use `#` and a
  checked character set (D7, D10).
- **Security, secrets and agents.** A prompt-injected stage agent could install a plugin with no click;
  agents could read `engine.token`; review stages are not read-only when they are command lines.
  Changed: management needs a person on this computer and refuses inside an agent's shell; token files
  join the agent file tools' protected paths; the sheet says pr-chain's coding agents use a shell without
  asking; command stages also lose `SSH_AUTH_SOCK`.
- **Security, web surface.** Any peer that reaches the web UI could read every file in a plugin folder,
  and an HTML file there would run on the web origin with the engine token. Changed: only `web/dist/`
  files of allowed types are served, never HTML or dotfiles. Management routes refuse remote callers.
  Codex added that page scripts can read the full engine token from `/config`; D1 now says so.
- **Design, copy.** Five phrases for one stopped loop, two names for the dev folder and for issue
  building, a broken singular. Changed: one state vocabulary, `Dev Folder`, `Build Issues Into PRs`,
  singular forms written out.
- **Design, flows.** Turn-off never reached open pages, a plugin tab could leave a blank main area,
  Retry re-imported the failed module, the phone modal was a card. Changed: `plugins-changed` events,
  chats fall back to Chat, Retry gets a new URL, phone modals fill the screen, 44 px touch targets.
- **Design, accessibility.** Mockup controls lacked roles and names, dim text failed contrast on fills,
  outside text had no escaping rule. Changed: the spec's UI rules (text never rendered as HTML, real
  controls, live regions) and the mockup fixes listed in the round commit.
- **Data, storage.** Plugin values in `config.json` would trip pr-chain's own config tripwire; no daily
  counter existed; dev and installed copies shared one data folder; `runs.json` had no atomic rule.
  Changed: installed values live in the plugin's data folder with one writer (D6); a per-day counter;
  `<id>@dev` data folders; atomic `runs.json` with results.
- **Data, parity.** pr-chain's code reaches outside its folder (`fold.py`, `land_pr.py`, prompts, rate
  tables) and its config lives in several files; there was no cutover. Changed: everything moves into the
  plugin, paths come from `KOLLAB_PLUGIN_ROOT` and `KOLLAB_PLUGIN_DATA`, and a cutover section stops the
  old loop before the new one starts.
- **Data, shapes.** Three settings shapes, two list shapes, a JSONC example in a `.json` file. Changed:
  one settings section shape (the `get_config_widgets()` one), one `PluginEntry`, a schema number, plain
  JSON with a key table.
- **Other, SRE.** The supervisor could adopt a stage child as the loop and start a second loop; caps
  died with the engine; exits during an outage were lost. Changed: a `run-service` wrapper writes its own
  pid file with a nonce and owns restarts, exit codes and logs; the daemon enforces run limits at the
  `/goal` dispatch gate (D4, D5).
- **Other, builder.** No render signature, no dev loop, steps 2 and 3 were not really parallel, the
  example used names that do not exist. Changed: components get `{k, session}`, `k.useApi`, a hello
  template with build and watch scripts, agent runs moved to step 4.
- **Other, owner rules.** The Board's three.js stage was missing; the owning-files table skipped
  `discovery.py` and `plugin_sdk.py`. Changed: both added.
- **Codex.** The problem statement was wrong: the loader already imports `~/.kollab/plugins` and
  `<cwd>/.kollab/plugins`. Changed: the statement, and the project-folder import is now bug #1035.

Rejected or deferred in round 1:

- Cut the view, modal, chat cards and the `/config` merge to a second spec: rejected, the owner asked for
  all of them.
- Have the engine parse every command stage's transcript for the cap: rejected; pr-chain already parses
  its transcripts and appends one usage line per stage.
- Draw the Issues tab and an 820 px frame: deferred; parity is proven against the running board, and
  the pages are fluid.
- Draw the off, failed, stopped and cap-reached states and the phone modal: due before build step 3.

Mockup fixes in this round: one `Build Issues Into PRs` switch on the Config tab, the add sheet's text
matches the spec, modal tabs carry tab roles, accent options limited to the app's own pair, a visible
focus ring, darker light-theme warning text, the gate copy says a comment sends a PR back to a fixer,
and the phone notice button is 44 px.

## Round 2: twelve lanes again, on the round 1 commit

| Reviewers | Findings | Verdicts |
|---|---|---|
| 12 Haiku lanes, 1 Codex adversarial | about 70 + 1 | all FAIL; 5 P0 |

What each lane caught, and what changed:

- **Security, the boundary that was not one.** Three lanes showed that round 1's promise ("agents cannot
  install plugins") was false: an agent with a shell can read `~/.kollab/engine.token` whatever the file
  tools block, and the management routes' "local caller" check passes every request the web UI proxies.
  Changed: the spec now says plainly that an agent with a shell can do anything the user can; the
  defaults carry the weight (`approval: "ask"`, unattended runs only with a declared grant the sheet
  shows); management routes are refused for remote callers at the web UI proxy, which sees the real
  client; agent runs get a scratch HOME (config through `KOLLAB_CONFIG_DIR`) and an allowlisted
  environment. An OS sandbox for agent shells is named as the real fix and filed as #1037.
- **Security, install integrity.** Confirm checked the manifest, not the code. Changed: the install is
  `git archive` of the commit, its tree hash shows on the sheet, and confirm and every load check it;
  URLs with credentials are refused; refs match `refs/heads` or `refs/tags` exactly; `.svg` is no longer
  served; the asset route sits above the app's catch-all.
- **Security, pr-chain's guard.** The current script does not give command stages a scratch HOME or
  strip `SSH_AUTH_SOCK`, so the spec's "keeps its guard" was untrue. Changed: the port adds both.
- **Data.** The import would copy worktrees and the `STOP` file, read the issue switch from a key that
  does not exist (it is a `NO-BUILD` file), keep old absolute paths in the ledger, and let a restart
  double-count usage. Changed: named sources and skip list, path rewrite, `NO-BUILD` mapping, one counter
  file with an offset and counted run ids, sibling lock files, atomic `STATE.json` writes in the port,
  values in `values.json` beside the declaration.
- **Design.** Menu rows existed only after the bundle ran, so `Failed to Load` had nothing to show on a
  fresh boot; Turn Off removed the plugin while its loop drained for 40 minutes; state mockups are
  missing. Changed: menu rows are declared in the manifest; a `Stopping` state keeps the row and page
  until the process exits; the status line now says state mockups are still owed before build step 3.
- **Other, SRE.** The 30 s tick would restart a process the user stopped; a killed wrapper could leave
  its child running while a second one started; services might start without the user's `PATH`.
  Changed: a desired state per process, a live recorded child blocks a new start, the wrapper gets the
  `PATH` captured at enable and `run:` programs resolve to absolute paths.
- **Other, builder.** pr-chain's repo, checkout, interpreter and owner list had no home; no secret could
  reach a background process; `dev` reloaded only the web bundle. Changed: those become pr-chain
  settings, services can name granted secrets, `dev` restarts services and remounts routes on change.
- **Codex.** pr-chain's routes would have become `/plugins/pr-chain/api/api/state`. Changed: the plugin
  router drops the old `/api` prefix.

Rejected in round 2:

- Move tab, modal, command and chat cards out of build step 3: rejected again, the owner asked for them.
- A one-time code from the page for every management call: rejected; an agent with the engine token can
  fetch anything the page can, so it would be a check that looks like a boundary and is not one.

## Round 3: the last of three

| Reviewers | Findings | Verdicts |
|---|---|---|
| 12 Haiku lanes, 1 Codex adversarial | 51 + 3 | all FAIL; 3 P0 |

What each lane caught, and what changed:

- **Data.** Round 2's rename left background processes reading `settings.json`, a file nothing writes
  (also found by Codex). The cutover dropped pr-chain's skip lists, so the new loop would pick PRs the
  owner had held back. The usage offset could not cross midnight. Changed: processes read `values.json`;
  `exclude.txt` and `exclude-issues.txt` are imported and read by the plugin's pick; a separate
  `usage.offset` with lines counted by their own date; `prs/**/wt/` skipped and `ui_seed` paths
  rewritten; install keeps `<id>.old` until `plugins.json` is written.
- **Schema.** The manifest declared the menu row and the SDK registered it again (also Codex); eight
  manifest keys had no definition; process names became file paths unchecked. Changed: one
  `contributes` block is the source for rows, pages, views, modals, slash commands and chat cards, and the
  bundle binds to its ids; every manifest key is defined; process names have a pattern.
- **Security.** Phones could save settings that set the interpreter and checkout; pr-chain's config
  writes change the commands its chains run; the asset route did not name its containment check; stage
  shells could still use the system git credential helper. Changed: settings saves and plugin routes
  marked `@ctx.local_only` refuse remote callers through the proxy and the PhoneLink bridge; `_safe_asset`
  and a script CSP; command stages get the agent-run allowlist plus `GIT_CONFIG_NOSYSTEM=1`; absent
  `approval` means ask; secrets never go to a run with a shell or network tool; the sheet now says the
  loop holds the token and the stage agents run as the user.
- **Design.** A stopped process showed Stop and no Start; the Failed to Load row had nothing to open; a
  plugin that froze the tab could only be escaped by editing the URL; the needs-you count was hover-only.
  Changed: buttons follow the state, a kollab-drawn panel opens from a failed row, a plugin whose last
  boot never finished is skipped with a notice, and the row text shows `4 · 1 needs you` everywhere.
- **SRE.** Two wrappers could run one process; agent-run daemons' fate across an engine restart was
  unstated; disable and update left old Python in running agents (Codex). Changed: the wrapper holds its
  lock for life; run daemons are detached and adopted or stopped by the next engine; Settings shows
  which agents still run the old version, with Restart Agents.
- **Builder and owner rules.** pr-chain's port waited on agent-run work its default path never uses; the
  cutover had no way back; the owner accounts would ship inside a plugin that may be public. Changed:
  pr-chain is built in step 3 on command-line stages and gains loadout stages in step 4; the old loop is
  kept stopped, not deleted, for 7 days; `owners` ships empty.

Rejected in round 3:

- A one-time code for install, confirm and enable (asked a third time): an agent holding the engine
  token can request the same sheet the page sees, so it would look like a wall and not be one. The
  terminal path already needs a real terminal, and #1037 is the actual fix.
- Rename `Build Issues Into PRs` to `Issue Intake`: kept the name, added help text that Off also stops
  scouting.
- Stop `/config` from returning the engine token to remote pages: existing behavior outside this spec,
  which states plainly that page scripts hold the token (D1).
- Mockup accessibility details (stage map as a list, chips as buttons, roving tab focus): fixed with the
  state mockups before build step 3; the spec already requires real controls and live regions.

## Where the rounds left it

Three rounds turned up 15 P0 and about 170 P1/P2 findings in all; every round's P0s are fixed or rejected
above with a reason. The design held across all three rounds: trusted plugins with an honest sheet, a
manifest that declares everything, a wrapper per background process, daemon-enforced agent limits, one
settings store per plugin, and pr-chain as the acceptance test. What moved was the truth of each claim
and the failure handling around it.
