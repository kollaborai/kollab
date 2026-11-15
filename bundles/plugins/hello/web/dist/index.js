import e, { useState as t } from "react";
import { definePlugin as n } from "@kollab/plugin";
//#region src/index.js
function r({ k: n }) {
	let [r, i] = t(0);
	return e.createElement("section", { className: "hello-example" }, e.createElement("p", null, "Hello from your plugin!"), e.createElement("p", null, `Hello count: ${r}`), e.createElement(n.ui.Button, {
		type: "button",
		onClick: () => i((e) => e + 1)
	}, "Say Hello"), e.createElement(n.ui.Button, {
		type: "button",
		variant: "outline",
		onClick: () => n.navigate({ modal: "hello-dialog" })
	}, "Open Hello Dialog"));
}
function i() {
	return e.createElement("section", { className: "hello-example" }, e.createElement("p", null, "This page is provided by the Hello plugin."));
}
var a = n(function(t) {
	t.view({
		id: "hello-view",
		label: "Hello View",
		icon: "box",
		render: r
	}), t.menu("hello-page"), t.page({
		id: "hello-page",
		title: "Hello Page",
		tabs: [{
			id: "hello",
			label: "Hello",
			render: i
		}]
	}), t.modal({
		id: "hello-dialog",
		title: "Hello Dialog",
		tabs: [{
			id: "hello",
			label: "Hello",
			render: () => e.createElement("p", null, "Hello from the dialog.")
		}]
	}), t.settings({ render: () => e.createElement("p", null, "Hello plugin settings example.") }), t.command({
		name: "hello-web",
		description: "Say hello in this chat",
		run: (e) => t.toast(`Hello, ${e.trim() || "world"}!`)
	}), t.renderer({
		tag: "hello_note",
		render: ({ tag: t }) => e.createElement("span", null, `Hello note: ${t.content}`)
	}), t.notice({ text: "Hello plugin is ready." });
});
//#endregion
export { a as default };
