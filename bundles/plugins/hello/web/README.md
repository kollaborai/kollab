Hello web example

Run `npm install`, then edit `src/index.js` and `src/index.css`. Run
`npm run build` for a production bundle or `npm run dev` to rebuild on changes.
Vite keeps React and `@kollab/plugin` external so the host supplies both. Commit
both source and dist. The SDK declarations in `src/kollab-plugin.d.ts` are
generated from the host by `frontend/scripts/generate-plugin-types.mjs`.

The example registers a session view, menu entry and page, modal, custom settings,
slash command, tagged-text renderer and notice. Contribution IDs are declared in
`../kollab-plugin.json`. Use the Hello View to try the counter and dialog, the
Hello Page menu entry to open its page, and `/hello-web name` in chat.
