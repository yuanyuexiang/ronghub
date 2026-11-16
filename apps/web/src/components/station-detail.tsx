"use client";
import { apiFetch } from "../lib/api-fetch";
import { useEffect, useRef, useState } from "react";
type Device = {
  id: number;
  name: string;
  power_kw: string;
  connectors: { id: number; external_id: string; status: string }[];
};
export function StationDetail({
  stationId,
  onClose,
}: {
  stationId: number;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [devices, setDevices] = useState<Device[]>([]),
    [station, setStation] = useState<{
      name: string;
      address: string;
      status: string;
      power_kw: string;
    } | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    Promise.all([
      apiFetch(`/api/v1/stations/${stationId}`, { cache: "no-store" }),
      apiFetch(`/api/v1/stations/${stationId}/equipment`, {
        cache: "no-store",
      }),
    ])
      .then(async (rs) => {
        if (rs.some((r) => !r.ok)) throw new Error("场站详情读取失败");
        const [s, e] = await Promise.all(rs.map((r) => r.json()));
        if (active) {
          setStation(s);
          setDevices(e.items);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [stationId]);
  const labels: Record<string, string> = {
    available: "空闲",
    charging: "充电中",
    unknown: "状态未知",
    faulted: "故障",
  };
  return (
    <dialog
      ref={dialog}
      className="station-dialog"
      onCancel={onClose}
      aria-labelledby="station-detail-title"
    >
      <div className="panel-title">
        <div>
          <h2 id="station-detail-title">{station?.name || "场站与设备详情"}</h2>
          <p>{station?.address || "正在读取场站信息"}</p>
        </div>
        <button className="outline" onClick={onClose}>
          关闭详情
        </button>
      </div>
      {loading ? (
        <p className="empty">正在加载设备…</p>
      ) : error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : (
        <div className="device-list">
          <p className="muted">
            装机功率 {station?.power_kw} kW · 设备状态与业务审批状态分别管理
          </p>
          {devices.length ? (
            devices.map((d) => (
              <article className="device" key={d.id}>
                <h3>
                  {d.name} <small>{d.power_kw} kW</small>
                </h3>
                <div className="connector-grid">
                  {d.connectors.map((c) => (
                    <div key={c.id}>
                      <b>{c.external_id}</b>
                      <em
                        className={c.status === "charging" ? "green" : "amber"}
                      >
                        {labels[c.status] || c.status}
                      </em>
                    </div>
                  ))}
                </div>
              </article>
            ))
          ) : (
            <p className="empty">尚未同步设备明细，接口数量仅为台账汇总</p>
          )}
        </div>
      )}
    </dialog>
  );
}
