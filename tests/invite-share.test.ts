import { describe, expect, it } from "vitest";
import {
  inviteRegistrationUrl,
  inviteShareText,
} from "../src/lib/invite-share";
describe("邀请注册链接", () => {
  it("文案包含当前站点的注册链接并编码邀请码", () => {
    const message = inviteShareText(
      "https://print.example",
      "hello&你好",
      "一起打印",
    );
    const url = new URL(message.split("注册链接：")[1]);
    expect(url.origin).toBe("https://print.example");
    expect(url.pathname).toBe("/u/account/register");
    expect(url.searchParams.get("invite")).toBe("hello&你好");
    expect(message).toContain("一起打印");
  });
  it("没有服务端分享文案仍可复制带链接的邀请", () => {
    expect(inviteShareText("http://localhost:8080", "CODE")).toContain(
      "http://localhost:8080/u/account/register?invite=CODE",
    );
  });
  it("不生成空邀请码或非法地址", () => {
    expect(() => inviteRegistrationUrl("https://print.example", " ")).toThrow();
    expect(() =>
      inviteRegistrationUrl("file:///tmp/index.html", "CODE"),
    ).toThrow();
  });
});
