"use client";

import { useState } from "react";
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
          <EnergyHistory key={stationId} stationId={stationId} />
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
