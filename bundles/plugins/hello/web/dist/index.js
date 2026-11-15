// A no-build example: kollab supplies the same React instance as its web UI.
import React, { useState } from "react";

function HelloView({ k }) {
  const [count, setCount] = useState(0);
  return React.createElement("section", { className: "hello-example" },
    React.createElement("p", null, "Hello from your plugin!"),
    React.createElement("p", null, `Hello count: ${count}`),
    React.createElement(k.ui.Button, { type: "button", onClick: () => setCount(value => value + 1) }, "Say Hello"),
    React.createElement(k.ui.Button, { type: "button", variant: "outline", onClick: () => k.navigate({ modal: "hello-dialog" }) }, "Open Hello Dialog"));
}

function HelloPage() {
  return React.createElement("section", { className: "hello-example" },
    React.createElement("p", null, "This page is provided by the Hello plugin."));
}

// Every contribution ID is declared in kollab-plugin.json.
export default function activate(k) {
  k.view({ id: "hello-view", label: "Hello View", icon: "box", render: HelloView });
  k.menu("hello-page");
  k.page({ id: "hello-page", title: "Hello Page", tabs: [{ id: "hello", label: "Hello", render: HelloPage }] });
  k.modal({ id: "hello-dialog", title: "Hello Dialog", tabs: [{ id: "hello", label: "Hello",
    render: () => React.createElement("p", null, "Hello from the dialog.") }] });
  k.settings({ render: () => React.createElement("p", null, "Hello plugin settings example.") });
  k.command({ name: "hello-web", description: "Say hello in this chat", run: args => k.toast(`Hello, ${args.trim() || "world"}!`) });
  k.renderer({ tag: "hello_note", render: ({ tag }) => React.createElement("span", null, `Hello note: ${tag.content}`) });
  k.notice({ text: "Hello plugin is ready." });
}
