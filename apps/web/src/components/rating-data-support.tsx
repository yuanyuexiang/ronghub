"use client";

import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { businessStates } from "./station-map-types";
import { StationPicker } from "./station-picker";
import { EnergyHistory } from "./station-observations";
import { StationDetail } from "./station-detail";

/** Reuse the source-separated, authorized station history rather than estimating daily facts. */
export function RatingDataSupport() {
  const [draft, setDraft] = useState("");
  const [stationId, setStationId] = useState<number | null>(null);
  const [showProfile, setShowProfile] = useState(false);
  return (
    <section
      className="panel rating-data-support"
      aria-label="评价数据支撑工作区"
    >
      <div className="panel-title">
        <div>
          <h2>数据支撑评价</h2>
          <p>选择授权场站，核对基本档案和独立来源的每日观测。</p>
        </div>
        {stationId !== null && (
          <button
            type="button"
            className="outline"
            onClick={() => setShowProfile(true)}
          >
            查看场站档案
          </button>
        )}
      </div>
      <form
        className="business-form"
        onSubmit={(event) => {
          event.preventDefault();
          setStationId(Number(draft));
        }}
      >
        <StationPicker label="评价数据场站" value={draft} onChange={setDraft} />
        <button className="primary" disabled={!draft}>
          查询评价支撑数据
        </button>
      </form>
      {stationId === null ? (
        <p className="empty">请选择场站后查询评价依据</p>
      ) : (
        <div className="rating-data-history">
          <StationSupportProfile
            key={`profile-${stationId}`}
            stationId={stationId}
          />
          <EnergyHistory key={stationId} stationId={stationId} ratingColumns />
        </div>
      )}
      <p className="rating-data-notice muted">
        缺观测不代表电量为零。台账为当前资料，历史电量按来源独立列示；缺少的充电时长、次数及正式评分口径不以示例数值代替。此页不执行评分或公示。
      </p>
      {stationId !== null && showProfile && (
        <StationDetail
          stationId={stationId}
          onClose={() => setShowProfile(false)}
        />
      )}
    </section>
  );
}

function StationSupportProfile({ stationId }: { stationId: number }) {
  const [profile, setProfile] = useState<ApiSchemas["StationDetail"] | null>(
      null,
    ),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const c = new AbortController();
    setProfile(null);
    setError("");
    typedApi
      .GET("/api/v1/stations/{station_id}", {
        params: { path: { station_id: stationId } },
        signal: c.signal,
        cache: "no-store",
      })
      .then((r) => {
        if (c.signal.aborted) return;
        if (!r.data) throw Error(apiError(r.error, "场站概况读取失败"));
        setProfile(r.data);
      })
      .catch((e) => {
        if (!c.signal.aborted)
          setError(e instanceof Error ? e.message : "读取失败");
      });
    return () => c.abort();
  }, [stationId, revision]);
  return (
    <section aria-label="评价支撑场站概况" className="panel">
      <h3>场站概况</h3>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {profile ? (
        <dl className="station-summary-grid">
          {[
            ["场站编号", profile.external_id],
            ["场站名称", profile.name],
            ["所属区域", profile.district],
            ["详细地址", profile.address],
            ["业务状态", businessStates[profile.status] ?? profile.status],
            ["额定功率", `${profile.power_kw} kW`],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || "未提供"}</dd>
            </div>
          ))}
        </dl>
      ) : (
        !error && <p role="status">正在读取场站概况…</p>
      )}
    </section>
  );
}
