"use client";

import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";

type Station = ApiSchemas["SubsidyAnnualStation"];
type Input = ApiSchemas["SubsidyAnnualInput"];
type Result = ApiSchemas["SubsidyAnnualResult"];
type Intent = { payload: Input; files: File[]; stations: Station[] };

export function SubsidyAnnual({
  csrf,
  policies,
  canSubmit,
  canMaterials,
  parentBusy,
  onBusy,
  onCreated,
}: {
  csrf: string;
  policies: ApiSchemas["SubsidyPolicyResponse"][];
  canSubmit: boolean;
  canMaterials: boolean;
  parentBusy: boolean;
  onBusy: (value: boolean) => void;
  onCreated: () => void;
}) {
  const revision = usePageRefresh();
  const [q, setQ] = useState(""),
    [district, setDistrict] = useState("");
  const [query, setQuery] = useState({ q: "", district: "", page: 1 });
  const [listing, setListing] = useState<
    ApiSchemas["SubsidyAnnualStations"] | null
  >(null);
  const [year, setYear] = useState<number | null>(null),
    [policyId, setPolicyId] = useState("");
  const [selection, setSelection] = useState<Record<number, Station>>({});
  const [note, setNote] = useState(""),
    [files, setFiles] = useState<File[]>([]);
  const [intent, setIntent] = useState<Intent | null>(null),
    [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(true),
    [sending, setSending] = useState(false);
  const [error, setError] = useState(""),
    [readError, setReadError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null),
    inFlight = useRef(false);
  const locked = parentBusy || sending || !!intent;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setReadError("");
    typedApi
      .GET("/api/v1/subsidy/annual-stations", {
        signal: controller.signal,
        cache: "no-store",
        params: { query: { ...query, page_size: 10 } },
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "年度场站读取失败"));
        if (query.page > Math.max(1, Math.ceil(data.total / 10))) {
          setQuery((previous) => ({
            ...previous,
            page: Math.max(1, Math.ceil(data.total / 10)),
          }));
          return;
        }
        setListing(data);
        setYear((previous) => previous ?? data.default_year);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setReadError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision]);
  useEffect(() => {
    onBusy(!!intent || sending);
  }, [intent, sending, onBusy]);
  const selected = Object.values(selection);
  const available = policies.filter(
    (policy) =>
      policy.claim_supported &&
      policy.enabled &&
      policy.is_demo &&
      year !== null &&
      policy.effective_from <= `${year}-01-01` &&
      policy.effective_to >= `${year}-12-31`,
  );
  const effectivePolicy =
    available.find((p) => String(p.id) === policyId) ?? available[0];
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  function prepare(mode: "draft" | "submit") {
    if (locked || !year || !effectivePolicy || !selected.length) return;
    setError("");
    if (
      files.length > 10 ||
      totalBytes > 20 * 1024 * 1024 ||
      totalBytes * selected.length > 100 * 1024 * 1024
    ) {
      setError("材料最多10份、每站总量20MiB，按场站复制后的整批材料最多100MiB");
      return;
    }
    setResult(null);
    setIntent({
      payload: {
        request_key: crypto.randomUUID(),
        application_year: year,
        policy_id: effectivePolicy.id,
        station_ids: selected.map((station) => station.id),
        application_note: note,
        mode,
      },
      files: [...files],
      stations: selected,
    });
  }
  async function send() {
    if (!intent || inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setError("");
    const form = new FormData();
    form.set("payload", JSON.stringify(intent.payload));
    for (const file of intent.files) form.append("files", file);
    try {
      const response = await typedApi.POST("/api/v1/subsidy/annual-batches", {
        headers: { "x-csrf-token": csrf },
        body: { payload: JSON.stringify(intent.payload) },
        bodySerializer: () => form,
      });
      if (!response.data)
        throw new Error(
          apiError(
            response.error,
            "整批申请未确认成功，请保留本确认清单重试或核对记录",
          ),
        );
      setResult(response.data);
      setIntent(null);
      setSelection({});
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
      onCreated();
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "请求未确认，请用同一清单重试",
      );
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }
  return (
    <section className="panel annual-subsidy" aria-label="年度多场站申报">
      <div className="panel-title">
        <div>
          <h2>年度补贴申报</h2>
          <p>选择年度和场站，一次生成每站独立申请，全部成功或全部回滚</p>
        </div>
        <span className="tag">已选 {selected.length} / 20 座</span>
      </div>
      <div className="business-form">
        <label>
          补贴年度
          <input
            aria-label="补贴年度"
            type="number"
            min={2000}
            max={listing?.current_year}
            value={year ?? ""}
            disabled={locked}
            onChange={(event) => {
              setYear(event.target.value ? Number(event.target.value) : null);
              setPolicyId("");
            }}
          />
        </label>
        <label>
          年度政策版本
          <select
            aria-label="年度政策版本"
            value={effectivePolicy?.id ?? ""}
            disabled={locked || !available.length}
            onChange={(event) => setPolicyId(event.target.value)}
          >
            {!available.length && (
              <option value="">该年度暂无完整有效政策</option>
            )}
            {available.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} V{p.version}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="inline-note">
        默认上一北京时间年度，核算周期为1月1日至12月31日。当前为演示核算；无对应政策或电量时不能创建，不自动补造数据。
      </p>
      <form
        className="business-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!locked) setQuery({ q, district, page: 1 });
        }}
      >
        <label>
          场站名称
          <input
            aria-label="年度场站名称"
            value={q}
            disabled={locked}
            maxLength={150}
            onChange={(event) => setQ(event.target.value)}
          />
        </label>
        <label>
          区域
          <input
            aria-label="年度场站区域"
            value={district}
            disabled={locked}
            maxLength={80}
            onChange={(event) => setDistrict(event.target.value)}
          />
        </label>
        <button className="outline" disabled={locked}>
          查询年度场站
        </button>
        <button
          className="outline"
          type="button"
          disabled={locked}
          onClick={() => {
            setQ("");
            setDistrict("");
            setQuery({ q: "", district: "", page: 1 });
          }}
        >
          重置场站查询
        </button>
      </form>
      {readError && (
        <p role="alert" className="error">
          {readError}
        </p>
      )}
      {loading ? (
        <p className="empty">正在读取可选场站…</p>
      ) : (
        !readError && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "场站编码 / 名称",
                      "区域",
                      "终端数量",
                      "终端总功率",
                      "选择",
                    ].map((h) => (
                      <th key={h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {listing?.items.map((station) => (
                    <tr key={station.id}>
                      <td>
                        #{station.id} · {station.name}
                      </td>
                      <td>{station.district}</td>
                      <td>{station.equipment_count}</td>
                      <td>{station.equipment_power_kw ?? "未知"} kW</td>
                      <td>
                        <button
                          className="outline"
                          type="button"
                          aria-label={`选择年度场站 ${station.name}`}
                          disabled={
                            locked ||
                            !station.selectable ||
                            !!selection[station.id] ||
                            selected.length >= 20
                          }
                          onClick={() =>
                            setSelection((previous) => ({
                              ...previous,
                              [station.id]: station,
                            }))
                          }
                        >
                          {selection[station.id] ? "已选择" : "选择"}
                        </button>
                        {station.unavailable_reason && (
                          <p className="inline-note">
                            {station.unavailable_reason}
                          </p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {listing?.items.length === 0 && (
                <p className="empty">暂无符合查询条件的已备案场站</p>
              )}
            </div>
            <div className="business-form">
              <button
                className="outline"
                type="button"
                disabled={locked || query.page <= 1}
                onClick={() => setQuery({ ...query, page: query.page - 1 })}
              >
                年度场站上一页
              </button>
              <span>
                第{query.page}页 · 共{listing?.total ?? 0}座
              </span>
              <button
                className="outline"
                type="button"
                disabled={locked || query.page * 10 >= (listing?.total ?? 0)}
                onClick={() => setQuery({ ...query, page: query.page + 1 })}
              >
                年度场站下一页
              </button>
            </div>
          </>
        )
      )}
      <div className="annual-selected">
        <h3>已选场站</h3>
        <p className="inline-note">
          查询和翻页保留选择；取消请在此移除。服务端创建时会重新检查资格和权限。
        </p>
        {selected.map((station) => (
          <span className="annual-chip" key={station.id}>
            {station.name} · #{station.id}
            <button
              type="button"
              disabled={locked}
              aria-label={`移除年度场站 ${station.name}`}
              onClick={() =>
                setSelection((previous) => {
                  const next = { ...previous };
                  delete next[station.id];
                  return next;
                })
              }
            >
              移除
            </button>
          </span>
        ))}
        {!!selected.length && (
          <button
            className="outline"
            type="button"
            disabled={locked}
            onClick={() => setSelection({})}
          >
            全部移除年度场站
          </button>
        )}
        {!selected.length && <p className="empty">尚未选择场站</p>}
      </div>
      <div className="business-form">
        <label className="annual-note">
          申请说明
          <textarea
            aria-label="年度申请说明"
            maxLength={2000}
            value={note}
            disabled={locked}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        {canMaterials && (
          <label>
            ZIP申报材料
            <input
              ref={fileInput}
              aria-label="年度ZIP材料"
              type="file"
              accept=".zip"
              multiple
              disabled={locked}
              onChange={(event) =>
                setFiles(Array.from(event.target.files ?? []))
              }
            />
          </label>
        )}
      </div>
      <p className="inline-note">
        每站最多10份ZIP/20MiB，整批独立存储最多100MiB；不解压。共选
        {files.length}份，提交后每站资料分别冻结；草稿允许暂缺。
      </p>
      <div className="business-form">
        <button
          className="outline"
          disabled={locked || !selected.length || !effectivePolicy}
          onClick={() => prepare("draft")}
        >
          暂存年度草稿
        </button>
        {canSubmit && (
          <button
            className="primary"
            disabled={
              locked ||
              !selected.length ||
              !effectivePolicy ||
              !note.trim() ||
              !files.length ||
              !canMaterials
            }
            onClick={() => prepare("submit")}
          >
            提交年度申请
          </button>
        )}
      </div>
      {intent && (
        <div className="annual-confirm" role="region" aria-label="年度申报确认">
          <h3>
            确认{intent.payload.application_year}年度 · {intent.stations.length}
            座场站
          </h3>
          <p>
            {intent.stations
              .map((station) => `${station.name} (#${station.id})`)
              .join("；")}
          </p>
          <p>
            {intent.payload.mode === "submit" ? "创建并提交审核" : "仅保存草稿"}{" "}
            · 共{intent.files.length}份ZIP
          </p>
          <p className="inline-note">
            每站生成独立申请；某站失败则整批回滚。网络中断后可保留此清单用同一请求编号重试，不会重复创建。回执仅代表创建时状态。
          </p>
          <button
            className="outline"
            disabled={sending}
            onClick={() => {
              setIntent(null);
              setError("");
            }}
          >
            返回修改年度清单
          </button>{" "}
          <button className="primary" disabled={sending} onClick={send}>
            {sending ? "正在整批办理…" : "确认办理年度申请"}
          </button>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="annual-confirm" role="status">
          <h3>年度申请已{result.replayed ? "核对原回执" : "成功生成"}</h3>
          <p>
            批次 #{result.receipt.batch_id} · {result.receipt.application_year}
            年度 · 共{result.receipt.items.length}条
          </p>
          <p>
            {result.receipt.items.map((claim) => `SUB-${claim.id}`).join("、")}
          </p>
          <p className="inline-note">
            以上为创建回执，当前状态及材料请到下方申请记录查看。
          </p>
        </div>
      )}
    </section>
  );
}
