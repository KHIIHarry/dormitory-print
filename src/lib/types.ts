import type { Quote, VolumePolicy } from "../growth/types";
import type { Artifact } from "./artifacts";
import type { components } from "./api-schema";
export type Role = components["schemas"]["Role"];
export type OrderStatus = components["schemas"]["OrderStatus"];
export type Segment = {
  id: string;
  range: string;
  colorMode: "BW" | "COLOR";
  sides: "SINGLE" | "DUPLEX";
  flip: "NONE" | "LONG_EDGE" | "SHORT_EDGE";
  copies: number;
};
export type PrintFile = {
  id: string;
  assetId: string;
  name: string;
  size: number;
  pages: number;
  kind: "DOCUMENT" | "PHOTO";
  imagePrintMedium?: "PENDING" | "A4" | "PHOTO";
  imagePrintVersion?: number;
  imageSource?: { file: File; url?: string; assetId: string };
  status: "UPLOADING" | "PROCESSING" | "READY" | "FAILED";
  error?: string;
  progress?: number;
  uploadPhase?:
    | "QUEUED"
    | "READING"
    | "TRANSFERRING"
    | "AWAITING_RESPONSE"
    | "PROCESSING"
    | "READY"
    | "FAILED";
  uploadVersion?: number;
  uploadRequestKey?: string;
  a4UploadAttempt?: { key: string; file?: File };
  url?: string;
  localFile?: File;
  segments: Segment[];
  excludedPages: number[];
  bindingType: "NONE" | "CORNER" | "SIDE";
  photo: {
    fit: "COVER" | "CONTAIN";
    rotationDegrees: 0 | 90 | 180 | 270;
    orientation: "PORTRAIT" | "LANDSCAPE";
    copies: number;
    zoom: number;
    sourceWidth?: number;
    sourceHeight?: number;
    x: number;
    y: number;
  };
  printStatus: string;
  bindingStatus: string;
  finalArtifacts?: Artifact[];
  taskResults?: Record<string, string>;
  attemptIds?: Record<string, string>;
  artifactId?: string;
  sha256?: string;
  pdfUrl?: string;
};
export type Position = {
  latitude: number;
  longitude: number;
  accuracyMeters: number;
  capturedAt: string;
  coordinateSystem: "WGS84";
};
export type Proof = {
  photoUrl?: string;
  skipPhotoReason?: string;
  position?: Position;
  skipLocationReason?: string;
  placementNote: string;
  deliveredAt: string;
};
export type Order = {
  currentQuoteId?: string;
  quote?: Quote;
  checkoutAccountId?: string | null;
  ownerAccountId?: string;
  ownerPrincipalId?: string;
  checkoutAuthKind?: "ANONYMOUS" | "REGISTERED" | "ORDER_ACCESS";
  settlement?: {
    settlementKind: "CASH_MANUAL" | "ZERO_PAYABLE";
    cashReceivedCents: number;
    discountCents: number;
    confirmedAt: string;
    confirmedBy: string;
  };
  blockers?: { code: string; message: string }[];
  id: string;
  pickupCode: string;
  version: number;
  contentRevision: number;
  status: OrderStatus;
  nickname: string;
  phone: string;
  locationId: string;
  slotId: string;
  createdAt: string;
  totalCents: number;
  files: PrintFile[];
  packingStatus: string;
  addressReview: boolean;
  note: string;
  customerNote?: string;
  priority: boolean;
  batchId?: string;
  proof?: Proof;
  deliveryAssignmentId?: string | null;
  assignment?: { id: string; orderId: string; batchId: string; status: string; version: number; addressReview?: boolean } | null;
  locationExceptionApproval?: { id: string; status: "APPROVED"; assignmentId: string; assignmentVersion: number; reason: string; failureCode?: string; approvedAt?: string } | null;
  history: { title: string; time: string; description: string }[];
  accessKind?: "OWNER" | "GUEST_OWNER" | "ORDER_GRANT" | "IMPORTED_GRANT";
  allowedActions: string[];
};
export type Building = {
  id: string;
  name: string;
  type: string;
  x: number;
  y: number;
  enabled: boolean;
};
export type Slot = {
  id: string;
  date: string;
  weekday: string;
  name: string;
  start: string;
  end: string;
  enabled: boolean;
  capacity: number;
};
export type Batch = {
  id: string;
  name: string;
  date: string;
  time: string;
  courierId: string;
  status: string;
  orderIds: string[];
  reason?: string;
  version: number;
};
export type Staff = {
  version?: number;
  emailMasked?: string;
  email?: string;
  emailVerified?: boolean;
  status?: "PENDING_SETUP" | "ACTIVE" | "DISABLED";
  mustChangePassword?: boolean;
  lastLoginAt?: string;
  allowedActions?: string[];
  id: string;
  name: string;
  username: string;
  phone: string;
  role: "ADMIN" | "COURIER";
  enabled: boolean;
};
export type ExceptionItem = {
  id: string;
  orderId: string;
  type: string;
  reason: string;
  status: string;
  time: string;
};
export type TrashItem = {
  batchSnapshot?: Batch;
  id: string;
  name: string;
  type: string;
  deletedAt: string;
  reason: string;
  by: string;
};
export type AuditItem = {
  id: string;
  action: string;
  target: string;
  by: string;
  time: string;
  reason: string;
};
export type Config = {
  features?: {
    customerEmailAuthAvailable: boolean;
    guestCheckoutEnabled: boolean;
    promotionsEnabled: boolean;
  };
  volumeDiscounts?: VolumePolicy;
  serviceEnabled: boolean;
  announcement: string;
  prices: {
    bwSingle: number | null;
    bwDuplex: number | null;
    colorSingle: number | null;
    colorDuplex: number | null;
    photo: number | null;
    corner: number | null;
    side: number | null;
  };
  wechat: string;
  phone: string;
  qrUrl: string;
  photoWidthMm: number;
  photoHeightMm: number;
  margins: { top: number; bottom: number; left: number; right: number };
  maxFiles: number;
  maxMb: number;
  bindingApproved: boolean;
  defaultFlip?: "LONG_EDGE" | "SHORT_EDGE";
  mapUrl?: string;
  locationMaxAgeSeconds?: number;
  locationMaxAccuracyMeters?: number;
};
export type DataState = {
  orders: Order[];
  batches: Batch[];
  buildings: Building[];
  slots: Slot[];
  staff: Staff[];
  exceptions: ExceptionItem[];
  trash: TrashItem[];
  audit: AuditItem[];
  config: Config;
};
