import { it, expect } from "vitest";
import { pricingErrors } from "../src/lib/domain";
import { createDemo, makeFile } from "../src/lib/demo";
it("unconfigured prices cannot masquerade as free service", () => {
  const config = createDemo().config;
  const file = makeFile("notes.pdf");
  config.prices.bwDuplex = null;
  expect(pricingErrors(file, config).length).toBeGreaterThan(0);
  config.prices.bwDuplex = 0;
  expect(pricingErrors(file, config)).toEqual([]);
});
it("binding remains blocked until D-04 is approved", () => {
  const config = createDemo().config;
  config.prices.corner = 20;
  const file = makeFile("notes.pdf");
  file.bindingType = "CORNER";
  expect(pricingErrors(file, config)).toContain("装订规则尚未定版，暂不可接单");
});
