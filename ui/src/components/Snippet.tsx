import type { ReactNode } from "react";

/**
 * A search snippet: escaped text with <mark> around the matches.
 *
 * The API already escapes the text, but the UI does not rely on that: the
 * snippet is parsed in an inert document (scripts never run, images never
 * load) and only text and <mark> become React elements. Any other tag is
 * reduced to its text, so nothing in a snippet can ever become markup.
 */
export function Snippet({ html }: { html: string }) {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const parts: ReactNode[] = [];
  doc.body.childNodes.forEach((node, index) => {
    if (node.nodeType === Node.ELEMENT_NODE && (node as Element).tagName === "MARK") {
      parts.push(<mark key={index}>{node.textContent}</mark>);
    } else {
      parts.push(node.textContent ?? "");
    }
  });
  return <p className="snippet">{parts}</p>;
}
