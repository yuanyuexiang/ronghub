"use client";
import { useEffect, useRef } from "react";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapPoint, StationPoint, Viewport } from "./station-map-types";

import { basemapLayers, hasBasemap as validTiles } from "./station-map-config";
export default function StationMapCanvas({
  items,
  selectedId,
  onSelect,
  onViewport,
  focus,
  reset,
  onTileError,
}: {
  items: MapPoint[];
  selectedId: number | null;
  onSelect: (point: StationPoint) => void;
  onViewport: (view: Viewport) => void;
  focus: { longitude: number; latitude: number; nonce: number } | null;
  reset: number;
  onTileError: (failed: boolean) => void;
}) {
  const container = useRef<HTMLDivElement>(null),
    mapRef = useRef<L.Map | null>(null),
    layer = useRef<L.LayerGroup | null>(null);
  const callbacks = useRef({ onSelect, onViewport, onTileError });
  callbacks.current = { onSelect, onViewport, onTileError };
  useEffect(() => {
    if (!container.current) return;
    const map = L.map(container.current, {
      minZoom: 2,
      maxZoom: 19,
      zoomControl: false,
      maxBounds: [
        [-85, -180],
        [85, 180],
      ],
      maxBoundsViscosity: 1,
    }).setView([26.0745, 119.2965], 11);
    mapRef.current = map;
    L.control
      .zoom({ position: "topright", zoomInTitle: "放大", zoomOutTitle: "缩小" })
      .addTo(map);
    L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
    if (validTiles) {
      // Labels stay below station markers and never intercept pointer interactions.
      const labels = map.createPane("basemapLabels");
      labels.style.zIndex = "250";
      labels.style.pointerEvents = "none";
      const states = basemapLayers.map(() => ({ failed: false }));
      const reportTiles = () =>
        callbacks.current.onTileError(states.some((state) => state.failed));
      basemapLayers.forEach(({ url, ...options }, index) => {
        L.tileLayer(url, { maxZoom: 19, noWrap: true, ...options })
          .on("loading", () => {
            states[index].failed = false;
          })
          .on("tileerror", () => {
            states[index].failed = true;
            reportTiles();
          })
          .on("load", reportTiles)
          .addTo(map);
      });
    } else {
      class Grid extends L.GridLayer {
        createTile(coords: L.Coords) {
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 256;
          const ctx = canvas.getContext("2d")!;
          ctx.fillStyle = "#eef3f8";
          ctx.fillRect(0, 0, 256, 256);
          ctx.strokeStyle = "#dde6f0";
          ctx.lineWidth = 1;
          for (let i = 0; i <= 256; i += 32) {
            ctx.beginPath();
            ctx.moveTo(i, 0);
            ctx.lineTo(i, 256);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(0, i);
            ctx.lineTo(256, i);
            ctx.stroke();
          }
          const coordinate = map.unproject(
            [coords.x * 256, coords.y * 256],
            coords.z,
          );
          ctx.fillStyle = "#91a6ba";
          ctx.font = "11px system-ui";
          ctx.fillText(
            `${coordinate.lat.toFixed(3)}°N  ${coordinate.lng.toFixed(3)}°E`,
            12,
            22,
          );
          return canvas;
        }
      }
      new Grid({ attribution: "坐标视图 · 无道路或行政边界底图" }).addTo(map);
    }
    layer.current = L.layerGroup().addTo(map);
    const report = () => {
      const b = map.getBounds();
      const rounded = (n: number) => Math.round(n * 1e6) / 1e6;
      callbacks.current.onViewport({
        west: Math.max(-180, rounded(b.getWest())),
        south: Math.max(-85, rounded(b.getSouth())),
        east: Math.min(180, rounded(b.getEast())),
        north: Math.min(85, rounded(b.getNorth())),
        zoom: map.getZoom(),
      });
    };
    map.on("moveend", report);
    map.whenReady(report);
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      map.off("moveend", report);
      map.remove();
      mapRef.current = null;
      layer.current = null;
    };
  }, []);
  useEffect(() => {
    const map = mapRef.current,
      group = layer.current;
    if (!map || !group) return;
    group.clearLayers();
    items.forEach((point) => {
      const clustered = point.kind === "cluster";
      const state = clustered ? "cluster" : point.runtime_state;
      const marker = L.marker(
        [Number(point.latitude), Number(point.longitude)],
        {
          icon: L.divIcon({
            className: `ronghub-map-marker ${clustered ? "cluster" : "station"} ${!clustered && point.id === selectedId ? "selected" : ""}`,
            html: clustered
              ? `<span>${Math.max(1, Number(point.count))}</span>`
              : `<span class="map-dot state-${["fault", "charging", "available", "offline", "occupied"].includes(state) ? state : "unknown"}"></span>`,
            iconSize: clustered ? [42, 42] : [28, 28],
            iconAnchor: clustered ? [21, 21] : [14, 14],
          }),
          keyboard: true,
        },
      ).addTo(group);
      const label = clustered
        ? `聚合${point.count}个场站，点击放大`
        : `场站：${point.name}`;
      marker.getElement()?.setAttribute("aria-label", label);
      const text = document.createElement("span");
      text.textContent = label;
      marker.bindTooltip(text);
      marker.on("click", () => {
        if (clustered) {
          const [west, south, east, north] = point.bounds;
          if (west === east && south === north)
            map.setView(
              [Number(point.latitude), Number(point.longitude)],
              Math.min(19, map.getZoom() + 3),
            );
          else
            map.fitBounds(
              [
                [south, west],
                [north, east],
              ],
              { maxZoom: Math.min(19, map.getZoom() + 3), padding: [45, 45] },
            );
        } else callbacks.current.onSelect(point);
      });
    });
  }, [items, selectedId]);
  useEffect(() => {
    if (focus)
      mapRef.current?.setView(
        [focus.latitude, focus.longitude],
        Math.max(14, mapRef.current.getZoom()),
      );
  }, [focus]);
  useEffect(() => {
    if (reset) mapRef.current?.setView([26.0745, 119.2965], 11);
  }, [reset]);
  return (
    <div
      className="station-map-container"
      ref={container}
      role="region"
      aria-label="场站交互地图"
    />
  );
}
