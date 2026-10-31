import { expect, it } from "vitest";
import { planResources, loadIndependently } from "../src/lib/resources";
it("public and local workflow pages never load unrelated private orders", () => {
  for (const path of [
    "/u/",
    "/u/help",
    "/u/new",
    "/u/new/settings",
    "/u/account/login",
  ]) {
    expect(
      planResources(path, "", null, []).some((r) =>
        r.path.includes("/customer/orders"),
      ),
    ).toBe(false);
  }
  expect(
    planResources("/u/orders", "", null, []).some((r) => r.name === "orders"),
  ).toBe(true);
  expect(planResources("/manage/settings/mail", "", "ADMIN", ["*"])).toEqual(
    [],
  );
});
it("admin query identity includes its current search; account screens load no operational resources", () => {
  expect(
    planResources("/manage/orders", "?status=PENDING", "ADMIN", ["*"]).find(
      (r) => r.name === "orders",
    )?.path,
  ).toBe("/admin/orders?status=PENDING");
  expect(planResources("/courier/account", "", "COURIER", [])).toEqual([]);
});
it("commits independent results without waiting for a slower or failed sibling", async () => {
  let finish!: () => void;
  const slow = new Promise<void>((r) => (finish = r));
  const committed: string[] = [],
    failed: string[] = [];
  const task = loadIndependently(
    [
      { name: "config", path: "/config" },
      { name: "orders", path: "/orders" },
    ],
    async (r) => {
      if (r.name === "orders") {
        await slow;
        throw new Error("orders offline");
      }
      return "ready";
    },
    (r) => committed.push(r.name),
    (r) => failed.push(r.name),
  );
  await Promise.resolve();
  await Promise.resolve();
  expect(committed).toEqual(["config"]);
  expect(failed).toEqual([]);
  finish();
  await task;
  expect(failed).toEqual(["orders"]);
  expect(committed).toEqual(["config"]);
});

it("limited admins never prefetch operational resources outside their capabilities", () => {
  expect(planResources("/manage", "", "ADMIN", ["mail.manage"])).toEqual([]);
  expect(planResources("/manage/batches", "", "ADMIN", ["production.manage"])).toEqual([]);
  expect(planResources("/manage/trash", "", "ADMIN", ["mail.manage"])).toEqual([]);
  expect(planResources("/manage/staff", "", "ADMIN", ["staff.manage"]).map(r=>r.name)).toEqual(["staff"]);
});
