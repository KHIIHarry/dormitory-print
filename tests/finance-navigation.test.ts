import { expect, it } from "vitest";
import { ADMIN } from "../src/lib/api";
import { adminNavAllowed, adminResources } from "../src/admin/resources";
import { activeAdminItem, visibleAdminGroups } from "../src/admin/navigation";
import { CAPABILITY_CATALOG } from "../src/v1/admin-api";

it("requires dedicated finance permission and loads no unrelated private data", () => {
  const path = `${ADMIN}/finance`;
  expect(adminNavAllowed(path, ["finance.manage"])).toBe(true);
  expect(adminNavAllowed(path, ["payment.manage", "payroll.manage"])).toBe(
    false,
  );
  expect(adminResources(path, ["finance.manage"])).toEqual([]);
  expect(activeAdminItem(path)?.label).toBe("耗材与收支");
  const visible = visibleAdminGroups(["finance.manage"])
    .flatMap((g) => g.items)
    .map((i) => i.to);
  expect(visible).toContain(path);
  expect(visible).not.toContain(`${ADMIN}/orders`);
  expect(visible).not.toContain(`${ADMIN}/payroll`);
  expect(
    CAPABILITY_CATALOG.find((c) => c.key === "finance.manage")?.enforced,
  ).toBe(true);
});
