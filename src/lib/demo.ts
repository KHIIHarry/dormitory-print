import type { DataState, Order, PrintFile } from "./types";
export const makeFile = (name: string, pages = 12): PrintFile => ({
  id: crypto.randomUUID(),
  assetId: "demo-asset",
  name,
  size: 1240000,
  pages,
  kind: "DOCUMENT",
  status: "READY",
  progress: 100,
  segments: [
    {
      id: crypto.randomUUID(),
      range: `1-${pages}`,
      colorMode: "BW",
      sides: "DUPLEX",
      flip: "LONG_EDGE",
      copies: 1,
    },
  ],
  excludedPages: [],
  bindingType: "NONE",
  photo: {
    fit: "CONTAIN",
    rotationDegrees: 0,
    orientation: "PORTRAIT",
    copies: 1,
    zoom: 1,
    x: 50,
    y: 50,
  },
  printStatus: "PENDING",
  bindingStatus: "NOT_REQUIRED",
});
export function createDemo(): DataState {
  const statuses: Order["status"][] = [
    "PENDING_CONFIRMATION",
    "PENDING_PRINT",
    "READY_FOR_DELIVERY",
    "DELIVERING",
    "DELIVERED",
    "PENDING_PRINT",
    "PENDING_CONFIRMATION",
    "DELIVERY_EXCEPTION",
  ];
  const names = [
    "小林同学",
    "晚风",
    "陈同学",
    "许一一",
    "周末晴",
    "一颗栗子",
    "小满",
    "阿白",
  ];
  const codes = [
    "K7M4P",
    "R8F2A",
    "H3N6X",
    "B5T9D",
    "W2C8K",
    "P6J3R",
    "A9V7M",
    "N4E8S",
  ];
  const orders = statuses.map((status, i): Order => {
    const f = makeFile(
      [
        "英语期末复习资料.pdf",
        "高等数学·习题集.pdf",
        "课程设计报告.docx",
        "考研政治知识点.pdf",
        "线性代数重点整理.pdf",
        "实验报告（最终版）.pdf",
        "计算机网络笔记.pdf",
        "大学英语六级真题.pdf",
      ][i],
      12 + i * 2,
    );
    if (i === 1) {
      f.bindingType = "CORNER";
      f.bindingStatus = "WAITING_PRINT";
    }
    if (["READY_FOR_DELIVERY", "DELIVERING", "DELIVERED"].includes(status)) {
      f.printStatus = "COMPLETED";
      f.bindingStatus = "NOT_REQUIRED";
    }
    return {
      id: `order-${i + 1}`,
      pickupCode: codes[i],
      version: 1,
      contentRevision: 1,
      status,
      nickname: names[i],
      phone: `1380013800${i}`,
      locationId: `building-${(i % 4) + 1}`,
      slotId: "slot-1",
      createdAt: `2026-10-29 1${i}:2${i}`,
      totalCents: [240, 360, 480, 180, 320, 640, 260, 400][i],
      files: [f],
      packingStatus: ["READY_FOR_DELIVERY", "DELIVERING", "DELIVERED"].includes(
        status,
      )
        ? "PACKED"
        : "BLOCKED",
      addressReview: false,
      note: "",
      priority: i === 1,
      batchId: i === 2 ? "batch-1" : i === 3 ? "batch-2" : undefined,
      history: [
        {
          title: "订单已提交",
          time: `2026-10-29 1${i}:2${i}`,
          description: "请添加管理员微信，核对提取码并线下付款。",
        },
      ],
      allowedActions: [],
      ...(status === "DELIVERED"
        ? {
            proof: {
              placementNote: "资料放在一楼收发台左侧，请及时领取。",
              deliveredAt: "2026-10-29 18:08",
              skipPhotoReason: "演示凭证，未采集实际照片",
              skipLocationReason: "演示凭证，未采集设备坐标",
            },
          }
        : {}),
    };
  });
  return {
    orders,
    batches: [
      {
        id: "batch-1",
        name: "晚间配送 · 东区",
        date: "2026-10-29",
        time: "19:00 – 20:00",
        courierId: "staff-2",
        status: "PLANNED",
        orderIds: ["order-3"],
        version: 1,
      },
      {
        id: "batch-2",
        name: "午后配送 · 西区",
        date: "2026-10-29",
        time: "17:00 – 18:00",
        courierId: "staff-2",
        status: "DELIVERING",
        orderIds: ["order-4"],
        version: 1,
      },
    ],
    buildings: [
      {
        id: "building-1",
        name: "知行苑 1 栋",
        type: "宿舍楼",
        x: 26,
        y: 30,
        enabled: true,
      },
      {
        id: "building-2",
        name: "知行苑 2 栋",
        type: "宿舍楼",
        x: 40,
        y: 33,
        enabled: true,
      },
      {
        id: "building-3",
        name: "明德苑 3 栋",
        type: "宿舍楼",
        x: 69,
        y: 61,
        enabled: true,
      },
      {
        id: "building-4",
        name: "第一教学楼",
        type: "教学楼",
        x: 64,
        y: 27,
        enabled: true,
      },
      {
        id: "building-5",
        name: "第二教学楼",
        type: "教学楼",
        x: 30,
        y: 66,
        enabled: true,
      },
    ],
    slots: [
      {
        id: "slot-1",
        date: "2026-10-29",
        weekday: "星期四",
        name: "晚间配送",
        start: "19:00",
        end: "20:00",
        enabled: true,
        capacity: 30,
      },
      {
        id: "slot-2",
        date: "2026-10-30",
        weekday: "星期五",
        name: "午间配送",
        start: "12:00",
        end: "13:00",
        enabled: true,
        capacity: 20,
      },
      {
        id: "slot-3",
        date: "2026-10-30",
        weekday: "星期五",
        name: "晚间配送",
        start: "19:00",
        end: "20:00",
        enabled: true,
        capacity: 30,
      },
    ],
    staff: [
      {
        id: "staff-1",
        name: "管理员",
        username: "admin",
        phone: "13800138000",
        role: "ADMIN",
        enabled: true,
      },
      {
        id: "staff-2",
        name: "李同学",
        username: "courier",
        phone: "13800138009",
        role: "COURIER",
        enabled: true,
      },
    ],
    exceptions: [
      {
        id: "ex-1",
        orderId: "order-8",
        type: "无法联系收件人",
        reason: "电话暂时无人接听，资料已妥善保管，等待重新安排。",
        status: "OPEN",
        time: "2026-10-29 17:42",
      },
    ],
    trash: [
      {
        id: "trash-1",
        name: "旧版校园配送底图",
        type: "地图草稿",
        deletedAt: new Date().toISOString(),
        reason: "底图更新，保留恢复窗口",
        by: "管理员",
      },
    ],
    audit: [
      {
        id: "log-1",
        action: "确认线下收款",
        target: "H3N6X",
        by: "管理员",
        time: "2026-10-29 17:32",
        reason: "实收 ¥4.80，与订单报价一致",
      },
      {
        id: "log-2",
        action: "确认资料已装袋",
        target: "B5T9D",
        by: "管理员",
        time: "2026-10-29 17:35",
        reason: "逐项核对完成",
      },
      {
        id: "log-3",
        action: "创建配送批次",
        target: "晚间配送 · 东区",
        by: "管理员",
        time: "2026-10-29 17:40",
        reason: "安排今日晚间订单",
      },
    ],
    config: {
      serviceEnabled: true,
      announcement: "今日正常接单 · 请提前上传资料，留足打印与配送时间",
      prices: {
        bwSingle: 20,
        bwDuplex: 30,
        colorSingle: 100,
        colorDuplex: 160,
        photo: 200,
        corner: null,
        side: null,
      },
      wechat: "",
      phone: "",
      qrUrl: "",
      photoWidthMm: 127,
      photoHeightMm: 89,
      margins: { top: 3, bottom: 3, left: 3, right: 3 },
      a4Margins: { top: 3, bottom: 3, left: 3, right: 3 },
      maxFiles: 10,
      maxMb: 50,
      bindingApproved: false,
    },
  };
}
