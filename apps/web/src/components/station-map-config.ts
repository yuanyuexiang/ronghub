export const tileURL = process.env.NEXT_PUBLIC_MAP_TILE_URL || "";
export const attribution = process.env.NEXT_PUBLIC_MAP_TILE_ATTRIBUTION || "";
export const hasBasemap =
  !!attribution &&
  (tileURL.startsWith("https://") ||
    (tileURL.startsWith("/") && !tileURL.startsWith("//")));
