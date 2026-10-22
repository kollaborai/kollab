import assert from "node:assert/strict";
import test from "node:test";
import { responsiveQrSvg } from "../src/utils/qr-svg.ts";

const drawing = '<path d="M16 16h28v28H16z"/></svg>';

test("resizing a QR preserves its full intrinsic frame and drawing", () => {
  for (const size of [244, 292, 372]) {
    const markup = `<svg width="${size}" height="${size}" class="segno">${drawing}`;
    const result = responsiveQrSvg(markup);
    assert.ok(result.includes(`viewBox="0 0 ${size} ${size}"`));
    assert.ok(result.endsWith(drawing));
    assert.equal(responsiveQrSvg(result), result);
  }
});

test("explicit view boxes are preserved", () => {
  const markup = `<svg viewBox="0 0 73 73" width="292" height="292">${drawing}`;
  assert.equal(responsiveQrSvg(markup), markup);
});

test("SVG dimension quoting, pixel units and whitespace retain their frame", () => {
  const markup = `<svg width = '292px' height = "292.5px">${drawing}`;
  assert.ok(responsiveQrSvg(markup).includes('viewBox="0 0 292 292.5"'));
});

test("missing, relative or empty dimensions do not invent a drawing frame", () => {
  for (const attributes of ['width="100%" height="292"', 'width="0" height="292"', 'width="292"']) {
    const markup = `<svg ${attributes}>${drawing}`;
    assert.equal(responsiveQrSvg(markup), markup);
  }
});
