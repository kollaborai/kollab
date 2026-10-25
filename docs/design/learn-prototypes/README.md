# Learn concepts

Two review prototypes for a compact Learn popout. Neither content direction has
been selected or implemented in the product. The first reference-browser and
task-card concepts were rejected; these explore different interactions.

Open `index.html` through a local static server to compare the two views. For
example, from this directory run `python3 -m http.server 8092 --bind 127.0.0.1`.
The top tabs keep each example's state while switching views.

## Walkthrough

An interactive miniature of Kollab teaches Session Options through the controls
used in the app: Agent, Type, Model, and Workspace. Start Session takes a snapshot
of the example choices; later choices apply to the next example session. The
composer previews an explicitly labeled example user message. It does not display
a fabricated agent reply. Reference topics remain available through Topics and
contextual links.

The popout is 620px tall when there is room. On narrow screens its internal panes
scroll within the available viewport.

## System map

A network contains devices, project workspaces, and agents. Selecting an object
updates the context pane and highlights its relationships. Messaging adds a
directional example path; Swap direction changes the sender and recipient.
Agent handles and roles remain distinct. A Knocks address belongs to the device
connection within its workspace and network, and is shared by agents on that
connection. The diagram presents its format rather than an invented live address.

At 760px and below the inspector moves beneath the map. At 600px and below the
devices stack. Both panes scroll internally when needed.

## Boundaries and review evidence

- All actions use in-memory examples. There are no backend calls, messages,
  sessions, account actions, credential actions, or persisted learning state.
- The local avatar captures are product references; their PNG files are unchanged.
  The K mark, system type, neutral surfaces, and control arrangements follow the
  existing Web UI. No external fonts, scripts, images, or services are required.
- Both concepts were inspected in the browser at 390px, 820px, and 1100px. The
  walkthrough's agent/workspace selection, session snapshot, and example-message
  preview were exercised. Map selection, reference access, and direction swapping
  were exercised. These viewport checks do not establish physical touch behavior.
- Each concept contains all ten reference topics. Their data was checked against
  main's `packages/kollabor-webui/frontend/src/learn/catalog.ts` on 2026-10-21,
  including PhoneLink's same-browser bookmark wording.

Catalog SHA-256: `5fbbf8a2a4f2a2f488ec8f1b140097294893e3c41790e4c9dc6728122422cb23`.

Keep these review artifacts until a learning direction is selected and replaces
them. They are documentation prototypes, not another product implementation.
