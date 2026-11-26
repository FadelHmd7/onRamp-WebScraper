import type { Block } from "../api";

/** A document's content in page order, each block as the element it was. */
export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <div className="blocks">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "heading": {
      // The page's own h1 is the document title; nest everything one level down.
      const level = Math.min(block.level + 1, 6);
      const Tag = `h${level}` as "h2" | "h3" | "h4" | "h5" | "h6";
      return <Tag>{block.text}</Tag>;
    }
    case "paragraph":
      return <p>{block.text}</p>;
    case "list": {
      const items = block.items.map((item, i) => <li key={i}>{item}</li>);
      return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
    }
    case "table":
      return (
        <div className="scroll">
          <table className="content-table">
            {block.caption && <caption>{block.caption}</caption>}
            {block.header.length > 0 && (
              <thead>
                <tr>
                  {block.header.map((cell, i) => (
                    <th key={i} scope="col">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) =>
                    c === 0 && block.row_headers ? (
                      <th key={c} scope="row">
                        {cell}
                      </th>
                    ) : (
                      <td key={c}>{cell}</td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "code":
      return (
        <pre>
          <code>{block.text}</code>
        </pre>
      );
    case "quote":
      return <blockquote>{block.text}</blockquote>;
  }
}
