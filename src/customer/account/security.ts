/** Pure read: repeated React initializers observe the same fragment until commit. */
export function readFragmentToken(location: Pick<Location, "hash">): string {
  return (
    new URLSearchParams(location.hash.replace(/^#/, "")).get("token") || ""
  );
}

/** Read a secret only into memory and immediately remove the fragment from history. */
export function consumeFragment(
  location: Pick<Location, "hash" | "pathname" | "search">,
  replace: (url: string) => void,
): string {
  const token = readFragmentToken(location);
  if (location.hash) replace(location.pathname + location.search);
  return token;
}

/** Keep only pending operations: tokens are never persisted or logged. */
export function singleFlight<T>() {
  const pending = new Map<string, Promise<T>>();
  return (key: string, action: () => Promise<T>): Promise<T> => {
    const existing = pending.get(key);
    if (existing) return existing;
    const operation = action().finally(() => {
      pending.delete(key);
    });
    pending.set(key, operation);
    return operation;
  };
}
export function safeInviteeLabel(label: string) {
  if (!label || !/[*•…]/.test(label) || /\d{7,}/.test(label))
    return "已邀请用户";
  if (
    label.includes("@") &&
    !/^[^@\s]*[*•…][^@\s]*@[^@\s]*[*•…][^@\s]*$/.test(label)
  )
    return "已邀请用户";
  return label;
}
export function couponTab(status: string) {
  return status === "EXPIRED" || status === "REVOKED" ? "INACTIVE" : status;
}
export function safeCustomerReturn(value: string | null) {
  return value &&
    /^\/u(?:\/|\?|$)/.test(value) &&
    !/[\\\r\n]/.test(value) &&
    !value.includes("#") &&
    !/^\/u\/account\/(login|register|verify|forgot|reset)(?:[/?]|$)/.test(value)
    ? value
    : "/u/account";
}
