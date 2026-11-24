// Kollab supplies the same React instance and SDK as its web UI.
import React, { useState } from "react";
import { definePlugin } from "@kollab/plugin";
import "./index.css";

/** @param {import("@kollab/plugin").PluginRenderProps} props */
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
export default definePlugin(function activate(k) {
  k.view({ id: "hello-view", label: "Hello View", icon: "box", render: HelloView });
  k.menu("hello-page");
  k.page({ id: "hello-page", title: "Hello Page", tabs: [{ id: "hello", label: "Hello", render: HelloPage }] });
  k.modal({ id: "hello-dialog", title: "Hello Dialog", tabs: [{ id: "hello", label: "Hello",
    render: () => React.createElement("p", null, "Hello from the dialog.") }] });
  k.notice({ text: "Hello plugin is ready." });
});
