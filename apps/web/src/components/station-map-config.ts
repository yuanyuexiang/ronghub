const key = process.env.NEXT_PUBLIC_TIANDITU_KEY?.trim() || "";
const customURL = process.env.NEXT_PUBLIC_MAP_TILE_URL || "";
const customAttribution = process.env.NEXT_PUBLIC_MAP_TILE_ATTRIBUTION || "";
const tianditu = /^[a-f0-9]{32}$/i.test(key);

export type BasemapLayer = {
  url: string;
  attribution?: string;
  maxNativeZoom?: number;
  pane?: string;
  subdomains?: string;
};
const wmts = (layer: "vec" | "cva") =>
  `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${key}`;

export const basemapLayers: BasemapLayer[] = tianditu
  ? [
      {
        url: wmts("vec"),
        attribution:
          '&copy; <a href="https://www.tianditu.gov.cn/" target="_blank" rel="noopener noreferrer">天地图</a>',
        maxNativeZoom: 18,
        subdomains: "01234567",
      },
      {
        url: wmts("cva"),
        maxNativeZoom: 18,
        pane: "basemapLabels",
        subdomains: "01234567",
      },
    ]
  : customAttribution &&
      (customURL.startsWith("https://") ||
        (customURL.startsWith("/") && !customURL.startsWith("//")))
    ? [{ url: customURL, attribution: customAttribution }]
    : [];
export const hasBasemap = basemapLayers.length > 0;
export const basemapName = tianditu ? "天地图 · 道路与地名" : "场站分布地图";
