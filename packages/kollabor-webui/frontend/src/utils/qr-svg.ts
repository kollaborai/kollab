/** Preserve Segno's full drawing when CSS resizes an inline QR SVG. */
export function responsiveQrSvg(markup: string): string {
  return markup.replace(/<svg\b[^>]*>/, (tag) => {
    if (/\sviewBox\s*=/.test(tag)) return tag;
    const dimension = (name: "width" | "height") =>
      tag.match(new RegExp(`\\s${name}\\s*=\\s*["']([0-9]+(?:\\.[0-9]+)?)(?:px)?["']`))?.[1];
    const width = dimension("width");
    const height = dimension("height");
    if (!width || !height || Number(width) <= 0 || Number(height) <= 0)
      return tag;
    return tag.replace(/>$/, ` viewBox="0 0 ${width} ${height}">`);
  });
}
