import type { Block } from "./api";

export interface Change {
  kind: "added" | "removed";
  text: string;
}

/**
 * Which blocks a new version added and which it removed, in page order.
 *
 * A longest-common-subsequence over the blocks' text: blocks in both versions,
 * in the same order, are unchanged; the rest were added or removed. An edited
 * paragraph shows as its old text removed and its new text added.
 */
export function diffBlocks(before: Block[], after: Block[]): Change[] {
  const a = before.map(blockText);
  const b = after.map(blockText);
  // lcs[i][j]: length of the longest common subsequence of a[i:] and b[j:]
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i]![j] =
        a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const changes: Change[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      i++;
      j++;
    } else if (i < a.length && (j >= b.length || lcs[i + 1]![j]! >= lcs[i]![j + 1]!)) {
      // On a tie, the removal comes first: an edit reads "old text, then new".
      changes.push({ kind: "removed", text: a[i++]! });
    } else {
      changes.push({ kind: "added", text: b[j++]! });
    }
  }
  return changes;
}

/** The plain text of a block, the same way the API indexes it. */
export function blockText(block: Block): string {
  switch (block.type) {
    case "list":
      return block.items.join("\n");
    case "table":
      return [block.caption ?? "", ...[block.header, ...block.rows].map((r) => r.join(" | "))]
        .filter(Boolean)
        .join("\n");
    default:
      return block.text;
  }
}
