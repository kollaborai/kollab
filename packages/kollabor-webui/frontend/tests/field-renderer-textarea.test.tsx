import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import type { PanelField } from "../src/api";
import { FieldRenderer } from "../src/components/panels/FieldRenderer";

const field: PanelField = {
  path: "voice_prompt",
  type: "textarea",
  label: "Instructions",
  value: "line one\nline two",
  editable: true,
};

test("a textarea field renders a multi-line box holding its value", () => {
  const html = renderToStaticMarkup(
    <FieldRenderer field={field} draft={{}} onChange={() => {}} />,
  );
  expect(html).toContain("<textarea");
  expect(html).toContain("line one\nline two");
  expect(html).not.toContain('type="text"');
});
