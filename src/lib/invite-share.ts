/** Registration links always point to this app and carry the inviter's code. */
export function inviteRegistrationUrl(
  origin: string,
  inviteCode: string,
): string {
  const url = new URL("/u/account/register", origin);
  if (!["http:", "https:"].includes(url.protocol))
    throw new Error("注册链接地址无效");
  if (!inviteCode.trim()) throw new Error("邀请码暂不可用");
  url.searchParams.set("invite", inviteCode.trim());
  return url.href;
}
export function inviteShareText(
  origin: string,
  inviteCode: string,
  introduction?: string,
): string {
  return `${introduction?.trim() || "邀请你使用 DormPrint 打印，点击下方链接注册即可自动填写我的邀请码。"}\n\n注册链接：${inviteRegistrationUrl(origin, inviteCode)}`;
}
