import { useEffect, useState, useRef } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  FileCheck2,
  MapPin,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { z } from "zod";
import { DEMO, useStore } from "../lib/store";
import { photoCrop, photoPaper } from "../lib/photo-layout";
import { parseRanges, pricingErrors } from "../lib/domain";
import { isUncertainSubmission, money, now } from "../lib/api";
import type { Order } from "../lib/types";
import type { Artifact } from "../lib/artifacts";
import {
  Button,
  ErrorState,
  Field,
  FileRow,
  Loading,
  Modal,
  Notice,
  PageHeading,
  Panel,
  PriceRows,
  Steps,
} from "../components/ui";
import { TaskStatus } from "../components/feedback";
import { CampusMap } from "../components/campus-map";
import { DocPreview } from "../components/doc-preview";
import { growthRequest, useGrowthResource } from "../growth/client";
import { useIdentity } from "../growth/identity";
import { assertQuote } from "../growth/pricing";
import type { Coupon, PromotionIntent, Quote } from "../growth/types";
import { CustomerAuthForm } from "./account";
import {
  checkoutSignature,
  canAcknowledgeArtifact,
  allArtifactsAcknowledged,
  orderNoteSchema,
  demoPriceLines,
  quoteIsCurrent,
  sameArtifactAcks,
} from "./checkout";
import "./checkout.css";
const contactSchema = z.object({
  nickname: z
    .string()
    .trim()
    .min(1, "请填写微信昵称")
    .max(40, "昵称最多 40 字"),
  phone: z.string().regex(/^1[3-9]\d{9}$/, "请输入正确的 11 位手机号"),
  building: z.string().min(1, "请选择目标楼栋"),
  slot: z.string().min(1, "请选择一个开放时段"),
  customerNote: orderNoteSchema,
});
type Preparation = {
  id: string;
  version: number;
  status: string;
  artifacts: Artifact[];
  quote?: Quote;
};
type CheckoutSnapshot = {
  preparationId: string;
  preparationVersion: number;
  artifacts: Artifact[];
  acks: string[];
  quote: Quote | null;
  generation: number;
  promotionIntent?: PromotionIntent;
  name: string;
  phone: string;
  building: string;
  slot: string;
  customerNote?: string;
};
export function Review() {
  const { draft, editingOrderId } = useStore();
  return (
    <ReviewContent
      key={checkoutSignature(draft, editingOrderId || undefined)}
    />
  );
}
function ReviewContent() {
  const {
    draft,
    data,
    resources,
    setData,
    editingOrderId,
    toast,
    checkoutMemory,
    setCheckoutMemory,
    setSavedAccessLinks,
  } = useStore();
  const configReady = DEMO || !!resources.config?.hasData;
  const configReadyRef = useRef(configReady);
  const readinessEpoch = useRef(0);
  if (configReadyRef.current !== configReady) {
    configReadyRef.current = configReady;
    readinessEpoch.current++;
  }
  const [contentReadiness, setContentReadiness] = useState(-1);
  const hasStarted = useRef(false);
  const identity = useIdentity(),
    nav = useNavigate();
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const originalRef = useRef(data.orders.find((o) => o.id === editingOrderId));
  const original = originalRef.current;
  const signature = checkoutSignature(draft, editingOrderId || undefined);
  const signatureRef = useRef(signature);
  signatureRef.current = signature;
  const saved = checkoutMemory[signature] as CheckoutSnapshot | undefined;
  const preparingId = useRef(saved?.preparationId || ""),
    prepVersion = useRef(saved?.preparationVersion || 1),
    prepState = useRef("");
  const mounted = useRef(true),
    sequence = useRef(0),
    quoteSequence = useRef(0),
    quoteLock = useRef(false),
    submissionLock = useRef(false),
    prepFlight = useRef<Promise<Preparation> | null>(null),
    preparationEpoch = useRef(0),
    restartAfterLogout = useRef(false);
  const [artifacts, setArtifacts] = useState<Artifact[]>(
      saved?.artifacts || [],
    ),
    [acks, setAcks] = useState<string[]>(saved?.acks || []);
  const artifactsRef = useRef(artifacts);
  artifactsRef.current = artifacts;
  const [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [quoteError, setQuoteError] = useState(""),
    [quoteBusy, setQuoteBusy] = useState(false);
  const [contentGeneration, setContentGeneration] = useState(
    identity.generation,
  );
  const [quote, setQuote] = useState<Quote | null>(
    saved?.generation === identity.generation ? saved.quote : null,
  );
  const [promotionIntent, setPromotionIntent] = useState<PromotionIntent>(
    saved?.promotionIntent || { selectionMode: "AUTO" },
  );
  const quoteRef = useRef(quote);
  quoteRef.current = quote;
  const [confirmedQuote, setConfirmedQuote] = useState(""),
    [preview, setPreview] = useState<Artifact | null>(null),
    [loginOpen, setLoginOpen] = useState(false),
    [adoptOpen, setAdoptOpen] = useState(false);
  const [name, setName] = useState(saved?.name || original?.nickname || ""),
    [phone, setPhone] = useState(saved?.phone || original?.phone || ""),
    [building, setBuilding] = useState(
      saved?.building || original?.locationId || "",
    ),
    [slot, setSlot] = useState(saved?.slot || original?.slotId || ""),
    [customerNote, setCustomerNote] = useState(
      saved?.customerNote ?? original?.customerNote ?? "",
    );
  const [previewIntent, setPreviewIntent] = useState<"VIEW" | "CONFIRM">(
    "VIEW",
  );
  const [previewReady, setPreviewReady] = useState(false);
  const openPreview = (artifact: Artifact, intent: "VIEW" | "CONFIRM") => {
    setPreviewReady(false);
    setPreviewIntent(intent);
    setPreview(artifact);
  };
  const [search, setSearch] = useState(""),
    [fields, setFields] = useState<Record<string, string>>({}),
    [submitting, setSubmitting] = useState(false),
    [submissionIssue, setSubmissionIssue] = useState(""),
    [clock, setClock] = useState(Date.now());
  const verified =
    identity.customer.authKind === "REGISTERED" &&
    !!identity.customer.account?.verified;
  const coupons = useGrowthResource<{ items: Coupon[] }>(
    verified &&
      (!original || original.ownerAccountId === identity.customer.account?.id)
      ? "/customer/coupons"
      : null,
  );
  const current = () => mounted.current;
  const [priceVersionChange, setPriceVersionChange] = useState<{
    from: number;
    to: number;
  } | null>(null);
  const acceptQuote = (next: Quote) => {
    const valid = assertQuote(next);
    const previous = quoteRef.current;
    setPriceVersionChange(
      previous && previous.priceVersion !== valid.priceVersion
        ? { from: previous.priceVersion, to: valid.priceVersion }
        : null,
    );
    setQuote(valid);
    quoteRef.current = valid;
    setConfirmedQuote("");
    setClock(Date.now());
  };
  const prepare = async () => {
    if (!configReadyRef.current) return;
    const configEpoch = readinessEpoch.current;
    const seq = ++sequence.current,
      generation = identityRef.current.generation,
      epoch = preparationEpoch.current;
    let flight: Promise<Preparation> | null = null;
    quoteSequence.current++;
    quoteLock.current = false;
    setQuoteBusy(false);
    setBusy(true);
    setError("");
    setQuoteError("");
    setConfirmedQuote("");
    try {
      const invalid = draft.flatMap((f) => pricingErrors(f, data.config));
      if (invalid.length) throw new Error(invalid.join("；"));
      if (!prepFlight.current)
        prepFlight.current = (async () => {
          let result: Preparation;
          if (preparingId.current) {
            if (prepState.current === "FAILED")
              await growthRequest(
                `/preparations/${preparingId.current}/retry`,
                { method: "POST", body: {} },
              );
            result = await growthRequest<Preparation>(
              `/preparations/${preparingId.current}`,
            );
          } else if (DEMO) {
            const module = await import("../lib/artifacts");
            const generated = await module.generateDemoArtifacts(
              draft,
              data.config,
            );
            if (
              epoch !== preparationEpoch.current ||
              generation !== identityRef.current.generation ||
              !configReadyRef.current ||
              configEpoch !== readinessEpoch.current
            )
              throw new Error("结算身份已变更，请重新准备");
            result = await growthRequest<Preparation>("/preparations", {
              method: "POST",
              body: {
                id: crypto.randomUUID(),
                lines: demoPriceLines(draft, data.config),
                artifacts: generated,
                replacesOrderId: original?.id,
                expectedOrderVersion: original?.version,
                checkoutAccountId: original?.checkoutAccountId || null,
                promotionIntent: { selectionMode: "AUTO" },
              },
            });
          } else {
            const files = draft.map((f) =>
              f.kind === "DOCUMENT"
                ? {
                    clientFileId: f.id,
                    assetId: f.assetId,
                    kind: f.kind,
                    segments: f.segments.map((s) => ({
                      clientSegmentId: s.id,
                      ranges: parseRanges(s.range, f.pages),
                      colorMode: s.colorMode,
                      sides: s.sides,
                      flip: s.flip,
                      copies: s.copies,
                    })),
                    excludedPages: f.excludedPages,
                    bindingType: f.bindingType,
                  }
                : {
                    clientFileId: f.id,
                    assetId: f.assetId,
                    kind: f.kind,
                    copies: f.photo.copies,
                    orientation: f.photo.orientation,
                    fit: f.photo.fit,
                    rotationDegrees: f.photo.rotationDegrees,
                    crop:
                      f.photo.fit === "CONTAIN"
                        ? null
                        : (() => {
                            const paper = photoPaper(f, data.config);
                            const turn = f.photo.rotationDegrees % 180 !== 0;
                            return photoCrop(
                              turn
                                ? f.photo.sourceHeight || 1
                                : f.photo.sourceWidth || 1,
                              turn
                                ? f.photo.sourceWidth || 1
                                : f.photo.sourceHeight || 1,
                              paper.printWidth,
                              paper.printHeight,
                              f.photo.zoom,
                              f.photo.x,
                              f.photo.y,
                            );
                          })(),
                  },
            );
            result = await growthRequest<Preparation>("/preparations", {
              method: "POST",
              body: {
                files,
                replacesOrderId: original?.id,
                expectedOrderVersion: original?.version,
                promotionIntent: { selectionMode: "AUTO" },
              },
            });
          }
          if (
            epoch !== preparationEpoch.current ||
            generation !== identityRef.current.generation ||
            !configReadyRef.current ||
            configEpoch !== readinessEpoch.current
          )
            throw new Error("结算身份已变更，请重新准备");
          preparingId.current = result.id;
          prepState.current = result.status;
          for (
            let attempt = 0;
            ["QUEUED", "PROCESSING"].includes(result.status) && attempt < 40;
            attempt++
          ) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            if (
              !current() ||
              epoch !== preparationEpoch.current ||
              generation !== identityRef.current.generation ||
              !configReadyRef.current ||
              configEpoch !== readinessEpoch.current
            )
              throw new Error("准备页面已关闭，请重新打开");
            result = await growthRequest<Preparation>(
              `/preparations/${result.id}`,
            );
            if (
              !current() ||
              epoch !== preparationEpoch.current ||
              generation !== identityRef.current.generation ||
              !configReadyRef.current ||
              configEpoch !== readinessEpoch.current
            )
              throw new Error("结算身份已变更，请重新准备");
            prepState.current = result.status;
          }
          if (result.status === "FAILED")
            throw new Error(
              "最终 PDF 生成失败，可重试当前任务，不会创建新准备单",
            );
          if (result.status !== "READY")
            throw new Error(
              "最终 PDF 仍在处理中，稍后重试将读取同一个准备任务",
            );
          return result;
        })();
      flight = prepFlight.current;
      const result = await flight;
      if (
        !current() ||
        seq !== sequence.current ||
        generation !== identityRef.current.generation ||
        !configReadyRef.current ||
        configEpoch !== readinessEpoch.current
      )
        return;
      if (!result.quote)
        throw new Error(
          "CLIENT_UPGRADE_REQUIRED：准备单缺少完整报价，请升级客户端或重新准备",
        );
      if (!result.artifacts?.length)
        throw new Error("准备单尚无可确认的最终 PDF");
      const previousArtifacts = artifactsRef.current;
      setAcks((previous) =>
        sameArtifactAcks(previousArtifacts, result.artifacts, previous),
      );
      setArtifacts(result.artifacts);
      artifactsRef.current = result.artifacts;
      prepVersion.current = result.version;
      acceptQuote(result.quote);
      setContentGeneration(generation);
      setContentReadiness(configEpoch);
    } catch (e) {
      if (
        current() &&
        seq === sequence.current &&
        generation === identityRef.current.generation &&
        configReadyRef.current &&
        configEpoch === readinessEpoch.current
      )
        setError((e as Error).message);
    } finally {
      if (prepFlight.current === flight) prepFlight.current = null;
      if (current() && seq === sequence.current) setBusy(false);
    }
  };
  const refreshQuote = async (
    intent: PromotionIntent = { selectionMode: "AUTO" },
    adopt: boolean | "withdraw" = false,
  ) => {
    if (
      !configReadyRef.current ||
      !preparingId.current ||
      quoteLock.current ||
      submissionLock.current
    )
      return;
    quoteLock.current = true;
    const configEpoch = readinessEpoch.current;
    const seq = ++quoteSequence.current,
      generation = identityRef.current.generation;
    setPromotionIntent(intent);
    setQuoteBusy(true);
    setQuoteError("");
    setConfirmedQuote("");
    try {
      const path = `/preparations/${encodeURIComponent(preparingId.current)}`;
      const response = await growthRequest<Quote | Preparation>(
        `${path}/${adopt === "withdraw" ? "withdraw-checkout" : adopt ? "adopt-checkout" : "requote"}`,
        {
          method: "POST",
          body: adopt
            ? { expectedVersion: prepVersion.current, confirm: true }
            : { promotionIntent: intent },
        },
      );
      if (
        !current() ||
        seq !== quoteSequence.current ||
        generation !== identityRef.current.generation ||
        !configReadyRef.current ||
        configEpoch !== readinessEpoch.current
      )
        return;
      const prepared = await growthRequest<Preparation>(path);
      if (
        !current() ||
        seq !== quoteSequence.current ||
        generation !== identityRef.current.generation ||
        !configReadyRef.current ||
        configEpoch !== readinessEpoch.current
      )
        return;
      const next = "quote" in response ? response.quote : (response as Quote);
      if (!next) throw new Error("服务端尚未返回完整新报价");
      prepVersion.current = prepared.version;
      if (prepared.artifacts?.length) {
        const previousArtifacts = artifactsRef.current;
        setAcks((previous) =>
          sameArtifactAcks(previousArtifacts, prepared.artifacts, previous),
        );
        setArtifacts(prepared.artifacts);
        artifactsRef.current = prepared.artifacts;
      }
      acceptQuote(next);
      setContentGeneration(generation);
      setContentReadiness(configEpoch);
      if (adopt) setAdoptOpen(false);
    } catch (e) {
      if (
        current() &&
        seq === quoteSequence.current &&
        generation === identityRef.current.generation &&
        configReadyRef.current &&
        configEpoch === readinessEpoch.current
      )
        setQuoteError((e as Error).message);
    } finally {
      if (current() && seq === quoteSequence.current) {
        quoteLock.current = false;
        setQuoteBusy(false);
      }
    }
  };
  const refreshQuoteRef = useRef(refreshQuote);
  refreshQuoteRef.current = refreshQuote;
  useEffect(() => {
    mounted.current = true;
    return () => {
      preparationEpoch.current++;
      prepFlight.current = null;
      mounted.current = false;
      sequence.current++;
      quoteSequence.current++;
    };
  }, []);
  const previousGeneration = useRef(identity.generation);
  useEffect(() => {
    if (previousGeneration.current === identity.generation) return;
    previousGeneration.current = identity.generation;
    preparationEpoch.current++;
    prepFlight.current = null;
    quoteLock.current = false;
    setBusy(false);
    sequence.current++;
    quoteSequence.current++;
    setConfirmedQuote("");
    setPreview(null);
    setSubmissionIssue("");
    setError("");
    setQuoteError("");
    setQuote(null);
    quoteRef.current = null;
    restartAfterLogout.current = false;
  }, [identity.generation]);
  useEffect(() => {
    if (!configReady) {
      preparationEpoch.current++;
      sequence.current++;
      quoteSequence.current++;
      prepFlight.current = null;
      quoteLock.current = false;
      setBusy(false);
      setQuoteBusy(false);
      setError("");
      setQuoteError("");
      setConfirmedQuote("");
      setPreview(null);
      return;
    }
    if (!draft.length) return;
    if (hasStarted.current && preparingId.current) {
      setBusy(false);
      void refreshQuoteRef.current({ selectionMode: "AUTO" });
    } else void prepare();
    hasStarted.current = true;
  }, [configReady, identity.generation]);
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const logout = () => {
      preparationEpoch.current++;
      setSubmissionIssue("");
      restartAfterLogout.current = true;
      sequence.current++;
      quoteSequence.current++;
      preparingId.current = "";
      prepVersion.current = 1;
      prepFlight.current = null;
      setQuote(null);
      quoteRef.current = null;
      setArtifacts([]);
      artifactsRef.current = [];
      setAcks([]);
      setConfirmedQuote("");
      setAdoptOpen(false);
      setLoginOpen(false);
      setPreview(null);
      setPromotionIntent({ selectionMode: "AUTO" });
      quoteLock.current = false;
      setQuoteBusy(false);
    };
    window.addEventListener("dormprint:customer-logout", logout);
    return () =>
      window.removeEventListener("dormprint:customer-logout", logout);
  }, [signature]);
  useEffect(() => {
    if (!preparingId.current || !artifacts.length) return;
    setCheckoutMemory((memory) => ({
      ...memory,
      [signature]: {
        preparationId: preparingId.current,
        preparationVersion: prepVersion.current,
        artifacts,
        acks,
        quote,
        generation: identity.generation,
        promotionIntent,
        name,
        phone,
        building,
        slot,
        customerNote,
      } satisfies CheckoutSnapshot,
    }));
  }, [
    signature,
    artifacts,
    acks,
    quote,
    identity.generation,
    promotionIntent,
    name,
    phone,
    building,
    slot,
    customerNote,
    setCheckoutMemory,
  ]);
  if (!draft.length) return <Navigate to="/u/new" replace />;
  const contentCurrent =
    configReady &&
    contentGeneration === identity.generation &&
    contentReadiness === readinessEpoch.current;
  const expired = !quoteIsCurrent(quote, clock);
  const accountAdopted =
    !!quote?.checkoutAccountId &&
    quote.checkoutAccountId === identity.customer.account?.id;
  const a4Sheets =
    quote?.lines
      .filter((line) => line.service === "A4")
      .reduce((sum, line) => sum + line.quantity, 0) || 0;
  const publicVolume = data.config.volumeDiscounts;
  const nextVolumeTier =
    publicVolume?.enabled &&
    (!publicVolume.effectiveAt || Date.parse(publicVolume.effectiveAt) <= clock)
      ? [...publicVolume.tiers]
          .filter((tier) => tier.minSheets > a4Sheets)
          .sort((a, b) => a.minSheets - b.minSheets)[0]
      : undefined;

  const submit = async () => {
    if (
      !configReadyRef.current ||
      !contentCurrent ||
      submissionLock.current ||
      submissionIssue ||
      busy ||
      error
    )
      return;
    const result = contactSchema.safeParse({
      nickname: name,
      phone,
      building,
      slot,
      customerNote,
    });
    if (!result.success) {
      setFields(
        Object.fromEntries(
          result.error.issues.map((i) => [i.path[0], i.message]),
        ),
      );
      return;
    }
    setFields({});
    if (
      !quote ||
      !quoteIsCurrent(quote) ||
      confirmedQuote !== quote.id ||
      quoteBusy ||
      quoteError
    ) {
      toast("请取得最新报价并明确确认本次应付金额");
      return;
    }
    if (!allArtifactsAcknowledged(artifacts, acks)) {
      toast("请逐项检查并确认全部最终 PDF");
      return;
    }
    if (!data.config.serviceEnabled) {
      toast("当前已暂停接单，请稍后重试");
      return;
    }
    if (!data.slots.some((s) => s.id === slot && s.enabled)) {
      toast("原预约时段已关闭，请重新选择开放时段");
      return;
    }
    if (original && original.status !== "PENDING_CONFIRMATION") {
      toast("订单已确认付款，不能继续修改内容");
      return;
    }
    const submittedQuote = quote,
      submittedGeneration = identity.generation,
      submittedSignature = signature,
      submittedConfigEpoch = readinessEpoch.current;
    submissionLock.current = true;
    setSubmitting(true);
    try {
      let order: Order;
      let issuedToken: string | undefined;
      if (DEMO) {
        const orderId = original?.id || crypto.randomUUID();
        const reservation = await growthRequest<{
          ownerPrincipalId: string;
          accessToken?: string;
        }>("/__demo/reserve", {
          method: "POST",
          body: {
            preparationId: preparingId.current,
            orderId,
            quoteId: quote.id,
            expectedVersion: prepVersion.current,
          },
        });
        issuedToken = reservation.accessToken;
        order = {
          id: orderId,
          pickupCode:
            original?.pickupCode ||
            Array.from(
              crypto.getRandomValues(new Uint8Array(5)),
              (n) => "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[n % 32],
            ).join(""),
          version: (original?.version || 0) + 1,
          contentRevision: (original?.contentRevision || 0) + 1,
          status: "PENDING_CONFIRMATION",
          nickname: name,
          phone,
          locationId: building,
          slotId: slot,
          createdAt: original?.createdAt || now(),
          totalCents: submittedQuote.totalCents,
          quote: submittedQuote,
          currentQuoteId: submittedQuote.id,
          checkoutAccountId: submittedQuote.checkoutAccountId,
          checkoutAuthKind:
            original?.checkoutAuthKind ||
            (submittedQuote.checkoutAccountId ? "REGISTERED" : "ANONYMOUS"),
          ownerPrincipalId: reservation.ownerPrincipalId,
          ownerAccountId: original
            ? original.ownerAccountId
            : submittedQuote.checkoutAccountId || undefined,
          files: draft.map((f) => {
            const a = artifacts.find((a) => a.fileId === f.id);
            return {
              ...f,
              artifactId: a?.id,
              sha256: a?.sha256,
              pdfUrl: a?.url,
              finalArtifacts: artifacts.filter((a) => a.fileId === f.id),
              taskResults: {},
            };
          }),
          packingStatus: "BLOCKED",
          addressReview: false,
          note: original?.note || "",
          customerNote: result.data.customerNote,
          priority: false,
          history: [
            {
              title: original ? "订单内容已修订" : "订单已提交",
              time: now(),
              description:
                submittedQuote.totalCents === 0
                  ? "等待管理员人工确认零元结算"
                  : "等待核对微信付款",
            },
            ...(original?.history || []),
          ],
          allowedActions: [
            "ORDER_REVISE_PENDING",
            "ORDER_CANCEL_PENDING",
            "ORDER_CHANGE_LOCATION_PRE_DELIVERY",
          ],
        };
      } else {
        const response = await growthRequest<{
          order: Order;
          accessToken?: string;
        }>(
          original
            ? `/orders/${encodeURIComponent(original.id)}/revisions`
            : "/orders",
          {
            method: "POST",
            body: {
              expectedVersion: original?.version,
              preparationId: preparingId.current,
              quoteId: submittedQuote.id,
              previewAcks: artifacts.map((a) => ({
                artifactId: a.id,
                sha256: a.sha256,
              })),
              contact: { wechatNickname: name, phone },
              locationId: building,
              slotId: slot,
              customerNote: result.data.customerNote,
            },
          },
        );
        order = response.order;
        issuedToken = response.accessToken;
      }
      if (
        !current() ||
        identityRef.current.generation !== submittedGeneration ||
        signatureRef.current !== submittedSignature
      )
        return;
      if (
        !configReadyRef.current ||
        submittedConfigEpoch !== readinessEpoch.current
      ) {
        setSubmissionIssue(
          "提交期间服务配置已变更，结果待核实，请先在订单列表核对是否已生成订单，避免重复下单。",
        );
        return;
      }
      if (issuedToken) {
        const link = new URL("/u/order-link", window.location.origin);
        link.searchParams.set("orderId", order.id);
        link.hash = new URLSearchParams({ token: issuedToken }).toString();
        setSavedAccessLinks((links) => ({ ...links, [order.id]: link.href }));
      }
      if (order.totalCents !== submittedQuote.totalCents)
        throw new Error("订单金额与已确认报价不一致，请重新核对订单状态");
      setData((s) => ({
        ...s,
        orders: [order, ...s.orders.filter((o) => o.id !== order.id)],
      }));
      setCheckoutMemory((memory) => {
        const next = { ...memory };
        delete next[signature];
        return next;
      });
      nav(`/u/orders/${order.id}/contact`);
    } catch (e) {
      if (
        !current() ||
        identityRef.current.generation !== submittedGeneration ||
        signatureRef.current !== submittedSignature
      )
        return;
      if (
        !configReadyRef.current ||
        submittedConfigEpoch !== readinessEpoch.current
      ) {
        setSubmissionIssue(
          "提交期间服务配置已变更，结果待核实，请先在订单列表核对是否已生成订单，避免重复下单。",
        );
        return;
      }
      if (isUncertainSubmission(e)) {
        setSubmissionIssue(
          "提交结果待核实，请先在订单列表核对是否已生成订单，避免重复下单。",
        );
      } else {
        toast((e as Error).message);
        setConfirmedQuote("");
        setQuoteError((e as Error).message);
      }
    } finally {
      submissionLock.current = false;
      if (current()) setSubmitting(false);
    }
  };
  return (
    <main className="flow-page">
      <Link to="/u/new/settings" className="back-link">
        <ArrowLeft size={17} />
        返回打印设置
      </Link>
      <PageHeading
        title="最后检查一下，就可以下单了"
        description="确认打印效果，告诉我们把资料送到哪里。"
      />
      <Steps step={2} />
      <div className="checkout-layout">
        <div className="checkout-main">
          <Panel
            title="确认最终打印文件"
            action={
              <span className="soft-tag">{artifacts.length} 个独立任务</span>
            }
          >
            {!configReady ||
            busy ||
            (!contentCurrent && !error && !quoteError) ? (
              <Loading
                label={!configReady ? "正在读取打印服务配置…" : undefined}
              />
            ) : error || (!contentCurrent && quoteError) ? (
              <ErrorState message={error || quoteError} retry={prepare} />
            ) : (
              artifacts.map((a) => {
                const file = draft.find((f) => f.id === a.fileId)!;
                return (
                  <div
                    className={`artifact-row ${acks.includes(a.id) ? "confirmed" : ""}`}
                    key={a.id}
                  >
                    <FileRow
                      file={file}
                      action={
                        <button
                          className="text-button"
                          onClick={() => openPreview(a, "VIEW")}
                        >
                          查看最终 PDF
                          <ChevronRight size={15} />
                        </button>
                      }
                    />
                    <div className="artifact-info">
                      <span>{a.range} 页</span>
                      <span>
                        {a.color === "BW" ? "黑白" : "彩色"} ·{" "}
                        {a.sides === "DUPLEX" ? "双面" : "单面"}
                      </span>
                      <span>
                        {a.copies} 份 · {a.pages} PDF 页 · {a.sheets} 张纸
                      </span>
                    </div>
                    {a.blankBacks > 0 && (
                      <p className="form-hint">
                        每份奇数页后补空白背面，共 {a.blankBacks}{" "}
                        页，不额外按张收费。
                      </p>
                    )}
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={acks.includes(a.id)}
                        onChange={(e) => {
                          if (e.target.checked) openPreview(a, "CONFIRM");
                          else setAcks((s) => s.filter((id) => id !== a.id));
                        }}
                      />
                      <span>我已检查这份最终 PDF，确认打印设置</span>
                    </label>
                    <small className="hash-label">
                      SHA-256 · {a.sha256.slice(0, 18)}…
                    </small>
                  </div>
                );
              })
            )}
          </Panel>
          <Panel title="本次结算与优惠">
            <div className="checkout-promotion-stack">
              {original ? (
                <Notice>
                  当前修订保持原订单的结算归属与优惠授权。单订单链接授权不会读取或换用原账号其他优惠券；若原优惠不再适用，请由有权订单持有人重新确认报价。
                </Notice>
              ) : (
                <>
                  {verified ? (
                    <>
                      <Notice>
                        {accountAdopted
                          ? "本次结算已明确关联到当前账号。"
                          : "你已登录账号，当前准备单仍保留原游客结算身份。可继续游客下单，或仅关联本次结算以使用账号优惠，不会合并其他游客订单。"}
                      </Notice>
                      {accountAdopted && !original && (
                        <Button
                          variant="secondary"
                          disabled={
                            !configReady ||
                            busy ||
                            quoteBusy ||
                            submitting ||
                            !quote
                          }
                          onClick={() =>
                            void refreshQuote(
                              { selectionMode: "AUTO" },
                              "withdraw",
                            )
                          }
                        >
                          撤回本次账号结算，改为游客
                        </Button>
                      )}
                      {!accountAdopted && (
                        <Button
                          variant="secondary"
                          disabled={
                            !configReady ||
                            busy ||
                            quoteBusy ||
                            submitting ||
                            !quote
                          }
                          onClick={() => setAdoptOpen(true)}
                        >
                          仅关联本次结算到账号
                        </Button>
                      )}
                    </>
                  ) : (
                    <>
                      <Notice>
                        游客可正常打印并自动享用已配置的满量优惠；验证邮箱登录后可选择关联本次结算并使用账号券。
                      </Notice>
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => setLoginOpen(true)}
                      >
                        登录 / 注册以使用账号优惠
                      </Button>
                    </>
                  )}
                  {verified && coupons.loading && (
                    <Notice>正在加载账号优惠券…</Notice>
                  )}
                  {verified && coupons.error && (
                    <ErrorState
                      message={coupons.error}
                      retry={coupons.reload}
                    />
                  )}
                  <Field
                    label="优惠选择"
                    hint="自动择优、指定优惠券都会与满量优惠比较；同额优先满量，保留优惠券。"
                  >
                    <select
                      disabled={
                        !configReady ||
                        busy ||
                        quoteBusy ||
                        submitting ||
                        !quote
                      }
                      value={
                        promotionIntent.selectionMode === "SPECIFIC_COUPON"
                          ? `coupon:${promotionIntent.couponGrantId || ""}`
                          : promotionIntent.selectionMode || "AUTO"
                      }
                      onChange={(e) => {
                        const value = e.target.value;
                        void refreshQuote(
                          value.startsWith("coupon:")
                            ? {
                                selectionMode: "SPECIFIC_COUPON",
                                couponGrantId: value.slice(7),
                              }
                            : { selectionMode: value as "AUTO" | "NO_COUPON" },
                        );
                      }}
                    >
                      <option value="AUTO">自动选择最优优惠</option>
                      <option value="NO_COUPON">
                        不使用优惠券（仍享满量优惠）
                      </option>
                      {accountAdopted &&
                        coupons.data?.items.map((coupon) => (
                          <option
                            value={`coupon:${coupon.couponGrantId}`}
                            key={coupon.couponGrantId}
                            disabled={
                              coupon.status !== "AVAILABLE" ||
                              coupon.riskHold === "FROZEN" ||
                              (!!coupon.validUntil &&
                                Date.parse(coupon.validUntil) <= Date.now())
                            }
                          >
                            {coupon.name} · {money(coupon.amountCents)} · 满{" "}
                            {money(coupon.minSpendCents)} ·{" "}
                            {coupon.service === "ALL"
                              ? "全部服务"
                              : coupon.service === "A4"
                                ? "A4打印"
                                : "照片"}
                            {coupon.riskHold === "FROZEN"
                              ? " · 已冻结"
                              : coupon.status !== "AVAILABLE"
                                ? ` · ${coupon.status}`
                                : ""}
                          </option>
                        ))}
                    </select>
                  </Field>
                  {accountAdopted && coupons.data?.items.length === 0 && (
                    <small>
                      暂无可用账号优惠券，仍可享用实际配置的满量优惠。
                    </small>
                  )}
                </>
              )}
              {original &&
                verified &&
                original.ownerAccountId === identity.customer.account?.id && (
                  <Field
                    label="重新选择优惠"
                    hint="本次修订提交时原子替换预约；新金额仍须明确确认。"
                  >
                    <select
                      disabled={
                        !configReady ||
                        busy ||
                        quoteBusy ||
                        submitting ||
                        !quote
                      }
                      value={
                        promotionIntent.selectionMode === "SPECIFIC_COUPON"
                          ? `coupon:${promotionIntent.couponGrantId}`
                          : promotionIntent.selectionMode || "AUTO"
                      }
                      onChange={(e) =>
                        void refreshQuote(
                          e.target.value.startsWith("coupon:")
                            ? {
                                selectionMode: "SPECIFIC_COUPON",
                                couponGrantId: e.target.value.slice(7),
                              }
                            : {
                                selectionMode: e.target.value as
                                  "AUTO" | "NO_COUPON",
                              },
                        )
                      }
                    >
                      <option value="AUTO">自动选择最优优惠</option>
                      <option value="NO_COUPON">
                        不使用优惠券（仍享满量优惠）
                      </option>
                      {coupons.data?.items
                        .filter(
                          (c) =>
                            c.riskHold === "NONE" &&
                            (c.status === "AVAILABLE" ||
                              (c.status === "RESERVED" &&
                                c.reservedOrderId === original.id)),
                        )
                        .map((c) => (
                          <option
                            key={c.couponGrantId}
                            value={`coupon:${c.couponGrantId}`}
                          >
                            {c.name} · {money(c.amountCents)}
                          </option>
                        ))}
                    </select>
                  </Field>
                )}
            </div>
          </Panel>
          <Panel title="联系方式">
            <div className="form-grid">
              <Field label="微信昵称 *" error={fields.nickname}>
                <input
                  value={name}
                  maxLength={40}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="便于管理员核对付款"
                  autoComplete="nickname"
                />
              </Field>
              <Field label="手机号码 *" error={fields.phone}>
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="配送时用于联系"
                  inputMode="tel"
                  autoComplete="tel"
                />
              </Field>
            </div>
            <div className="checkout-order-note">
              <Field
                label="订单备注（选填）"
                error={fields.customerNote}
                hint="补充需要管理员留意的事项；打印参数请在上一步设置。"
              >
                <textarea
                  value={customerNote}
                  maxLength={30}
                  rows={3}
                  placeholder="如：几份资料请分开整理，方便领取"
                  onChange={(e) => setCustomerNote(e.target.value.slice(0, 30))}
                />
              </Field>
              <p className="order-note-count">{customerNote.length} / 30</p>
            </div>
          </Panel>
          <Panel title="把资料送到哪里？">
            <div className="map-search">
              <Search size={17} />
              <input
                placeholder="搜索宿舍楼或教学楼"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <CampusMap
              buildings={data.buildings}
              value={building}
              onChange={setBuilding}
              mapUrl={data.config.mapUrl}
            />
            <div className="building-choices">
              {data.buildings
                .filter((b) => b.enabled && b.name.includes(search))
                .map((b) => (
                  <button
                    key={b.id}
                    className={b.id === building ? "selected" : ""}
                    onClick={() => setBuilding(b.id)}
                  >
                    <MapPin size={15} />
                    {b.name}
                    {building === b.id && <Check size={14} />}
                  </button>
                ))}
            </div>
            {fields.building && (
              <p className="field-error">{fields.building}</p>
            )}
            <p className="form-hint">
              送达楼栋公共区域，不需要填写楼层与门牌号。
            </p>
          </Panel>
          <Panel title="选择预约配送时段">
            <div className="slot-choices">
              {data.slots
                .filter((s) => s.enabled)
                .map((s) => (
                  <button
                    className={slot === s.id ? "selected" : ""}
                    key={s.id}
                    onClick={() => setSlot(s.id)}
                  >
                    <span>
                      {s.date.slice(5).replace("-", "月")}日 · {s.weekday}
                    </span>
                    <b>{s.name}</b>
                    <small>
                      {s.start} – {s.end}
                    </small>
                    {slot === s.id && <CheckCircle2 size={18} />}
                  </button>
                ))}
            </div>
            {!data.slots.some((s) => s.enabled) && (
              <Notice>
                暂无可用配送时段，可保留当前工作进度，待管理员开放时段后再下单。
              </Notice>
            )}
            {fields.slot && <p className="field-error">{fields.slot}</p>}
            <p className="form-hint">
              所选为申请时段：人工确认收款后才占用时段容量，结算前不保证名额。最终配送时间以管理员更新计划为准。
            </p>
            {(() => {
              const minutes = (
                data.config as unknown as { unpaidTimeoutMinutes?: number | null }
              ).unpaidTimeoutMinutes;
              return typeof minutes === "number" && minutes > 0 ? (
                <p className="v1-deadline">
                  提交后若 {minutes} 分钟内未人工确认收款，订单将按配置期限自动关闭并释放临时资源；以服务端关闭结果为准。
                </p>
              ) : (
                <p className="v1-deadline">
                  未配置未付款自动关闭期限；提交后请及时联系管理员确认收款。
                </p>
              );
            })()}
          </Panel>
        </div>
        <aside className="checkout-summary">
          <Panel title="订单费用">
            {!configReady ||
            busy ||
            (!contentCurrent && !error && !quoteError) ? (
              <Loading
                label={!configReady ? "正在读取打印服务配置…" : undefined}
              />
            ) : error ? (
              <Notice type="error">请先完成打印文件准备。</Notice>
            ) : contentCurrent && quote ? (
              <>
                <PriceRows total={quote.totalCents} quote={quote} />
                <span className="quote-version">报价版本 · {quote.id}</span>
                <p className="form-hint">
                  本单 A4 用纸{" "}
                  {quote.lines
                    .filter((line) => line.service === "A4")
                    .reduce((sum, line) => sum + line.quantity, 0)}{" "}
                  张。照片与装订不计入满量折扣。
                </p>
                {!quote.volumeDiscountCandidate && nextVolumeTier && (
                  <p className="form-hint">
                    距当前配置的满量门槛还差{" "}
                    {nextVolumeTier.minSheets - a4Sheets} 张 A4
                    用纸，实际减免以报价结果为准。
                  </p>
                )}
                {quote.volumeDiscountCandidate && (
                  <Notice>
                    A4 原价{" "}
                    {money(quote.volumeDiscountCandidate.a4SubtotalCents)}，满{" "}
                    {quote.volumeDiscountCandidate.minSheets} 张享{" "}
                    {quote.volumeDiscountCandidate.payRateBps / 1000} 折，可减{" "}
                    {money(quote.volumeDiscountCandidate.discountCents)}。
                    {quote.selectedPromotionType === "VOLUME_TIER"
                      ? "本次采用满量优惠，优惠券保持可用。"
                      : quote.selectedPromotionType === "NONE"
                        ? "本次没有实际减免。"
                        : "本次采用更优的优惠方案。"}
                  </Notice>
                )}
                {quote.discounts.map((discount) => (
                  <Notice key={discount.kind}>
                    {discount.description} · 本次减免{" "}
                    {money(discount.amountCents)}
                  </Notice>
                ))}
                {priceVersionChange && (
                  <Notice type="warning">
                    价格版本已由 v{priceVersionChange.from} 更新为 v
                    {priceVersionChange.to}
                    ，原金额确认已清除；请核对新报价并重新勾选确认。已提交且内容未变的订单继续锁价，不受影响。
                  </Notice>
                )}
                <p className="form-hint">
                  本次报价价格版本 v{quote.priceVersion}，有效期至{" "}
                  {new Date(quote.expiresAt).toLocaleTimeString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                    hour12: false,
                  })}
                  （后台配置{" "}
                  {(
                    data.config as unknown as { quoteMinutes?: number }
                  ).quoteMinutes ?? 15}{" "}
                  分钟）。过期或价格版本变化时必须重新获取并确认；已提交订单的报价锁价。
                </p>
                {expired && (
                  <Notice type="warning">
                    报价已过期，请重新报价并确认金额，PDF 检查记录会保留。
                  </Notice>
                )}
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={confirmedQuote === quote.id}
                    disabled={
                      !configReady || quoteBusy || expired || !!quoteError
                    }
                    onChange={(e) =>
                      setConfirmedQuote(e.target.checked ? quote.id : "")
                    }
                  />
                  <span>
                    我确认本次应付 {money(quote.totalCents)}
                    ，并按此金额提交订单。
                  </span>
                </label>
              </>
            ) : (
              <Notice>尚无可用的完整报价，请重新获取。</Notice>
            )}
            {quoteBusy && <TaskStatus label="正在重新核验身份与优惠…" />}
            {quoteError && (
              <div role="alert">
                <Notice type="error">{quoteError}</Notice>
              </div>
            )}
            {!busy && (quoteError || expired || !quote) && (
              <Button
                variant="secondary"
                onClick={() => void refreshQuote(promotionIntent)}
                disabled={!configReady || quoteBusy || submitting}
              >
                重新获取报价
              </Button>
            )}
            <Notice>
              {quote?.totalCents === 0
                ? "本单优惠后无需转账，下单后联系管理员人工确认零元结算。"
                : "下单后添加管理员微信，核对提取码与金额后线下付款。"}
            </Notice>
            {submissionIssue && (
              <Notice type="warning">
                {submissionIssue} <Link to="/u/orders">查看订单</Link>
              </Notice>
            )}
            <Button
              busy={submitting}
              busyLabel="正在提交…"
              className="full"
              onClick={submit}
              disabled={
                submitting ||
                !!submissionIssue ||
                busy ||
                !contentCurrent ||
                !!error ||
                quoteBusy ||
                !!quoteError ||
                !quote ||
                expired ||
                confirmedQuote !== quote.id ||
                !allArtifactsAcknowledged(artifacts, acks) ||
                !artifacts.length ||
                !data.slots.some((s) => s.enabled)
              }
            >
              确认并提交订单
              <ArrowRight size={17} />
            </Button>
            <p className="privacy-caption">
              <ShieldCheck size={13} />
              文件与联系信息仅用于本次服务
            </p>
          </Panel>
        </aside>
      </div>
      {loginOpen && (
        <Modal title="登录 / 注册" onClose={() => setLoginOpen(false)}>
          <CustomerAuthForm
            mode="login"
            embedded
            onSuccess={() => {
              setLoginOpen(false);
              setAdoptOpen(true);
            }}
          />
        </Modal>
      )}
      {adoptOpen && verified && !original && (
        <Modal
          title="仅关联本次结算"
          onClose={() => !quoteBusy && setAdoptOpen(false)}
        >
          <div className="checkout-promotion-stack">
            <Notice>
              确认后仅授权当前准备单与打印文件用于账号结算。不会合并本机其他游客订单，PDF
              未变化时保留已有检查记录。新报价需要重新确认金额。
            </Notice>
            {quoteError && <Notice type="error">{quoteError}</Notice>}
            <Button
              disabled={!configReady || quoteBusy || submitting}
              onClick={() => void refreshQuote({ selectionMode: "AUTO" }, true)}
            >
              {quoteBusy ? "正在关联并重新报价…" : "确认仅关联本次结算"}
            </Button>
            <Button
              variant="secondary"
              disabled={!configReady || quoteBusy || submitting}
              onClick={() => setAdoptOpen(false)}
            >
              继续游客结算
            </Button>
          </div>
        </Modal>
      )}
      {contentCurrent && preview && (
        <Modal
          title={`${previewIntent === "CONFIRM" ? "确认最终打印格式" : "最终打印 PDF"} · ${draft.find((f) => f.id === preview.fileId)?.name || "打印文件"}`}
          wide
          onClose={() => setPreview(null)}
        >
          <DocPreview
            key={`${preview.id}:${preview.sha256}`}
            file={draft.find((f) => f.id === preview.fileId)!}
            pdfUrl={preview.url}
            onReadyChange={setPreviewReady}
          />
          <Notice>
            此 PDF 为当前确认版本。打印设置变更后需要重新生成并确认。
          </Notice>
          {previewIntent === "CONFIRM" && (
            <Button
              className="final-preview-confirm"
              disabled={
                !canAcknowledgeArtifact(preview, artifacts, previewReady)
              }
              onClick={() => {
                if (
                  !contentCurrent ||
                  !canAcknowledgeArtifact(preview, artifacts, previewReady)
                )
                  return;
                setAcks((s) =>
                  s.includes(preview.id) ? s : [...s, preview.id],
                );
                setPreview(null);
              }}
            >
              <Check size={16} />
              我已确认最终打印格式
            </Button>
          )}
        </Modal>
      )}
    </main>
  );
}
