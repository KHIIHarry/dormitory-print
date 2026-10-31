import { expect, it } from "vitest";
import { createRouteScrollMemory } from "../src/lib/route-scroll";
it("restores each history entry independently on POP and starts new navigation at the top", () => {
  const memory = createRouteScrollMemory();
  memory.save("list-a", 410);
  memory.save("detail", 80);
  expect(memory.position("list-b", "PUSH")).toBe(0);
  expect(memory.position("list-a", "POP")).toBe(410);
  expect(memory.position("detail", "POP")).toBe(80);
  expect(memory.position("missing", "POP")).toBe(0);
});
it("does not share scroll positions between separate app lifetimes", () => {
  const memory = createRouteScrollMemory();
  memory.save("entry", 200);
  expect(createRouteScrollMemory().position("entry", "POP")).toBe(0);
});
