import { isValidElement, type ReactElement } from "react";
import { expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  selected: { o: { id: "o", version: 1 }, f: { id: "f", name: "文件" } },
  pendingActions: {
    "/admin/binding-tasks/f/complete": true,
    "/admin/orders/o/pack": true,
  },
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: (initial: unknown) => [
    initial === null ? state.selected : initial,
    () => {},
  ],
}));
vi.mock("../src/lib/store", () => ({
  ADMIN: "/manage",
  DEMO: true,
  useStore: () => ({
    data: {
      orders: [
        {
          id: "o",
          status: "PENDING_PRINT",
          files: [],
          packingStatus: "PENDING",
        },
      ],
      buildings: [],
    },
    pendingActions: state.pendingActions,
    act: () => {},
    toast: () => {},
  }),
}));
import { BindingCenter, PackingCenter } from "../src/admin/production";
function find(
  node: unknown,
  label: string,
): ReactElement<{ busy?: boolean; children?: unknown }> | undefined {
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = find(item, label);
      if (found) return found;
    }
  }
  if (isValidElement<{ busy?: boolean; children?: unknown }>(node)) {
    if (node.props.children === label) return node;
    return find(node.props.children, label);
  }
}
it("ties binding confirmation to its exact pending mutation", () => {
  expect(find(BindingCenter(), "确认装订完成")?.props.busy).toBe(true);
});
it("ties packing confirmation to its exact pending mutation", () => {
  const root = PackingCenter();
  function busy(node: unknown): boolean {
    if (Array.isArray(node)) return node.some(busy);
    if (isValidElement<{ busy?: boolean; children?: unknown }>(node))
      return node.props.busy === true || busy(node.props.children);
    return false;
  }
  expect(busy(root)).toBe(true);
});
