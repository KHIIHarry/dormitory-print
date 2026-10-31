import { MapPin, Minus, Plus, LocateFixed } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Building } from "../lib/types";
export function CampusMap({
  buildings,
  value,
  onChange,
  editable = false,
  onPlace,
  onMove,
  mapUrl,
}: {
  buildings: Building[];
  value?: string;
  onChange?: (id: string) => void;
  editable?: boolean;
  onPlace?: (x: number, y: number) => void;
  onMove?: (id: string, x: number, y: number) => void;
  mapUrl?: string;
}) {
  const [zoom, setZoom] = useState(1);
  const mapRef = useRef<HTMLDivElement>(null);
  const [ratio, setRatio] = useState<number | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setSize({ width, height });
    });
    if (mapRef.current) observer.observe(mapRef.current);
    return () => observer.disconnect();
  }, []);
  const fitted =
    mapUrl && ratio
      ? {
          width: Math.min(size.width, size.height * ratio),
          height: Math.min(size.height, size.width / ratio),
        }
      : undefined;
  return (
    <div className="campus-map" ref={mapRef}>
      <div
        className="map-drawing"
        style={{
          ...fitted,
          transform: fitted
            ? `translate(-50%,-50%) scale(${zoom})`
            : `scale(${zoom})`,
          ...(fitted ? { position: "absolute", top: "50%", left: "50%" } : {}),
        }}
        onClick={(e) => {
          if (editable && onPlace && e.target === e.currentTarget) {
            const box = e.currentTarget.getBoundingClientRect();
            onPlace(
              ((e.clientX - box.left) / box.width) * 100,
              ((e.clientY - box.top) / box.height) * 100,
            );
          }
        }}
      >
        {mapUrl ? (
          <img
            src={mapUrl}
            alt="校园地图底图"
            className="map-base"
            onLoad={(e) =>
              setRatio(
                e.currentTarget.naturalWidth / e.currentTarget.naturalHeight,
              )
            }
          />
        ) : (
          <svg
            viewBox="0 0 700 420"
            aria-label="校园示意底图，非真实地理地图"
            preserveAspectRatio="none"
          >
            <rect width="700" height="420" fill="#F1F0E9" />
            <path
              d="M0 200H700M330 0V420M0 365H700M550 0V420M0 90H700"
              stroke="#fff"
              strokeWidth="23"
            />
            <path
              d="M0 200H700M330 0V420M0 365H700M550 0V420"
              stroke="#DDDCD5"
              strokeWidth="1"
              strokeDasharray="6 7"
            />
            <rect x="55" y="105" width="68" height="62" rx="6" fill="#E0E4D6" />
            <rect
              x="147"
              y="105"
              width="68"
              height="62"
              rx="6"
              fill="#E1DCD2"
            />
            <rect
              x="242"
              y="105"
              width="52"
              height="62"
              rx="6"
              fill="#E1DCD2"
            />
            <rect
              x="380"
              y="110"
              width="111"
              height="48"
              rx="6"
              fill="#E1DCD2"
            />
            <rect
              x="75"
              y="237"
              width="112"
              height="83"
              rx="8"
              fill="#DCE4D4"
            />
            <rect
              x="212"
              y="238"
              width="75"
              height="83"
              rx="6"
              fill="#E1DCD2"
            />
            <rect
              x="381"
              y="240"
              width="112"
              height="80"
              rx="6"
              fill="#E1DCD2"
            />
            <rect
              x="580"
              y="234"
              width="86"
              height="90"
              rx="35"
              fill="#DCE4D4"
            />
            <path
              d="M590 260h65M590 280h65M590 300h65"
              stroke="#BFCFB6"
              strokeWidth="2"
            />
            <text x="70" y="53" fill="#838878" fontSize="12">
              知行苑
            </text>
            <text x="390" y="52" fill="#838878" fontSize="12">
              教学区
            </text>
            <text x="90" y="284" fill="#919B83" fontSize="12">
              校园绿地
            </text>
            <text x="580" y="356" fill="#919B83" fontSize="12">
              运动场
            </text>
            <text x="350" y="390" fill="#9D9B93" fontSize="11">
              学 府 路
            </text>
          </svg>
        )}
        {buildings
          .filter((b) => editable || b.enabled)
          .map((b) => (
            <button
              key={b.id}
              className={`map-hotspot ${value === b.id ? "selected" : ""} ${!b.enabled ? "disabled" : ""}`}
              style={{ left: `${b.x}%`, top: `${b.y}%` }}
              onPointerDown={(e) => {
                if (editable) {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  onChange?.(b.id);
                }
              }}
              onPointerMove={(e) => {
                if (!editable || !onMove || e.buttons !== 1) return;
                const box =
                  e.currentTarget.parentElement!.getBoundingClientRect();
                onMove(
                  b.id,
                  Math.max(
                    0,
                    Math.min(100, ((e.clientX - box.left) / box.width) * 100),
                  ),
                  Math.max(
                    0,
                    Math.min(100, ((e.clientY - box.top) / box.height) * 100),
                  ),
                );
              }}
              onClick={(e) => {
                e.stopPropagation();
                onChange?.(b.id);
              }}
            >
              <MapPin size={20} fill="currentColor" />
              <span>{b.name}</span>
            </button>
          ))}
      </div>
      <span className="map-label">校园配送示意图</span>
      <div className="map-controls">
        <button
          aria-label="放大地图"
          onClick={() => setZoom((z) => Math.min(1.6, z + 0.2))}
        >
          <Plus size={16} />
        </button>
        <button
          aria-label="缩小地图"
          onClick={() => setZoom((z) => Math.max(1, z - 0.2))}
        >
          <Minus size={16} />
        </button>
      </div>
      {editable && (
        <span className="map-edit-hint">点击地图空白处添加楼栋热点</span>
      )}
    </div>
  );
}
