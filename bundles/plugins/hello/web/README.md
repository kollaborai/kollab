Hello web example

Edit `src/index.js` and `src/index.css`, then run `node build.mjs` in this
directory. The example uses browser-ready JavaScript and Kollab's shared React,
so it needs no dependency installation or bundler. Commit both source and dist.

The example registers a session view, menu entry and page, modal, custom settings,
slash command, tagged-text renderer and notice. Contribution IDs are declared in
`../kollab-plugin.json`. Use the Hello View to try the counter and dialog, the
Hello Page menu entry to open its page, and `/hello-web name` in chat.
