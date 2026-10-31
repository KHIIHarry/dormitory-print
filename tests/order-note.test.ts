import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { orderNoteSchema } from "../src/customer/checkout";

describe("订单备注", () => {
  it("允许留空并去掉首尾空白，保留中间换行", () => {
    expect(orderNoteSchema.parse("   ")).toBe("");
    expect(orderNoteSchema.parse("  第一份单独整理\n第二份放在一起  ")).toBe(
      "第一份单独整理\n第二份放在一起",
    );
  });
  it("最多接受30字，超出时给出明确错误", () => {
    expect(orderNoteSchema.safeParse("字".repeat(30)).success).toBe(true);
    const result = orderNoteSchema.safeParse("字".repeat(31));
    expect(result.success).toBe(false);
    if (!result.success)
      expect(result.error.issues[0].message).toBe("订单备注最多 30 字");
  });
  it("创建和修订接口均允许省略备注，并限制同样的长度", () => {
    const contract = JSON.parse(readFileSync("docs/openapi.yaml", "utf8"));
    for (const name of ["OrderSubmit", "RevisionSubmit"]) {
      const schema = contract.components.schemas[name];
      expect(schema.properties.customerNote).toMatchObject({
        type: "string",
        maxLength: 30,
      });
      expect(schema.required).not.toContain("customerNote");
    }
  });
});
