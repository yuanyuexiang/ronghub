"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { StationPicker } from "./station-picker";
import { EnergyHistory } from "./station-observations";
import { RatingPublications, resultStates } from "./rating-publications";

type Station = ApiSchemas["StationDetail"];
type Publication = ApiSchemas["RatingPublication"];
export function RatingStationResults({
  role,
  onOpen,
  busy,
  revision,
}: {
  role: string;
  onOpen: (id: number) => void;
  busy: boolean;
  revision: number;
}) {
  const canStations = useCapability("stations.read"),
    canStatistics = useCapability("statistics.read"),
    canDelist = useCapability("ratings.delist");
  const [draft, setDraft] = useState(""),
    [stationId, setStationId] = useState<number | null>(null);
  const [station, setStation] = useState<Station | null>(null),
    [result, setResult] = useState<Publication | null>(null),
    [score, setScore] = useState<string | null>(null);
  const [operatorName, setOperatorName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [help, setHelp] = useState(false);
  const refresh = usePageRefresh();
  useEffect(() => {
    if (stationId === null || !canStations) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setStation(null);
    setOperatorName(null);
    setResult(null);
    setScore(null);
    setHelp(false);
    (async () => {
      const [profile, publication, operators] = await Promise.all([
        typedApi.GET("/api/v1/stations/{station_id}", {
          params: { path: { station_id: stationId } },
          signal: controller.signal,
          cache: "no-store",
        }),
        typedApi.GET("/api/v1/ratings/publications", {
          params: { query: { station_id: stationId, page_size: 1 } },
          signal: controller.signal,
          cache: "no-store",
        }),
        typedApi.GET("/api/v1/operators", {
          signal: controller.signal,
          cache: "no-store",
        }),
      ]);
      if (!profile.data || !publication.data || !operators.data)
        throw Error(
          apiError(
            profile.error ?? publication.error ?? operators.error,
            "场站结果读取失败",
          ),
        );
      const item = publication.data.items[0] ?? null;
      let total: string | null = null;
      if (item) {
        const detail = await typedApi.GET(
          "/api/v1/ratings/applications/{application_id}",
          {
            params: { path: { application_id: item.id } },
            signal: controller.signal,
            cache: "no-store",
          },
        );
        if (!detail.data)
          throw Error(apiError(detail.error, "评定档案读取失败"));
        total = detail.data.calculation.total ?? null;
      }
      if (!controller.signal.aborted) {
        setStation(profile.data);
        setOperatorName(
          operators.data.items.find(
            (row) => row.id === profile.data.operator_id,
          )?.name ?? null,
        );
        setResult(item);
        setScore(total);
      }
    })()
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [stationId, refresh, revision, canStations]);
  return (
    <section
      className="panel rating-station-results"
      aria-label="场站评价结果工作区"
    >
      <div className="panel-title">
        <div>
          <h2>{role === "association" ? "评价结果管理" : "评审结果查看"}</h2>
          <p>按场站核对当前结果、基本资料与每日运营数据</p>
        </div>
      </div>
      {canStations ? (
        <>
          <form
            className="business-form result-station-query"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft) setStationId(Number(draft));
            }}
          >
            <StationPicker
              label="评价结果场站"
              value={draft}
              onChange={setDraft}
            />
            <button className="primary" disabled={!draft || busy || loading}>
              查询场站结果
            </button>
          </form>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {loading && (
            <p className="empty" role="status">
              正在读取场站评价结果…
            </p>
          )}
          {!stationId && (
            <p className="empty">请选择场站，查看评价结果和运营数据。</p>
          )}
          {station && (
            <>
              <article
                className="rating-station-card"
                aria-label="场站概况与评价结果"
              >
                <div className="rating-station-profile">
                  <h3>{station.name}</h3>
                  <p>
                    {station.district} · {station.address}
                  </p>
                  <dl>
                    <div>
                      <dt>运营商</dt>
                      <dd>
                        {result?.operator_name ?? operatorName ?? "名称未提供"}
                      </dd>
                    </div>
                    <div>
                      <dt>场站功率</dt>
                      <dd>{station.power_kw} kW</dd>
                    </div>
                    <div>
                      <dt>联系人</dt>
                      <dd>
                        {station.contact_accessible
                          ? (station.contact?.name ?? "来源未提供")
                          : "当前岗位不可查看"}
                      </dd>
                    </div>
                    <div>
                      <dt>联系电话</dt>
                      <dd>
                        {station.contact_accessible
                          ? (station.contact?.phone ?? "来源未提供")
                          : "当前岗位不可查看"}
                      </dd>
                    </div>
                  </dl>
                  <div className="rating-live-states" aria-label="枪口状态">
                    <span>占用 · 待实时来源</span>
                    <span>充电 · 待实时来源</span>
                    <span>空闲 · 待实时来源</span>
                    <span>离线 · 待实时来源</span>
                    <span>故障 · 待实时来源</span>
                  </div>
                </div>
                <div className="rating-station-grade">
                  <span>评审总分</span>
                  <strong>{score ?? "未留存"}</strong>
                  <b>
                    {result?.effective_stars == null
                      ? "尚未定级"
                      : `${result.effective_stars} 星`}
                  </b>
                  <p>
                    {result
                      ? (resultStates[result.result_state] ??
                        result.result_state)
                      : "暂无已提交评定结果"}
                  </p>
                  <small>有效期至：{result?.valid_until ?? "未确认"}</small>
                  {result && (
                    <div className="action-row">
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={() => onOpen(result.id)}
                      >
                        查看评定档案
                      </button>
                      {role === "association" && (
                        <>
                          <button
                            className="outline"
                            onClick={() => setHelp(!help)}
                          >
                            升降级
                          </button>
                          {canDelist && result.result_state !== "delisted" && (
                            <button
                              className="outline"
                              disabled={busy}
                              onClick={() => onOpen(result.id)}
                            >
                              摘牌办理
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              </article>
              {help && (
                <p className="progress-readonly-note">
                  现有升降级采用复评流程：运营商发起复评，协会复核后公示新结果。已公示历史不会被直接改分；可在评定档案追溯原结果。
                </p>
              )}
              <section className="rating-result-history">
                <h3>每日运营数据</h3>
                {canStatistics ? (
                  <EnergyHistory
                    key={stationId}
                    stationId={station.id}
                    ratingColumns
                  />
                ) : (
                  <p className="empty">当前岗位无运营统计查看权限</p>
                )}
              </section>
            </>
          )}
        </>
      ) : (
        <p className="empty">
          当前岗位无场站档案查看权限，可在结果目录中查阅授权结果。
        </p>
      )}
      <details className="rating-result-directory">
        <summary>场站结果目录</summary>
        <RatingPublications
          title="场站结果目录"
          layout="table"
          testIdPrefix="rating-result"
          revision={revision}
          onOpen={onOpen}
          busy={busy}
        />
      </details>
    </section>
  );
}
