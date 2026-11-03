import { expandRanges } from "./domain";
import type { PrintFile, Segment } from "./types";

export function isSinglePageSegment(segment: Segment, total: number) {
  try {
    return expandRanges(segment.range, total).length === 1;
  } catch {
    return false;
  }
}

export function normalizePrintFile(file: PrintFile): PrintFile {
  if (file.kind !== "DOCUMENT") return file;
  const segments = file.segments.map((segment) =>
    isSinglePageSegment(segment, file.pages) &&
    (segment.sides !== "SINGLE" || segment.flip !== "NONE")
      ? { ...segment, sides: "SINGLE" as const, flip: "NONE" as const }
      : segment,
  );
  if (segments.every((segment, index) => segment === file.segments[index]))
    return file;
  return {
    ...file,
    segments,
    artifactId: undefined,
    sha256: undefined,
    finalArtifacts: undefined,
  };
}

export function pageAssignments(file: Pick<PrintFile, "segments" | "pages">) {
  const assignments = new Map<number, number[]>();
  file.segments.forEach((segment, index) => {
    try {
      for (const page of expandRanges(segment.range, file.pages))
        assignments.set(page, [...(assignments.get(page) || []), index + 1]);
    } catch {
      // Incomplete range input remains visible and is validated by fileErrors.
    }
  });
  return assignments;
}

export function updatePrintSegment(
  file: PrintFile,
  id: string,
  patch: Partial<Segment>,
): PrintFile {
  const next = normalizePrintFile({
    ...file,
    segments: file.segments.map((segment) =>
      segment.id === id ? { ...segment, ...patch } : segment,
    ),
    artifactId: undefined,
    sha256: undefined,
    finalArtifacts: undefined,
  });
  const assigned = pageAssignments(next);
  return {
    ...next,
    excludedPages: next.excludedPages.filter((page) => !assigned.has(page)),
  };
}

export function pageSelectionLabel(pages: number[]) {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  const ranges: string[] = [];
  for (let index = 0; index < sorted.length; index++) {
    const start = sorted[index];
    let end = start;
    while (sorted[index + 1] === end + 1) end = sorted[++index];
    ranges.push(start === end ? `${start}` : `${start}–${end}`);
  }
  return ranges.join("、");
}
