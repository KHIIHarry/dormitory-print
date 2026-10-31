import { z } from "zod";
import { expandRanges, productionCount, pricingErrors } from "../lib/domain";
import type { Config, PrintFile } from "../lib/types";
import type { Artifact } from "../lib/artifacts";
import type { PriceLine, Quote } from "../growth/types";

export const orderNoteSchema = z.string().trim().max(30, "订单备注最多 30 字");

/** Development-only input lines; production receives authoritative lines from its preparation. */
export function demoPriceLines(
  files: PrintFile[],
  config: Config,
): PriceLine[] {
  const lines: PriceLine[] = [];
  for (const file of files) {
    const errors = pricingErrors(file, config);
    if (errors.length) throw new Error(errors.join("；"));
    if (file.kind === "PHOTO") {
      const price = config.prices.photo!;
      lines.push({
        key: `${file.id}:photo`,
        relatedId: file.id,
        unit: "SHEET",
        quantity: file.photo.copies,
        unitPriceCents: price,
        amountCents: price * file.photo.copies,
        service: "PHOTO",
      });
    } else {
      for (const segment of file.segments) {
        const count = productionCount(
          expandRanges(segment.range, file.pages).length,
          segment.copies,
          segment.sides,
        ).sheets;
        const key =
          segment.colorMode === "BW"
            ? segment.sides === "SINGLE"
              ? "bwSingle"
              : "bwDuplex"
            : segment.sides === "SINGLE"
              ? "colorSingle"
              : "colorDuplex";
        const price = config.prices[key]!;
        lines.push({
          key: `${file.id}:${segment.id}`,
          relatedId: segment.id,
          unit: "SHEET",
          quantity: count,
          unitPriceCents: price,
          amountCents: count * price,
          service: "A4",
        });
      }
      if (file.bindingType !== "NONE") {
        const quantity = file.segments[0]?.copies || 1,
          price =
            config.prices[file.bindingType === "CORNER" ? "corner" : "side"]!;
        lines.push({
          key: `${file.id}:binding`,
          relatedId: file.id,
          unit: "BOOKLET",
          quantity,
          unitPriceCents: price,
          amountCents: quantity * price,
          service: "BINDING",
        });
      }
    }
  }
  for (const line of lines)
    if (
      ![line.quantity, line.unitPriceCents, line.amountCents].every(
        (n) => Number.isSafeInteger(n) && n >= 0,
      )
    )
      throw new Error("价格行必须为非负整数分");
  return lines;
}
export function sameArtifactAcks(
  previous: Artifact[],
  next: Artifact[],
  acks: string[],
) {
  return acks.filter((id) =>
    next.some(
      (a) =>
        a.id === id &&
        previous.some((p) => p.id === id && p.sha256 === a.sha256),
    ),
  );
}
export function quoteIsCurrent(quote: Quote | null, time = Date.now()) {
  return (
    !!quote &&
    Number.isFinite(Date.parse(quote.expiresAt)) &&
    Date.parse(quote.expiresAt) > time
  );
}
export function checkoutSignature(files: PrintFile[], orderId?: string) {
  return JSON.stringify({
    orderId,
    files: files.map((f) => ({
      id: f.id,
      assetId: f.assetId,
      kind: f.kind,
      imagePrintVersion: f.imagePrintVersion,
      pages: f.pages,
      segments: f.segments,
      excludedPages: f.excludedPages,
      bindingType: f.bindingType,
      photo: f.photo,
    })),
  });
}

export function canAcknowledgeArtifact(
  artifact: Pick<Artifact, "id" | "sha256"> | null,
  current: Pick<Artifact, "id" | "sha256">[],
  ready: boolean,
) {
  return (
    ready &&
    !!artifact &&
    current.some((a) => a.id === artifact.id && a.sha256 === artifact.sha256)
  );
}
export function allArtifactsAcknowledged(
  artifacts: Pick<Artifact, "id">[],
  acks: string[],
) {
  return artifacts.length > 0 && artifacts.every((a) => acks.includes(a.id));
}
