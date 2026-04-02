/**
 * Normalizes text from LLMs so remark/GFM parsers recognize lists and blocks.
 * NBSP (U+00A0) after `*` breaks `* ` list detection; tabs in list-marker lines can also confuse the parser.
 */
export function cleanMarkdownForRender(rawText: string): string {
  if (!rawText) return "";
  return rawText
    .replace(/\u00A0/g, " ")
    .replace(/^\s*\*/gm, (match) => match.replace(/\t/g, "    "));
}
