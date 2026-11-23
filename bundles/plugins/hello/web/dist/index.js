// The hello plugin's web part: a right-hand menu row that opens a page with two tabs.
// Hand-written so the bundled fixture needs no build step. A real plugin builds this file into web/dist/
// with Vite and marks react, react-dom, react/jsx-runtime and @kollab/plugin external, so it runs on the
// app's own React (docs/specs/plugins.md, "Web plugin API").
import { createElement as h, useState } from "react";
import { definePlugin } from "@kollab/plugin";

function Greeting({ k }) {
  const [count, setCount] = useState(0);
  return h(
    "div",
    { className: "hello-plugin" },
    h("p", null, "Hello from a plugin. This page is drawn by the hello plugin's own code."),
    h(k.ui.Button, { type: "button", variant: "outline", size: "sm", onClick: () => setCount(count + 1) }, `Clicked ${count} Times`),
  );
}

function About() {
  return h(
    "div",
    { className: "hello-plugin" },
    h("p", null, "A plugin can add a row to this menu, a page like this one, chat tabs, dialogs and notices."),
  );
}

export default definePlugin((k) => {
  k.page({
    id: "hello",
    title: "Hello",
    tabs: [
      { id: "greeting", label: "Greeting", render: Greeting },
      { id: "about", label: "About", render: About },
    ],
  });
});
