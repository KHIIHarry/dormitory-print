/** Private route positions live only for this mounted application. */
export function createRouteScrollMemory() {
  const positions = new Map<string, number>();
  return {
    save(key: string, y: number) {
      positions.set(key, Math.max(0, y));
    },
    position(key: string, action: string) {
      return action === "POP" ? positions.get(key) || 0 : 0;
    },
  };
}
