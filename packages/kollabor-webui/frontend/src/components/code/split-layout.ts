import type { SplitNode } from "./editor-store";

function findPane(node: SplitNode, paneId: string): SplitNode | null {
  if (node.type === "leaf") return node.paneId === paneId ? node : null;
  return findPane(node.first, paneId) ?? findPane(node.second, paneId);
}

export function visibleSplitTree(tree: SplitNode, activePaneId: string, isNarrow: boolean): SplitNode {
  if (!isNarrow || tree.type === "leaf") return tree;
  return findPane(tree, activePaneId) ?? tree;
}
