/** Public price rows and admin inputs share names and billing units. */
export const SERVICE_PRICES = [
  { key: "bwSingle", label: "A4 黑白单面", unit: "张" },
  { key: "bwDuplex", label: "A4 黑白双面", unit: "张" },
  { key: "colorSingle", label: "A4 彩色单面", unit: "张" },
  { key: "colorDuplex", label: "A4 彩色双面", unit: "张" },
  { key: "photo", label: "5 寸照片", unit: "张" },
  { key: "corner", label: "左上角单钉", unit: "份" },
  { key: "side", label: "左侧双钉", unit: "份" },
  { key: "delivery", label: "配送费", unit: "次" },
] as const;
