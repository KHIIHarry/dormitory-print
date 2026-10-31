import { createElement } from "react";
import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResourceState } from "../src/admin/growth/common";
import * as taskState from "../src/courier/task-state";
it("renders successful empty results after a resource has loaded", () => {
  const html = renderToStaticMarkup(
    createElement(ResourceState, {
      loading: false,
      hasData: true,
      empty: true,
      error: "",
      reload: () => {},
    }),
  );
  expect(html).toContain("暂无记录");
});
it("keeps the last empty result mounted during background refresh", () => {
  const html = renderToStaticMarkup(
    createElement(ResourceState, {
      loading: false,
      hasData: true,
      refreshing: true,
      empty: true,
      error: "",
      reload: () => {},
    }),
  );
  expect(html).toContain("暂无记录");
  expect(html).not.toContain("resource-skeleton");
});
it("scopes the entire proof form to order and identity while preserving same-scope inputs", () => {
  const proofScope = (
    taskState as unknown as {
      proofScope: (
        id: string | undefined,
        generation: number,
        role: string | null,
      ) => string;
    }
  ).proofScope;
  expect(proofScope).toBeTypeOf("function");
  const original = proofScope("order-a", 1, "COURIER");
  expect(proofScope("order-a", 1, "COURIER")).toBe(original);
  expect(proofScope("order-b", 1, "COURIER")).not.toBe(original);
  expect(proofScope("order-a", 2, "COURIER")).not.toBe(original);
  expect(proofScope("order-a", 1, "ADMIN")).not.toBe(original);
});
