"use client";
import { SubsidyRuleOverview } from "./prototype-catalogues";
import { DailyEnergyWorkspace } from "./prototype-read-views";
import { SubsidyLedger } from "./subsidy-ledger";
import { useBusinessView } from "./business-view-context";
import {
  WorkspaceViews,
  useWorkspaceLeave,
  type LeaveGuardRegistrar,
} from "./business-workspace";
import { useRecordFocus } from "./use-record-focus";
import { usePageRefresh } from "./page-refresh";
import { SubsidyDeviceCalculation } from "./subsidy-device-calculation";
import { SubsidyStatistics } from "./subsidy-statistics";
import { SubsidyDeviceDays } from "./subsidy-device-days";
import { SubsidyPolicyDetail } from "./subsidy-policy-detail";
import { SubsidyPolicyForm } from "./subsidy-policy-form";
import { SubsidyAnnual } from "./subsidy-annual";
import { SubsidyEvidence } from "./subsidy-evidence";
import { BusinessExports } from "./business-exports";
import {
  SubsidyBatchReview,
  type SelectedSubsidy,
} from "./subsidy-batch-review";
import {
  useBusinessQueue,
  loadSubsidyQueue,
  QueueFilters,
  submissionTime,
  QueuePagination,
} from "./business-queue";
import { subsidyApi } from "../lib/business-api";
import type { ApiSchemas } from "../lib/typed-api";
import { useCapability, useGlobalScope } from "./permissions";
import { StationPicker } from "./station-picker";
import { useEffect, useState } from "react";
type Policy = ApiSchemas["SubsidyPolicyResponse"];
type Claim = ApiSchemas["SubsidyClaimDetail"];
const statuses: Record<string, string> = {
  draft: "草稿",
  submitted: "待审核",
  approved: "已审定",
  rejected: "已驳回",
  withdrawn: "已撤回",
  disbursed: "已登记发放",
};
const actions: Record<string, string> = {
  create: "核算草稿",
  submit: "提交审核",
  edit: "修改申请说明",
  material_upload: "上传ZIP材料",
  material_delete: "删除未提交材料",
  adjust: "金额调整",
  approve: "审核通过",
  reject: "审核驳回",
  withdraw: "撤回",
  record_disbursement: "登记发放",
};
export function SubsidyPanel({
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
  const [routeView, setView] = useBusinessView("claims");
  const recordsOnly = role === "operator" && routeView === "records";
  const api = subsidyApi(csrf, onChanged);
  const queue = useBusinessQueue(loadSubsidyQueue);
  const claims = queue.rows;
  const globalScope = useGlobalScope();
  const canCreate = useCapability("subsidy.create"),
    canEdit = useCapability("subsidy.edit") && !recordsOnly,
    canMaterials = useCapability("subsidy.materials") && !recordsOnly,
    canSubmit = useCapability("subsidy.submit") && !recordsOnly,
    canWithdraw = useCapability("subsidy.withdraw") && !recordsOnly,
    canAdjust = useCapability("subsidy.adjust"),
    canReview = useCapability("subsidy.review"),
    canDisburse = useCapability("subsidy.disburse"),
    canPolicies = useCapability("subsidy.policies");
  const [policyDetail, setPolicyDetail] = useState<Policy | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]),
    [sid, setSid] = useState(""),
    [pid, setPid] = useState(""),
    [start, setStart] = useState("2026-11-01"),
    [end, setEnd] = useState("2026-11-30"),
    [detail, setDetail] = useState<Claim | null>(null),
    [error, setError] = useState(""),
    [operationBusy, setBusy] = useState(false),
    [batchOpen, setBatchOpen] = useState(false),
    [batchBusy, setBatchBusy] = useState(false),
    [annualBusy, setAnnualBusy] = useState(false),
    [evidenceBusy, setEvidenceBusy] = useState(false),
    [evidenceReady, setEvidenceReady] = useState(false),
    [applicationNote, setApplicationNote] = useState(""),
    [selection, setSelection] = useState<Record<number, SelectedSubsidy>>({}),
    [comment, setComment] = useState(""),
    [delta, setDelta] = useState("0.00"),
    [showPolicy, setShowPolicy] = useState(false),
    [energy, setEnergy] = useState<ApiSchemas["SubsidyEnergyItem"][]>([]);
  const busy =
    operationBusy || batchOpen || batchBusy || evidenceBusy || annualBusy;
  const view = routeView === "records" ? "claims" : routeView.split(".")[0];
  const [evidenceDirty, setEvidenceDirty] = useState(false);
  const workspaceRef = useRecordFocus(detail?.id);
  const canLeave = useWorkspaceLeave(
    evidenceDirty || Boolean(comment.trim()) || delta !== "0.00" || showPolicy,
    busy,
    registerLeaveGuard,
  );
  function closeDetail() {
    if (!canLeave()) return;
    if (["policies", "annual"].includes(view)) setView("claims");
    setDetail(null);
    setComment("");
    setDelta("0.00");
    setEvidenceDirty(false);
  }
  const canBatch = role === "regulator" && canReview;
  function toggleSelection(row: ApiSchemas["SubsidyQueueItem"]) {
    if (busy || queue.loading) return;
    setSelection((current) => {
      const next = { ...current };
      if (next[row.id]) delete next[row.id];
      else if (row.status === "submitted" && Object.keys(next).length < 100)
        next[row.id] = row;
      return next;
    });
  }
  function selectPage() {
    if (busy || queue.loading) return;
    const next = { ...selection };
    for (const row of claims)
      if (row.status === "submitted" && !next[row.id]) next[row.id] = row;
    if (Object.keys(next).length > 100) {
      setError("本页选择会超过100条，请先取消部分选择");
      return;
    }
    setSelection(next);
  }
  async function load(refreshQueue = true) {
    const p = await api.policies();
    setPolicies(p.items);
    const available = p.items.filter(
      (item) => item.claim_supported && item.enabled,
    );
    setPid((current) =>
      available.some((item) => String(item.id) === current)
        ? current
        : available[0]
          ? String(available[0].id)
          : "",
    );
    if (refreshQueue) await queue.reload();
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    load(false).catch((error) => setError(error.message));
  }, [pageRefreshRevision]);
  useEffect(() => {
    const controller = new AbortController();
    setEnergy([]);
    if (sid)
      api
        .energy(Number(sid), controller.signal)
        .then((d) => {
          if (!controller.signal.aborted) setEnergy(d.items);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        });
    return () => controller.abort();
  }, [sid, pageRefreshRevision]);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
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
  async function open(id: number) {
    await run(async () => {
      setEvidenceReady(false);
      setDetail(await api.detail(id));
      setComment("");
      setDelta("0.00");
    });
  }
  async function create() {
    await run(async () => {
      const c = await api.create({
        station_id: Number(sid),
        policy_id: Number(pid),
        start_date: start,
        end_date: end,
        application_note: applicationNote,
      });
      await load();
      queue.reset();
      setEvidenceReady(false);
      setDetail(await api.detail(c.id));
    });
  }
  async function act(action: string) {
    if (!detail) return;
    await run(async () => {
      await api.action(detail.id, {
        action,
        version: detail.version,
        comment,
        delta,
      });
      await load();
      setDetail(await api.detail(detail.id));
      setComment("");
      setDelta("0.00");
    });
  }
  return (
    <section className="business-stack subsidy-workspace" ref={workspaceRef}>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="record-pane" hidden={!!detail}>
        <WorkspaceViews
          secondaryLabel={
            role === "operator" ? "补贴政策与电量数据" : undefined
          }
          label="补贴业务视图"
          value={view}
          onChange={setView}
          disabled={busy}
          items={[
            ["claims", "补贴申请记录"],
            [
              "policies",
              role === "operator" ? "政策与单站申报" : "补贴规则配置",
            ],
            ...(canCreate && role === "operator"
              ? ([["annual", "年度多站申报"]] as const)
              : []),
            ["statistics", "补贴统计"],
            ["energy", "设备电量数据"],
          ]}
        />
        <div className="record-pane" hidden={view !== "annual"}>
          {canCreate && role === "operator" && (
            <SubsidyAnnual
              csrf={csrf}
              policies={policies}
              canSubmit={canSubmit}
              canMaterials={canMaterials}
              parentBusy={busy}
              onBusy={setAnnualBusy}
              onCreated={() => {
                queue.reset();
                queue.reload();
                onChanged();
              }}
            />
          )}
        </div>
        <section className="panel" hidden={view !== "policies"}>
          <div className="panel-title">
            <div>
              <h2>补贴政策与申报</h2>
              <p>
                政策版本与电量依据 ·
                金额以服务端快照为准；发放仅登记状态，不执行资金支付
              </p>
            </div>
            {canPolicies && globalScope && role === "regulator" && (
              <button
                className="outline"
                disabled={busy}
                onClick={() => setShowPolicy(!showPolicy)}
              >
                新增政策版本
              </button>
            )}
          </div>
          <SubsidyRuleOverview policies={policies} />
          {showPolicy && globalScope && (
            <SubsidyPolicyForm
              busy={busy}
              onSave={async (body) => {
                await run(async () => {
                  await api.policy(body);
                  await load();
                  setShowPolicy(false);
                });
              }}
            />
          )}
          <div className="table-wrap">
            <table className="subsidy-policy-table">
              <thead>
                <tr>
                  {["政策名称", "版本", "单位标准", "有效期", "类型"].map(
                    (h) => (
                      <th key={h}>{h}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>
                      {p.code} / V{p.version}
                    </td>
                    <td>¥ {p.rate} / kWh</td>
                    <td>
                      {p.effective_from} 至 {p.effective_to}
                    </td>
                    <td>
                      <em className="amber">
                        {p.calculation_basis === "demo_device_day_v2"
                          ? "设备日演示核算"
                          : p.claim_supported
                            ? "旧站级演示"
                            : "设备规则 · 暂不可申报"}
                      </em>
                      <button
                        className="outline"
                        aria-label={`查看设备规则 ${p.code} V${p.version}`}
                        onClick={() => setPolicyDetail(p)}
                      >
                        查看规则
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!policies.length && <p className="empty">暂无政策版本</p>}
          </div>
          {policyDetail && (
            <SubsidyPolicyDetail
              policy={policyDetail}
              onClose={() => setPolicyDetail(null)}
            />
          )}
          {canCreate && role === "operator" && (
            <>
              <form
                className="business-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  create();
                }}
              >
                <StationPicker
                  label="补贴场站"
                  value={sid}
                  onChange={setSid}
                  status="operating"
                />
                <label>
                  政策版本
                  <select
                    aria-label="补贴政策"
                    value={pid}
                    onChange={(e) => setPid(e.target.value)}
                  >
                    {policies
                      .filter((p) => p.claim_supported && p.enabled)
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} V{p.version}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  开始日期
                  <input
                    type="date"
                    aria-label="补贴开始日期"
                    required
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  结束日期
                  <input
                    type="date"
                    aria-label="补贴结束日期"
                    required
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
                <label>
                  申请说明（草稿可暂缺）
                  <textarea
                    aria-label="补贴申请说明"
                    value={applicationNote}
                    maxLength={2000}
                    disabled={busy}
                    onChange={(event) => setApplicationNote(event.target.value)}
                  />
                </label>
                <button disabled={busy || !sid || !pid} className="outline">
                  核算并创建草稿
                </button>
              </form>
              <p className="inline-note">
                选中场站可用电量记录：
                {energy.length
                  ? energy.map((e) => `${e.day}：${e.kwh} kWh`).join("；")
                  : "暂无已获取电量，不能核算补贴"}
              </p>
            </>
          )}
        </section>

        <div className="record-pane" hidden={view !== "statistics"}>
          <SubsidyStatistics />
        </div>
        <div className="record-pane" hidden={view !== "energy"}>
          <DailyEnergyWorkspace />
        </div>
      </div>
      {recordsOnly && (
        <div hidden={!!detail}>
          <SubsidyLedger onOpen={open} busy={busy} />
        </div>
      )}
      <section
        className="panel"
        hidden={!detail && (view !== "claims" || recordsOnly)}
      >
        <div className="record-pane" hidden={!!detail}>
          <div className="panel-title">
            <div>
              <h2>
                {routeView === "records"
                  ? "补贴记录查询与导出"
                  : "补贴申请记录"}
              </h2>
              <p>原始核算、人工调整与发放记录分别留存</p>
            </div>
            <div className="action-row">
              <button
                className="outline"
                disabled={busy}
                onClick={() => run(load)}
              >
                刷新记录
              </button>
              {role === "operator" && canCreate && (
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => setView("policies")}
                >
                  新建补贴申请
                </button>
              )}
            </div>
          </div>
          <QueueFilters
            queue={queue}
            label="补贴"
            period
            statuses={statuses}
            busy={busy}
            onSearch={() => {
              setDetail(null);
              setSelection({});
            }}
          />
          <BusinessExports
            module="subsidy"
            query={queue.query}
            busy={queue.loading || busy}
          />
          {canBatch && (
            <>
              <SubsidyBatchReview
                selection={Object.values(selection)}
                disabled={operationBusy}
                csrf={csrf}
                onChanged={onChanged}
                onOpenChange={setBatchOpen}
                onBusyChange={setBatchBusy}
                onClear={() => setSelection({})}
                onCompleted={async (items) => {
                  setSelection({});
                  if (detail && items.some((item) => item.id === detail.id)) {
                    setDetail(null);
                    setComment("");
                    setDelta("0.00");
                  }
                  await queue.reload();
                }}
              />
              <div className="subsidy-page-selection">
                <button
                  type="button"
                  className="outline"
                  disabled={
                    busy ||
                    queue.loading ||
                    !claims.some((row) => row.status === "submitted")
                  }
                  onClick={selectPage}
                >
                  选择本页待审补贴
                </button>
              </div>
            </>
          )}
          <div className="table-wrap">
            <table className="aligned-records subsidy-claims-table">
              <colgroup>
                {canBatch && <col style={{ width: "5%" }} />}
                <col style={{ width: "9%" }} />
                <col style={{ width: canBatch ? "17%" : "22%" }} />
                <col style={{ width: "11%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "8%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "9%" }} />
              </colgroup>
              <thead>
                <tr>
                  {canBatch && <th>选择</th>}
                  {[
                    "编号",
                    "场站 / 企业",
                    "周期",
                    "最近提交（北京时间）",
                    "有效电量",
                    "核算金额",
                    "调整后金额",
                    "状态",
                    "操作",
                  ].map((h) => (
                    <th
                      key={h}
                      className={
                        h === "有效电量" ||
                        h === "核算金额" ||
                        h === "调整后金额"
                          ? "numeric-cell"
                          : undefined
                      }
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {claims.map((c) => (
                  <tr key={c.id}>
                    {canBatch && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`选择补贴 SUB-${c.id}`}
                          checked={!!selection[c.id]}
                          disabled={
                            busy ||
                            queue.loading ||
                            (!selection[c.id] &&
                              (c.status !== "submitted" ||
                                Object.keys(selection).length >= 100))
                          }
                          onChange={() => toggleSelection(c)}
                        />
                      </td>
                    )}
                    <td>SUB-{c.id}</td>
                    <td>
                      {c.station_name}
                      <br />
                      <small className="muted">
                        {c.operator_name} · 场站 #{c.station_id}
                      </small>
                    </td>
                    <td>
                      <span className="subsidy-period-date">
                        {c.start_date}
                      </span>
                      <br />
                      <span className="subsidy-period-date">
                        至 {c.end_date}
                      </span>
                    </td>
                    <td>{submissionTime(c.last_submitted_at)}</td>
                    <td className="numeric-cell">{c.total_kwh}</td>
                    <td className="numeric-cell">¥ {c.base_amount}</td>
                    <td className="numeric-cell">¥ {c.adjusted_amount}</td>
                    <td>
                      <em
                        className={
                          ["approved", "disbursed"].includes(c.status)
                            ? "green"
                            : "amber"
                        }
                      >
                        {statuses[c.status]}
                      </em>
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={() => open(c.id)}
                      >
                        补贴详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!claims.length && !queue.loading && !queue.error && (
              <p className="empty">当前条件下没有补贴记录</p>
            )}
          </div>
          <QueuePagination queue={queue} label="补贴" busy={busy} />
        </div>
        {detail && (
          <div
            className="record-detail business-record-detail"
            aria-label="补贴申请详情"
          >
            <div className="panel-title">
              <div>
                <h2>SUB-{detail.id} · 核算依据</h2>
                {recordsOnly && (
                  <button className="outline" onClick={() => setView("claims")}>
                    前往补贴申请办理
                  </button>
                )}
                <p>
                  {statuses[detail.status]} · 版本 {detail.version}
                </p>
              </div>
              <button className="outline" disabled={busy} onClick={closeDetail}>
                关闭补贴详情
              </button>
            </div>
            <dl className="station-facts claim-summary">
              <div>
                <dt>申请场站</dt>
                <dd>
                  {claims.find((claim) => claim.id === detail.id)
                    ?.station_name ?? `#${detail.station_id}`}
                </dd>
              </div>
              <div>
                <dt>运营商</dt>
                <dd>
                  {claims.find((claim) => claim.id === detail.id)
                    ?.operator_name ?? `#${detail.operator_id}`}
                </dd>
              </div>
              <div>
                <dt>核算周期</dt>
                <dd>
                  {detail.start_date} 至 {detail.end_date}
                </dd>
              </div>
              <div>
                <dt>
                  {["approved", "disbursed"].includes(detail.status)
                    ? "核定金额"
                    : "当前申请金额"}
                </dt>
                <dd>¥ {detail.adjusted_amount}</dd>
              </div>
            </dl>
            <SubsidyEvidence
              key={detail.id}
              claim={detail}
              csrf={csrf}
              canEdit={
                canEdit && role === "operator" && detail.status === "draft"
              }
              canMaterials={
                canMaterials && role === "operator" && detail.status === "draft"
              }
              parentBusy={operationBusy || batchOpen || batchBusy}
              onDirty={setEvidenceDirty}
              onBusy={setEvidenceBusy}
              onReady={setEvidenceReady}
              onUpdated={async () => {
                const current = await api.detail(detail.id);
                setDetail(current);
                await queue.reload();
                onChanged();
              }}
            />
            {detail.snapshot?.device_calculation && (
              <SubsidyDeviceCalculation
                claimId={detail.id}
                calculation={detail.snapshot.device_calculation}
              />
            )}
            {!detail.snapshot?.details &&
              !detail.snapshot?.device_calculation && (
                <p className="inline-note">
                  历史核算明细未留存，未使用当前政策或零金额补写。
                </p>
              )}
            {!detail.snapshot?.device_calculation && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>电量记录</th>
                      <th>日期</th>
                      <th>kWh</th>
                      <th>单价</th>
                      <th>金额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.snapshot?.details?.map((d) => (
                      <tr key={d.energy_id}>
                        <td>{d.energy_id}</td>
                        <td>{d.day}</td>
                        <td>{d.kwh}</td>
                        <td>{d.rate}</td>
                        <td>{d.amount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="detail-grid">
              <div>
                <h3>审核与调整记录</h3>
                {detail.history?.map((a, i) => (
                  <article className="timeline" key={i}>
                    <b>{actions[a.action] || a.action}</b>
                    <p>{a.comment || "无补充意见"}</p>
                    <small>
                      {submissionTime(a.created_at)}（北京时间） · 调整差额 ¥{" "}
                      {a.delta}
                    </small>
                  </article>
                ))}
              </div>
              <div>
                <h3>业务处理</h3>
                {canSubmit &&
                  role === "operator" &&
                  detail.status === "draft" && (
                    <button
                      className="outline"
                      disabled={busy || !evidenceReady}
                      onClick={() => act("submit")}
                    >
                      提交补贴审核
                    </button>
                  )}
                {canWithdraw &&
                  role === "operator" &&
                  ["draft", "submitted"].includes(detail.status) && (
                    <button
                      className="outline"
                      disabled={busy}
                      onClick={() => act("withdraw")}
                    >
                      撤回补贴申请
                    </button>
                  )}
                {((detail.status === "submitted" && (canAdjust || canReview)) ||
                  (detail.status === "approved" && canDisburse)) &&
                  role === "regulator" &&
                  ["submitted", "approved"].includes(detail.status) && (
                    <>
                      <label>
                        审核意见或凭证说明
                        <textarea
                          disabled={busy}
                          aria-label="补贴处理意见"
                          value={comment}
                          onChange={(e) => setComment(e.target.value)}
                          maxLength={2000}
                        />
                      </label>
                      {detail.status === "submitted" ? (
                        <>
                          {canAdjust && (
                            <label>
                              调整差额（可负，精确到分）
                              <input
                                aria-label="补贴调整差额"
                                value={delta}
                                onChange={(e) => setDelta(e.target.value)}
                              />
                            </label>
                          )}
                          <div className="action-row">
                            {canAdjust && (
                              <button
                                className="outline"
                                disabled={busy || !comment.trim()}
                                onClick={() => act("adjust")}
                              >
                                登记金额调整
                              </button>
                            )}
                            {canReview && (
                              <button
                                className="outline"
                                disabled={busy || !comment.trim()}
                                onClick={() => act("approve")}
                              >
                                通过补贴审核
                              </button>
                            )}
                            {canReview && (
                              <button
                                className="outline"
                                disabled={busy || !comment.trim()}
                                onClick={() => act("reject")}
                              >
                                驳回补贴申请
                              </button>
                            )}
                          </div>
                        </>
                      ) : (
                        canDisburse && (
                          <button
                            className="outline"
                            disabled={busy || !comment.trim()}
                            onClick={() => act("record_disbursement")}
                          >
                            登记发放状态（不付款）
                          </button>
                        )
                      )}
                    </>
                  )}
                {["disbursed", "withdrawn", "rejected"].includes(
                  detail.status,
                ) && <p className="muted">记录已归档，历史依据保留。</p>}
              </div>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
