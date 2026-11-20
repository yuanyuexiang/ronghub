"use client";
import {
  WorkspaceViews,
  useWorkspaceLeave,
  type LeaveGuardRegistrar,
} from "./business-workspace";
import { useRecordFocus } from "./use-record-focus";
import { usePageRefresh } from "./page-refresh";
import { BusinessExports } from "./business-exports";
import {
  useBusinessQueue,
  loadRatingQueue,
  QueueFilters,
  submissionTime,
  QueuePagination,
} from "./business-queue";
import { ratingApi } from "../lib/business-api";
import type { ApiSchemas } from "../lib/typed-api";
import { useCapability, useGlobalScope } from "./permissions";
import { StationPicker } from "./station-picker";
import { useEffect, useState } from "react";
import { RatingPublications, resultStates } from "./rating-publications";
import { RatingDataSupport } from "./rating-data-support";
import { RatingMaterials } from "./rating-materials";
import { RatingStandardWorkbench } from "./rating-standard-workbench";
import { RatingStandardAvailability } from "./rating-standard-availability";
type Standard = ApiSchemas["RatingStandardResponse"];
type Application = ApiSchemas["RatingApplicationDetail"];
const ratingFieldNames: Record<string, string> = {
  expert_score: "专家/材料评分原值",
  station_power_kw: "当前台账场站总功率",
  equipment_count: "当前库中设备数量",
  connector_count: "当前库中枪口数量",
};
function participates(
  rules: ApiSchemas["RatingRulesResponse"],
  indicator: ApiSchemas["RatingIndicatorResponse"],
) {
  return rules.indicator_status_version !== 1 || indicator.enabled === true;
}
const statuses: Record<string, string> = {
  draft: "草稿",
  submitted: "待评审",
  reviewed: "已复核待公示",
  published: "已公示",
  rejected: "已驳回",
  withdrawn: "已撤回",
  delisted: "已摘牌",
  superseded: "已替代",
};
const actions: Record<string, string> = {
  create: "创建申报",
  edit: "修改申报",
  submit: "提交申报",
  review: "人工复核",
  publish: "结果公示",
  reject: "驳回申请",
  withdraw: "撤回申请",
  delist: "摘牌",
  supersede: "新结果替代",
};
const kinds: Record<string, string> = {
  initial: "初次评星",
  renewal: "过期评星",
  reassessment: "复评 / 升降级",
  appeal: "异议申诉",
};
export function RatingPanel({
  role,
  csrf,
  onChanged,
  registerLeaveGuard,
}: {
  role: string;
  csrf: string;
  onChanged: () => void;
  registerLeaveGuard?: LeaveGuardRegistrar;
}) {
  const canStatistics = useCapability("statistics.read");
  const canStations = useCapability("stations.read");
  const canDataSupport = canStatistics && canStations;
  const api = ratingApi(csrf, onChanged);
  const queue = useBusinessQueue(loadRatingQueue);
  const apps = queue.rows;

  const globalScope = useGlobalScope();
  const canCreate = useCapability("ratings.create"),
    canEdit = useCapability("ratings.edit"),
    canMaterials = useCapability("ratings.materials"),
    canSubmit = useCapability("ratings.submit"),
    canWithdraw = useCapability("ratings.withdraw"),
    canReview = useCapability("ratings.review"),
    canPublish = useCapability("ratings.publish"),
    canDelist = useCapability("ratings.delist"),
    canStandards = useCapability("ratings.standards");
  const [standards, setStandards] = useState<Standard[]>([]),
    [sid, setSid] = useState(""),
    [standardId, setStandardId] = useState(""),
    [scores, setScores] = useState<Record<string, string>>({}),
    [note, setNote] = useState(""),
    [detail, setDetail] = useState<Application | null>(null),
    [reviewScores, setReviewScores] = useState<Record<string, string>>({}),
    [comment, setComment] = useState(""),
    [editNote, setEditNote] = useState(""),
    [validUntil, setValidUntil] = useState(""),
    [continuationNote, setContinuationNote] = useState(""),
    [continuationStandard, setContinuationStandard] = useState(""),
    [publicationRevision, setPublicationRevision] = useState(0),
    [error, setError] = useState(""),
    [operationBusy, setBusy] = useState(false),
    [standardBusy, setStandardBusy] = useState(false),
    [catalogRevision, setCatalogRevision] = useState(0),
    [materialBusy, setMaterialBusy] = useState(false),
    [showBuilder, setShowBuilder] = useState(false),
    [builderSeed, setBuilderSeed] = useState<Standard | undefined>(undefined);
  const busy = operationBusy || standardBusy;
  async function run(fn: () => Promise<void>) {
    if (busy || materialBusy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  async function load(refreshQueue = true, refreshPublication = true) {
    const s = await api.standards();
    setStandards(s.items);
    const available = s.items.find(
      (row) =>
        row.enabled &&
        row.activation_state !== "scheduled" &&
        row.rules.indicators?.length &&
        row.rules.thresholds?.length,
    );
    if (available) setStandardId((current) => current || String(available.id));
    if (refreshPublication) setPublicationRevision((v) => v + 1);
    if (refreshQueue) await queue.reload();
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    load(false, false).catch((error) => setError(error.message));
  }, [pageRefreshRevision]);
  function showDetail(a: Application) {
    setDetail(a);
    setEditNote(a.evidence_note);
    setValidUntil(a.valid_until ?? "");
    setContinuationNote("");
    setContinuationStandard(String(a.standard_id));
    setReviewScores(
      Object.fromEntries(
        Object.entries(a.current_scores || {}).map(([k, v]) => [
          k,
          String(v ?? ""),
        ]),
      ),
    );
  }
  async function open(id: number) {
    await run(async () => {
      const a = await api.detail(id);
      showDetail(a);
      setReviewScores(
        Object.fromEntries(
          Object.entries(a.current_scores || {}).map(([k, v]) => [
            k,
            String(v ?? ""),
          ]),
        ),
      );
      setComment("");
    });
  }
  async function act(action: string) {
    if (!detail) return;
    await run(async () => {
      await api.action(detail.id, {
        action,
        version: detail.version,
        ...(action === "publish" ? { valid_until: validUntil || null } : {}),
        comment,
        scores:
          action === "review"
            ? Object.fromEntries(
                Object.entries(reviewScores).filter(([, v]) => v !== ""),
              )
            : {},
      });
      await load();
      const a = await api.detail(detail.id);
      showDetail(a);
      setComment("");
    });
  }
  const editable =
    !!detail &&
    canEdit &&
    role === "operator" &&
    ["draft", "rejected"].includes(detail.status);
  const dirty =
    editable &&
    (editNote !== detail!.evidence_note ||
      detail!.rule_snapshot?.indicators?.some(
        (i) =>
          i.source !== "runtime" &&
          (reviewScores[i.code] ?? "") !==
            String(detail!.current_scores?.[i.code] ?? ""),
      ));
  const [view, setView] = useState("applications");
  const workspaceRef = useRecordFocus(detail?.id);
  const reviewDirty =
    !!detail &&
    !editable &&
    Object.entries(reviewScores).some(
      ([code, value]) => value !== String(detail.current_scores?.[code] ?? ""),
    );
  const canLeave = useWorkspaceLeave(
    Boolean(
      dirty ||
      reviewDirty ||
      comment.trim() ||
      continuationNote.trim() ||
      (detail && validUntil !== (detail.valid_until ?? "")) ||
      showBuilder,
    ),
    busy || materialBusy,
    registerLeaveGuard,
  );
  function closeDetail() {
    if (canLeave()) {
      if (view === "create") setView("applications");
      setDetail(null);
      setComment("");
      setContinuationNote("");
    }
  }
  const selected = standards.find((s) => String(s.id) === standardId);
  return (
    <section className="business-stack rating-workspace" ref={workspaceRef}>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="record-pane" hidden={!!detail}>
        <WorkspaceViews
          label="星级评价业务视图"
          value={view}
          onChange={setView}
          disabled={busy || materialBusy}
          items={[
            [
              "applications",
              role === "association" ? "企业申报管理" : "企业申报查看",
            ],
            [
              "standards",
              role === "association" ? "评价标准管理" : "评价标准查看",
            ],
            ["publications", "评价结果与公示"],
            ["process", "评审过程管理"],
            ...(canDataSupport ? [["data", "数据支撑评价"] as const] : []),
            ...(canCreate && role === "operator"
              ? ([["create", "新增星级申报"]] as const)
              : []),
          ]}
        />
        <section
          className="panel"
          hidden={view !== "standards" && view !== "create"}
        >
          <div className="panel-title">
            <div>
              <h2>{view === "create" ? "新增星级申报" : "星级评价标准"}</h2>
              <p>
                示例标准 ·
                申报自评不等于已核实运行数据；人工项须核验证据，平台项使用冻结观测且不允许人工覆盖
              </p>
            </div>
            {canStandards && globalScope && role === "association" && (
              <button
                className="outline"
                disabled={busy || materialBusy}
                onClick={() => {
                  setBuilderSeed(undefined);
                  setShowBuilder(!showBuilder);
                }}
              >
                配置新标准版本
              </button>
            )}
          </div>
          {showBuilder && globalScope && (
            <RatingStandardWorkbench
              key={builderSeed?.id ?? "new"}
              initial={builderSeed}
              csrf={csrf}
              parentBusy={busy || materialBusy}
              onBusy={setStandardBusy}
              catalogRevision={catalogRevision}
              onCompleted={async () => {
                await load();
                setShowBuilder(false);
                onChanged();
              }}
            />
          )}
          <div className="table-wrap" hidden={view === "create"}>
            <table>
              <thead>
                <tr>
                  <th>标准名称</th>
                  <th>代码 / 版本</th>
                  <th>指标及权重</th>
                  <th>类型</th>
                </tr>
              </thead>
              <tbody>
                {standards.map((s) => (
                  <tr key={s.id}>
                    <td>
                      {s.name}
                      <details className="rating-standard-details">
                        <summary>
                          查看指标配置 {s.name} V{s.version}
                        </summary>
                        <p>
                          整版生效：{s.effective_on ?? "历史日期未留存"} ·{" "}
                          {s.activation_state === "scheduled"
                            ? "尚未生效"
                            : s.activation_state === "effective"
                              ? "已生效"
                              : "历史生效状态未知"}
                        </p>
                        {s.rules.indicators?.map((indicator) => (
                          <article key={indicator.code}>
                            <p>
                              {s.rules.indicator_status_version === 1
                                ? indicator.enabled
                                  ? "已启用"
                                  : "已停用，不参与新版本计算"
                                : "历史指标状态未留存，沿用原版算法"}
                            </p>
                            <b>
                              {indicator.name} · 权重{indicator.weight}
                            </b>
                            <p>{indicator.description ?? "历史说明未留存"}</p>
                            <p>
                              字段：
                              {ratingFieldNames[indicator.field_name ?? ""] ??
                                "历史字段未留存"}{" "}
                              · 生效：{indicator.effective_from ?? "未知"}
                            </p>
                            {indicator.bands?.map((band, n) => (
                              <p key={n}>
                                [{band.lower}, {band.upper ?? "+∞"}) ·{" "}
                                {band.label} → {band.score}分
                              </p>
                            )) ?? <p>历史分档未留存</p>}
                          </article>
                        ))}
                        {canStandards &&
                          globalScope &&
                          role === "association" &&
                          s.rules.configuration_version === 2 && (
                            <button
                              className="outline"
                              disabled={busy || materialBusy}
                              onClick={() => {
                                setBuilderSeed(s);
                                setShowBuilder(true);
                              }}
                            >
                              基于此版新建 {s.code} V{s.version + 1}
                            </button>
                          )}
                      </details>
                    </td>
                    <td>
                      {s.code} / V{s.version}
                    </td>
                    <td>
                      {s.rules.indicators
                        ?.map((i) => `${i.name}（${i.weight}）`)
                        .join("、") || "历史指标未留存"}
                    </td>
                    <td>
                      <em className="amber">演示标准</em>
                      <p>{s.enabled ? "已启用" : "已停用"}</p>
                      {canStandards &&
                        globalScope &&
                        role === "association" && (
                          <RatingStandardAvailability
                            standard={s}
                            csrf={csrf}
                            parentBusy={busy || materialBusy}
                            onBusy={setStandardBusy}
                            onChanged={async () => {
                              await load();
                              setCatalogRevision((previous) => previous + 1);
                              onChanged();
                            }}
                          />
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!standards.length && <p className="empty">暂无评价标准</p>}
          </div>
          <div className="record-pane" hidden={view !== "create"}>
            {canCreate && role === "operator" && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    const a = await api.create({
                      station_id: Number(sid),
                      kind: "initial",
                      standard_id: Number(standardId),
                      scores,
                      evidence_note: note,
                    });
                    await load();
                    queue.reset();
                    showDetail(await api.detail(a.id));
                  });
                }}
              >
                <div className="business-form">
                  <StationPicker
                    label="评星场站"
                    value={sid}
                    onChange={setSid}
                    status="access_approved,accepted,operating"
                  />
                  <label>
                    评价标准
                    <select
                      aria-label="评星标准"
                      value={standardId}
                      onChange={(e) => {
                        setStandardId(e.target.value);
                        setScores({});
                      }}
                    >
                      {standards.map((s) => (
                        <option
                          key={s.id}
                          value={s.id}
                          disabled={
                            !s.enabled ||
                            s.activation_state === "scheduled" ||
                            s.activation_state === "scheduled" ||
                            !s.rules.indicators?.length ||
                            !s.rules.thresholds?.length
                          }
                        >
                          {s.name} V{s.version}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selected?.rules.configuration_version === 2 && (
                    <p className="inline-note">
                      填写人工字段原值（0–100），系统按档位换算指标得分；平台字段使用创建时冻结台账观测。
                    </p>
                  )}
                  {selected?.rules.indicators
                    ?.filter((i) => participates(selected.rules, i))
                    .map((i) => (
                      <label key={i.code}>
                        {i.name}（满分{i.max_score}）
                        {i.source === "runtime" ? (
                          <span className="muted">
                            {selected?.rules.configuration_version === 2
                              ? "使用创建时冻结台账观测，不允许自填"
                              : "运行指标待接口，不允许自填"}
                          </span>
                        ) : (
                          <input
                            aria-label={`自评-${i.code}`}
                            required
                            type="number"
                            min={0}
                            max={
                              selected?.rules.configuration_version === 2
                                ? 100
                                : i.max_score
                            }
                            step="0.01"
                            value={scores[i.code] || ""}
                            onChange={(e) =>
                              setScores({ ...scores, [i.code]: e.target.value })
                            }
                          />
                        )}
                      </label>
                    ))}
                  <label>
                    申报依据说明
                    <input
                      aria-label="评星依据说明"
                      required
                      value={note}
                      maxLength={2000}
                      onChange={(e) => setNote(e.target.value)}
                    />
                  </label>
                  <button
                    className="outline"
                    disabled={
                      busy ||
                      !sid ||
                      !standardId ||
                      !selected?.rules.indicators?.length ||
                      !selected?.enabled ||
                      selected.activation_state === "scheduled"
                    }
                  >
                    保存评星草稿
                  </button>
                </div>
              </form>
            )}
          </div>
        </section>
        <div className="record-pane" hidden={view !== "publications"}>
          <RatingPublications
            revision={publicationRevision}
            onOpen={open}
            busy={busy || materialBusy}
          />
        </div>
      </div>
      <div className="record-pane" hidden={!!detail || view !== "data"}>
        {canDataSupport && <RatingDataSupport />}
      </div>
      <section
        className="panel"
        hidden={!detail && !["applications", "process"].includes(view)}
      >
        <div className="record-pane" hidden={!!detail}>
          <div className="panel-title">
            <div>
              <h2>
                {view === "process"
                  ? "评审过程管理"
                  : view === "data"
                    ? "数据支撑评价"
                    : role === "association"
                      ? "星级申报与评审"
                      : "星级申报及结果"}
              </h2>
              <p>
                {view === "process"
                  ? "按申报状态和提交时间定位事项，进入详情核对提交、复核与公示记录。"
                  : view === "data"
                    ? "进入评价依据核对指标原始值、数据来源与缺项；资料不足不推定为零分或有效星级。"
                    : "每次评定保留标准版本、初始分、人工修改和公示历史"}
              </p>
            </div>
            <button
              className="outline"
              disabled={materialBusy}
              onClick={() => run(load)}
            >
              刷新评价
            </button>
          </div>
          <QueueFilters
            queue={queue}
            label="星级"
            statuses={statuses}
            kinds={kinds}
            busy={busy || materialBusy}
            onSearch={() => setDetail(null)}
          />
          <BusinessExports
            module="ratings"
            query={queue.query}
            busy={queue.loading || busy}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "申报编号",
                    "类型",
                    "场站 / 企业",
                    "最近提交（北京时间）",
                    "评审分数",
                    "星级",
                    "状态 / 有效期",
                    "操作",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => (
                  <tr key={a.id}>
                    <td>RATE-{a.id}</td>
                    <td>{kinds[a.kind]}</td>
                    <td>
                      {a.station_name}
                      <br />
                      <small className="muted">
                        {a.operator_name} · 场站 #{a.station_id}
                      </small>
                    </td>
                    <td>{submissionTime(a.last_submitted_at)}</td>
                    <td>{a.calculation.total ?? "数据未齐"}</td>
                    <td>
                      {a.status === "superseded"
                        ? `原评定${a.calculation.stars ?? "待核实"}星 · 已替代`
                        : ["published", "delisted"].includes(a.status)
                          ? a.effective_stars === null
                            ? "有效星级待确认"
                            : a.result_state === "valid"
                              ? `有效${a.effective_stars}星`
                              : `${resultStates[a.result_state]} · 当前${a.effective_stars}星`
                          : a.calculation.stars === null
                            ? "待核实"
                            : `${a.calculation.stars}星（建议）`}
                    </td>
                    <td>
                      <em
                        className={a.status === "published" ? "green" : "amber"}
                      >
                        {statuses[a.status]}
                      </em>
                      <small className="muted">
                        {a.valid_until ? ` 至 ${a.valid_until}` : ""}
                      </small>
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy || materialBusy}
                        onClick={() => open(a.id)}
                      >
                        评星详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!apps.length && !queue.loading && !queue.error && (
              <p className="empty">当前条件下没有星级记录</p>
            )}
          </div>
          <QueuePagination
            queue={queue}
            label="星级"
            busy={busy || materialBusy}
          />
        </div>
        {detail && (
          <div
            className="record-detail business-record-detail"
            aria-label="星级申报详情"
          >
            <div className="panel-title">
              <div>
                <h2>RATE-{detail.id} · 评价依据</h2>
                <p>
                  {statuses[detail.status]} · {detail.calculation.basis}
                </p>
              </div>
              {detail.has_report && (
                <a
                  className="outline report-download"
                  href={`/api/v1/ratings/applications/${detail.id}/report`}
                >
                  下载评价报告
                </a>
              )}
              <button
                className="outline"
                disabled={busy || materialBusy}
                onClick={closeDetail}
              >
                关闭评价详情
              </button>
            </div>
            <div className="rating-score-summary" aria-label="评审结果概况">
              <div>
                <span>评审总分</span>
                <strong>{detail.calculation.total ?? "待核实"}</strong>
              </div>
              <div>
                <span>
                  {["published", "delisted", "superseded"].includes(
                    detail.status,
                  )
                    ? "评定星级"
                    : "建议星级"}
                </span>
                <strong>
                  {detail.calculation.stars == null
                    ? "待定级"
                    : `${detail.calculation.stars} 星`}
                </strong>
              </div>
              <div>
                <span>当前有效星级</span>
                <strong>
                  {detail.effective_stars == null
                    ? "待确认"
                    : `${detail.effective_stars} 星`}
                </strong>
                <small>{resultStates[detail.result_state]}</small>
              </div>
            </div>
            <section
              className="rating-submission-info"
              aria-label="星级申报信息"
              data-testid="rating-submission-info"
            >
              <h3>申报信息</h3>
              <dl>
                <div>
                  <dt>场站（当前名称）</dt>
                  <dd>{detail.station_name}</dd>
                </div>
                <div>
                  <dt>运营商（当前名称）</dt>
                  <dd>{detail.operator_name}</dd>
                </div>
                <div>
                  <dt>审核状态</dt>
                  <dd>{statuses[detail.status] ?? detail.status}</dd>
                </div>
                <div>
                  <dt>最近提交账号（当前账号名）</dt>
                  <dd>
                    {detail.last_submission
                      ? `${detail.last_submission.current_account_username ?? "当前账号名称不可用"} · #${detail.last_submission.actor_id}`
                      : "无提交账号记录"}
                  </dd>
                </div>
                <div>
                  <dt>最近提交时间（北京时间）</dt>
                  <dd>
                    {submissionTime(
                      detail.last_submission?.submitted_at ?? null,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>记录版本</dt>
                  <dd>V{detail.version}</dd>
                </div>
              </dl>
              <p className="muted">
                场站、企业和账号显示当前名称；提交信息取最近一次实际提交记录，实名与历史名称未确认。
              </p>
            </section>
            <div className="detail-grid">
              <div>
                <section className="materials">
                  <h3>{kinds[detail.kind]} · 申报依据</h3>
                  {detail.before_result?.id != null && (
                    <p>
                      关联 RATE-{detail.before_result.id}：原评定
                      {detail.before_result.stars}星 · 原有效期至{" "}
                      {detail.before_result.valid_until ?? "未确认"}
                    </p>
                  )}
                  <p>
                    当前结果：{resultStates[detail.result_state]} · 有效星级{" "}
                    {detail.effective_stars ?? "待确认"} · 有效期至{" "}
                    {detail.valid_until ?? "待确认"}
                  </p>
                  {editable ? (
                    <label>
                      申报依据说明
                      <textarea
                        aria-label="修改评星依据"
                        disabled={busy || materialBusy}
                        value={editNote}
                        maxLength={2000}
                        onChange={(e) => setEditNote(e.target.value)}
                      />
                    </label>
                  ) : (
                    <p>{detail.evidence_note || "未填写"}</p>
                  )}
                  <b>
                    评审总分 {detail.calculation.total ?? "缺失待核实"} ·{" "}
                    {detail.calculation.stars === null
                      ? "未定级"
                      : `评定${detail.calculation.stars}星`}
                  </b>
                </section>
                <RatingMaterials
                  key={detail.id}
                  applicationId={detail.id}
                  version={detail.version}
                  editable={
                    canMaterials &&
                    role === "operator" &&
                    ["draft", "rejected"].includes(detail.status)
                  }
                  csrf={csrf}
                  parentBusy={busy || !!dirty}
                  onBusy={setMaterialBusy}
                />
                {detail.calculation.categories?.map((c) => (
                  <p key={c.code}>
                    {c.name} · 分类权重 {c.weight} · 得分{" "}
                    {c.score ?? "数据未齐"}
                  </p>
                ))}
                <h3>评定过程</h3>
                {detail.history?.map((h, i) => (
                  <article className="timeline" key={i}>
                    <b>{actions[h.action] || h.action}</b>
                    <p>{h.comment || "无补充说明"}</p>
                    <small>{submissionTime(h.created_at)}（北京时间）</small>
                  </article>
                ))}
              </div>
              <div>
                <h3>指标与复核</h3>
                {detail.rule_snapshot.configuration_version === 2 && (
                  <p className="inline-note">
                    下列人工输入为字段原值（0–100），并非映射后的指标得分。平台字段不可人工覆盖；各档位与得分见上方计算依据。
                  </p>
                )}
                {detail.rule_snapshot.evaluation_context && (
                  <p className="inline-note">
                    创建时冻结台账观测：
                    {submissionTime(
                      detail.rule_snapshot.evaluation_context.captured_at,
                    )}
                    （北京时间） ；客户源完整性未验证，不能当作历史运行率。
                  </p>
                )}
                {detail.calculation.details
                  ?.filter((row) => row.field_name)
                  .map((row) =>
                    row.enabled === false ? (
                      <p className="rating-score-evidence" key={row.code}>
                        {row.name} · 已停用 · {row.exclusion_reason} ·
                        不记零分或缺失
                      </p>
                    ) : (
                      <p className="rating-score-evidence" key={row.code}>
                        {row.name} · 字段
                        {ratingFieldNames[row.field_name ?? ""] ??
                          "历史字段未留存"}{" "}
                        · 原值 {row.raw_value ?? "缺失"} →{" "}
                        {row.band_label ?? row.missing_reason ?? "未匹配"} ·
                        得分 {row.score ?? "未知"} · 生效{" "}
                        {row.effective_from ?? "未知"}
                        {row.enabled === true && (
                          <>
                            {" "}
                            · 启用权重之和{row.normalization_weight} ·
                            最终权重约
                            {Number(row.effective_weight_percent).toFixed(4)}%
                          </>
                        )}
                      </p>
                    ),
                  )}

                {!detail.rule_snapshot.indicators && (
                  <p className="inline-note">
                    历史指标快照未留存，不使用当前标准补写评分依据。
                  </p>
                )}
                {(detail.rule_snapshot.categories?.length
                  ? detail.rule_snapshot.categories
                  : [{ code: "legacy", name: "未分组指标", weight: null }]
                ).map((category) => (
                  <fieldset
                    className="rating-indicator-group"
                    key={category.code}
                  >
                    <legend>
                      {category.name}
                      {category.weight !== null &&
                        ` · 分类权重 ${category.weight}`}
                    </legend>
                    {category.code !== "legacy" && (
                      <p className="muted">
                        分类得分：
                        {detail.calculation.categories?.find(
                          (row) => row.code === category.code,
                        )?.score ?? "未提供分类得分"}
                      </p>
                    )}
                    {detail.rule_snapshot.indicators
                      ?.filter(
                        (i) =>
                          participates(detail.rule_snapshot, i) &&
                          (i.category ?? "legacy") === category.code,
                      )
                      .map((i) => (
                        <label className="score-field" key={i.code}>
                          {i.name}（权重{i.weight} / 满分{i.max_score}）
                          {(canReview &&
                            !(
                              detail.rule_snapshot.configuration_version ===
                                2 && i.source === "runtime"
                            ) &&
                            role === "association" &&
                            ["submitted", "reviewed"].includes(
                              detail.status,
                            )) ||
                          (editable && i.source !== "runtime") ? (
                            <input
                              disabled={busy || materialBusy}
                              aria-label={`${editable ? "修改自评" : "复核"}-${i.code}`}
                              type="number"
                              min={0}
                              max={
                                detail.rule_snapshot.configuration_version === 2
                                  ? 100
                                  : i.max_score
                              }
                              step="0.01"
                              value={reviewScores[i.code] ?? ""}
                              onChange={(e) =>
                                setReviewScores({
                                  ...reviewScores,
                                  [i.code]: e.target.value,
                                })
                              }
                            />
                          ) : (
                            <b>
                              {detail.rule_snapshot.configuration_version ===
                                2 && i.source === "runtime"
                                ? (detail.calculation.details?.find(
                                    (row) => row.code === i.code,
                                  )?.raw_value ?? "缺失")
                                : (detail.current_scores?.[i.code] ?? "缺失")}
                            </b>
                          )}
                        </label>
                      ))}
                  </fieldset>
                ))}
                {editable && (
                  <>
                    {dirty && (
                      <p className="inline-note">
                        自评有未保存修改，请先保存再上传或提交。
                      </p>
                    )}
                    <button
                      className="outline"
                      disabled={busy || materialBusy || !dirty}
                      onClick={() =>
                        run(async () => {
                          await api.edit(detail.id, {
                            version: detail.version,
                            evidence_note: editNote,
                            scores: Object.fromEntries(
                              Object.entries(reviewScores).filter(
                                ([, v]) => v !== "",
                              ),
                            ),
                          });
                          showDetail(await api.detail(detail.id));
                          await load();
                        })
                      }
                    >
                      保存申报修改
                    </button>
                  </>
                )}
                {canSubmit &&
                  role === "operator" &&
                  ["draft", "rejected"].includes(detail.status) && (
                    <button
                      className="outline"
                      disabled={busy || materialBusy || !!dirty}
                      onClick={() => act("submit")}
                    >
                      提交星级申报
                    </button>
                  )}
                {canWithdraw &&
                  role === "operator" &&
                  ["draft", "submitted"].includes(detail.status) && (
                    <button
                      className="outline"
                      disabled={busy || materialBusy}
                      onClick={() => act("withdraw")}
                    >
                      撤回评星申请
                    </button>
                  )}
                {canCreate &&
                  role === "operator" &&
                  ["published", "delisted"].includes(detail.status) && (
                    <section className="materials">
                      <h3>复评与异议申诉</h3>
                      <p className="muted">
                        原结果保留至新结果公示。申诉沿用原标准；复评可选择新版本。时限及正式流程待确认。
                      </p>
                      <label>
                        申请理由
                        <textarea
                          aria-label="复评申诉理由"
                          maxLength={2000}
                          value={continuationNote}
                          disabled={busy}
                          onChange={(e) => setContinuationNote(e.target.value)}
                        />
                      </label>
                      <label>
                        复评标准
                        <select
                          aria-label="复评标准"
                          value={continuationStandard}
                          onChange={(e) =>
                            setContinuationStandard(e.target.value)
                          }
                        >
                          {standards.map((s) => (
                            <option
                              key={s.id}
                              value={s.id}
                              disabled={
                                !s.enabled ||
                                s.activation_state === "scheduled" ||
                                !s.rules.indicators?.length ||
                                !s.rules.thresholds?.length
                              }
                            >
                              {s.name} V{s.version}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="action-row">
                        {[
                          "appeal",
                          detail.result_state === "expired"
                            ? "renewal"
                            : "reassessment",
                        ].map((kind) => (
                          <button
                            key={kind}
                            className="outline"
                            disabled={
                              busy || materialBusy || !continuationNote.trim()
                            }
                            onClick={() =>
                              run(async () => {
                                const created = await api.create({
                                  station_id: detail.station_id,
                                  standard_id:
                                    kind === "appeal"
                                      ? detail.standard_id
                                      : Number(continuationStandard),
                                  kind,
                                  parent_id: detail.id,
                                  parent_version: detail.version,
                                  scores: detail.current_scores,
                                  evidence_note: continuationNote,
                                });
                                await load();
                                queue.reset();
                                showDetail(await api.detail(created.id));
                              })
                            }
                          >
                            {kind === "appeal"
                              ? "创建异议申诉草稿"
                              : "创建复评草稿"}
                          </button>
                        ))}
                      </div>
                    </section>
                  )}
                {role === "association" &&
                  ((canReview &&
                    ["submitted", "reviewed"].includes(detail.status)) ||
                    (canPublish && detail.status === "reviewed") ||
                    (canDelist && detail.status === "published")) && (
                    <>
                      <label>
                        评审意见
                        <textarea
                          disabled={busy || materialBusy}
                          aria-label="评审意见"
                          value={comment}
                          maxLength={2000}
                          onChange={(e) => setComment(e.target.value)}
                        />
                      </label>
                      <div className="action-row">
                        {canReview &&
                          ["submitted", "reviewed"].includes(detail.status) && (
                            <button
                              className="outline"
                              disabled={busy || materialBusy || !comment.trim()}
                              onClick={() => act("review")}
                            >
                              保存人工复核
                            </button>
                          )}
                        {canReview && detail.status === "submitted" && (
                          <button
                            className="outline"
                            disabled={busy || materialBusy || !comment.trim()}
                            onClick={() => act("reject")}
                          >
                            驳回星级申报
                          </button>
                        )}
                        {canPublish && detail.status === "reviewed" && (
                          <label>
                            公示有效期至
                            <input
                              aria-label="公示有效期至"
                              type="date"
                              value={validUntil}
                              disabled={busy}
                              onChange={(e) => setValidUntil(e.target.value)}
                            />
                            <small className="muted">
                              需显式填写；期限规则待客户确认
                            </small>
                          </label>
                        )}
                        {canPublish && detail.status === "reviewed" && (
                          <button
                            className="outline"
                            disabled={
                              busy ||
                              materialBusy ||
                              !comment.trim() ||
                              !validUntil
                            }
                            onClick={() => act("publish")}
                          >
                            发布星级结果
                          </button>
                        )}
                        {canDelist && detail.status === "published" && (
                          <button
                            className="outline"
                            disabled={busy || materialBusy || !comment.trim()}
                            onClick={() => act("delist")}
                          >
                            摘牌并留存记录
                          </button>
                        )}
                      </div>
                    </>
                  )}
              </div>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
