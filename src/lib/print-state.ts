export function taskStatus(
  file: { printStatus: string; taskResults?: Record<string, string> },
  id: string,
) {
  return file.taskResults
    ? file.taskResults[id] || "PENDING"
    : file.printStatus;
}
export function aggregatePrintStatus(
  ids: string[],
  results: Record<string, string>,
) {
  const states = ids.map((id) => results[id] || "PENDING");
  return states.length && states.every((s) => s === "COMPLETED")
    ? "COMPLETED"
    : states.includes("ATTEMPTED")
      ? "ATTEMPTED"
      : states.includes("EXCEPTION")
        ? "EXCEPTION"
        : "PENDING";
}
