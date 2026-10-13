/** Repair recognizable TeX commands decoded as JSON escapes, only inside math. */
function repairMathEscapes(content: string): string {
  return content
    .replace(/\t(imes|heta|ext(?:bf|it|rm)?|frac|o|anh)(?![A-Za-z])/g, '\\t$1')
    .replace(/\f(rac|orall)(?![A-Za-z])/g, '\\f$1')
    .replace(/\u0008(eta|ar|egin|oldsymbol|inom|ig(?:g|l|r)?)(?![A-Za-z])/g, '\\b$1')
    .replace(/\r(ight|angle|ho)(?![A-Za-z])/g, '\\r$1')
    .replace(/\n(abla|eq|u|otin)(?![A-Za-z])/g, '\\n$1');
}

function findMathEnd(text: string, delimiter: string, start: number): number {
  let index = text.indexOf(delimiter, start);
  while (index >= 0) {
    let slashes = 0;
    for (let position = index - 1; position >= 0 && text[position] === '\\'; position -= 1) slashes += 1;
    if (slashes % 2 === 0) return index;
    index = text.indexOf(delimiter, index + delimiter.length);
  }
  return -1;
}

/** Normalize TeX delimiters and JSON escapes while preserving Markdown code verbatim. */
export function normalizeMathDelimiters(text: string): string {
  let result = ''; let offset = 0;
  while (offset < text.length) {
    const remaining = text.slice(offset);
    if (offset === 0 || text[offset - 1] === '\n') {
      const fence = /^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)/.exec(remaining);
      if (fence) {
        const closing = new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}[ \\t]*(?:\\n|$)`, 'm');
        const end = closing.exec(remaining.slice(fence[0].length));
        const length = end ? fence[0].length + end.index + end[0].length : remaining.length;
        result += remaining.slice(0, length); offset += length; continue;
      }
    }
    if (text[offset] === '`') {
      const opening = /^`+/.exec(remaining)![0];
      const closing = new RegExp(`(?<!\x60)\x60{${opening.length}}(?!\x60)`).exec(remaining.slice(opening.length));
      if (closing) {
        const length = opening.length + closing.index + opening.length;
        result += remaining.slice(0, length); offset += length; continue;
      }
    }
    if (remaining.startsWith('\\\\')) { result += '\\\\'; offset += 2; continue; }
    if (remaining.startsWith('\\$')) { result += '\\$'; offset += 2; continue; }
    if (remaining.startsWith('$')) {
      const delimiter = remaining.startsWith('$$') ? '$$' : '$';
      const end = findMathEnd(remaining, delimiter, delimiter.length);
      if (end >= 0) {
        result += delimiter + repairMathEscapes(remaining.slice(delimiter.length, end)) + delimiter;
        offset += end + delimiter.length; continue;
      }
    }
    const display = remaining.startsWith('\\[');
    if (display || remaining.startsWith('\\(')) {
      const end = remaining.indexOf(display ? '\\]' : '\\)', 2);
      if (end >= 0) {
        const content = repairMathEscapes(remaining.slice(2, end));
        result += display ? `\n$$\n${content}\n$$\n` : `$${content}$`;
        offset += end + 2; continue;
      }
    }
    result += text[offset]; offset += 1;
  }
  return result;
}
