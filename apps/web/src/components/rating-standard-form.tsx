"use client";
import type { ApiSchemas } from "../lib/typed-api";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError } from "../lib/typed-api";
type DraftIndicator = Omit<
  ApiSchemas["IndicatorInput"],
  "bands" | "field_name" | "enabled"
> & {
  enabled: boolean | null;
  field_name: ApiSchemas["IndicatorInput"]["field_name"] | "";
  bands: {
    lower: string;
    upper: string | null;
    score: string;
    label: string;
  }[];
};
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const newIndicator = (
  code: string,
  name: string,
  weight: string,
): DraftIndicator => ({
  enabled: true,
  code,
  name,
  weight,
  max_score: "100",
  source: "manual",
  category: "general",
  field_name: "expert_score",
  description: "根据材料与专家原值按档位映射（配置示例）",
  effective_from: today(),
  bands: [
    { lower: "0", upper: "80", score: "40", label: "一般" },
    { lower: "80", upper: null, score: "100", label: "优秀" },
  ],
});
export function RatingStandardForm({
  busy,
  csrf,
  initial,
  draft,
  onDraftSave,
  onDirty,
  onSave,
}: {
  busy: boolean;
  csrf: string;
  initial?: ApiSchemas["RatingStandardResponse"];
  draft?: ApiSchemas["RatingDraftData"];
  onDraftSave?: (data: ApiSchemas["RatingDraftData"]) => Promise<void>;
  onDirty?: () => void;
  onSave: (body: ApiSchemas["StandardInput"]) => Promise<void>;
}) {
  const [code, setCode] = useState(draft?.code ?? initial?.code ?? ""),
    [name, setName] = useState(draft?.name ?? initial?.name ?? ""),
    [version, setVersion] = useState(
      draft ? draft.version : String((initial?.version ?? 0) + 1),
    );
  const [categories, setCategories] = useState(
    draft?.categories?.map((c) => ({
      code: c.code,
      name: c.name,
      weight: c.weight,
    })) ??
      initial?.rules.categories ?? [
        { code: "general", name: "综合评价", weight: "100" },
      ],
  );
  const [indicators, setIndicators] = useState<DraftIndicator[]>([
    ...(draft?.indicators?.map((item): DraftIndicator => ({
      enabled: item.enabled ?? null,
      code: item.code,
      name: item.name,
      weight: item.weight,
      max_score: item.max_score,
      source: item.source ?? "manual",
      category: item.category,
      field_name: item.field_name ?? "",
      description: item.description,
      effective_from: item.effective_from,
      bands:
        item.bands?.map((band) => ({ ...band, upper: band.upper ?? null })) ??
        [],
    })) ??
      initial?.rules.indicators?.map((item): DraftIndicator => ({
        ...item,
        enabled: item.enabled ?? null,
        source: item.source === "runtime" ? "runtime" : "manual",
        category: item.category ?? "general",
        effective_from: item.effective_from ?? "",
        description: item.description ?? "",
        field_name: item.field_name ?? "expert_score",
        bands:
          item.bands?.map((band) => ({ ...band, upper: band.upper ?? null })) ??
          [],
      })) ?? [
        newIndicator("service", "服务材料", "60"),
        newIndicator("safety", "安全材料", "40"),
      ]),
  ]);
  const [fields, setFields] = useState<ApiSchemas["RatingFieldResponse"][]>([]);
  const [fieldError, setFieldError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/ratings/fields", {
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "字段目录读取失败"));
        setFields(data.items);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFieldError(error.message);
      });
    return () => controller.abort();
  }, []);
  function update(index: number, changes: Partial<DraftIndicator>) {
    setIndicators((previous) =>
      previous.map((item, n) => (n === index ? { ...item, ...changes } : item)),
    );
  }
  const [thresholds, setThresholds] = useState<
    { minimum: string; stars: string }[]
  >(
    draft?.thresholds?.map((item) => ({
      minimum: item.minimum,
      stars: item.stars,
    })) ??
      initial?.rules.thresholds?.map((item) => ({
        ...item,
        stars: String(item.stars),
      })) ?? [
        { minimum: "60", stars: "1" },
        { minimum: "70", stars: "2" },
        { minimum: "80", stars: "3" },
        { minimum: "90", stars: "4" },
        { minimum: "95", stars: "5" },
      ],
  );
  const [formError, setFormError] = useState("");
  function data(): ApiSchemas["RatingDraftData"] {
    return {
      code,
      name,
      version,
      categories: categories.map((c) => ({ ...c, weight: String(c.weight) })),
      indicators: indicators.map((i) => ({
        ...i,
        weight: String(i.weight),
        max_score: String(i.max_score),
        category: i.category ?? "",
        bands: i.bands,
      })),
      thresholds: thresholds.map((item) => ({ ...item })),
    };
  }
  const [preview, setPreview] = useState<ApiSchemas["RatingWeightPlan"] | null>(
    null,
  );
  const [previewBusy, setPreviewBusy] = useState(false);
  const previewController = useRef<AbortController | null>(null);
  useEffect(() => () => previewController.current?.abort(), []);
  function clearPreview() {
    previewController.current?.abort();
    setPreviewBusy(false);
    setPreview(null);
  }
  function complete(): ApiSchemas["StandardInput"] {
    const ready = indicators.map((item) => {
      const field_name = item.field_name,
        enabled = item.enabled;
      if (!field_name) throw new Error("请选择各指标字段");
      if (enabled === null)
        throw new Error("请选择各指标的启用状态，旧状态不能推断");
      return { ...item, field_name, enabled };
    });
    return {
      code,
      name,
      version: Number(version),
      categories,
      indicators: ready,
      thresholds: thresholds.map((item) => ({
        minimum: item.minimum,
        stars: Number(item.stars),
      })),
    };
  }
  async function previewWeights() {
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    setPreviewBusy(true);
    setFormError("");
    setPreview(null);
    try {
      const response = await typedApi.POST(
        "/api/v1/ratings/standards/preview",
        {
          signal: controller.signal,
          headers: { "x-csrf-token": csrf },
          body: complete(),
        },
      );
      if (controller.signal.aborted) return;
      if (!response.data)
        throw new Error(
          apiError(
            response.error,
            "预览失败，请检查完整字段、至少一个启用指标及各分类的参与指标",
          ),
        );
      setPreview(response.data);
    } catch (error) {
      if (!controller.signal.aborted)
        setFormError(error instanceof Error ? error.message : "权重预览失败");
    } finally {
      if (
        previewController.current === controller &&
        !controller.signal.aborted
      )
        setPreviewBusy(false);
    }
  }
  return (
    <form
      className="standard-builder"
      onChangeCapture={() => {
        onDirty?.();
        clearPreview();
      }}
      onClickCapture={(event) => {
        if (event.target instanceof Element) {
          const button = event.target.closest("button");
          if (
            button?.type === "button" &&
            button.dataset.readonlyAction !== "true"
          ) {
            onDirty?.();
            clearPreview();
          }
        }
      }}
      onSubmit={(e) => {
        e.preventDefault();
        setFormError("");
        try {
          onSave(complete());
        } catch (error) {
          setFormError(
            error instanceof Error ? error.message : "请检查标准字段",
          );
        }
      }}
    >
      {formError && (
        <p className="error" role="alert">
          {formError}
        </p>
      )}
      <fieldset className="rating-builder-lock" disabled={busy}>
        <div className="business-form">
          <label>
            标准代码
            <input
              aria-label="标准代码"
              required
              maxLength={60}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <label>
            标准名称
            <input
              aria-label="标准名称"
              required
              maxLength={150}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            版本
            <input
              required
              type="number"
              min={1}
              max={2147483647}
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
          </label>
        </div>
        <h3>分类权重</h3>
        <p className="inline-note">
          分类内先按指标权重归一，再按分类权重汇总。下列初始值仅为配置示例。
        </p>
        {categories.map((c, index) => (
          <div className="business-form" key={index}>
            <label>
              分类代码
              <input
                required
                maxLength={50}
                aria-label={`分类${index + 1}代码`}
                value={c.code}
                onChange={(event) => {
                  const value = event.target.value;
                  setCategories(
                    categories.map((item, n) =>
                      n === index ? { ...item, code: value } : item,
                    ),
                  );
                  setIndicators(
                    indicators.map((item) =>
                      item.category === c.code
                        ? { ...item, category: value }
                        : item,
                    ),
                  );
                }}
              />
            </label>
            <label>
              分类名称
              <input
                required
                maxLength={100}
                value={c.name}
                onChange={(e) =>
                  setCategories(
                    categories.map((x, n) =>
                      n === index ? { ...x, name: e.target.value } : x,
                    ),
                  )
                }
              />
            </label>
            <label>
              分类权重
              <input
                required
                type="number"
                min="0.0001"
                step="0.0001"
                value={c.weight}
                onChange={(e) =>
                  setCategories(
                    categories.map((x, n) =>
                      n === index ? { ...x, weight: e.target.value } : x,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              className="outline"
              disabled={
                categories.length === 1 ||
                indicators.some((i) => i.category === c.code)
              }
              onClick={() =>
                setCategories(categories.filter((x) => x.code !== c.code))
              }
            >
              移除空分类
            </button>
          </div>
        ))}
        <button
          type="button"
          className="outline"
          disabled={categories.length >= 20}
          onClick={() =>
            setCategories([
              ...categories,
              { code: "category_" + Date.now(), name: "新分类", weight: "10" },
            ])
          }
        >
          增加分类
        </button>
        <h3>评价指标 · 字段与分档</h3>
        <p className="inline-note">
          每个指标有独立生效日。整版按启用指标的最晚生效日开放新申报；已有申请使用原规则与创建时冻结观测，不随台账变化重算。修改请创建新版本。
        </p>
        {fieldError && (
          <p role="alert" className="error">
            {fieldError}
          </p>
        )}
        {indicators.map((i, index) => (
          <fieldset
            className="rating-indicator-editor"
            key={index}
            disabled={busy}
          >
            <legend>
              指标 {index + 1} · {i.name || "待命名"}
            </legend>
            <div className="business-form">
              <label>
                指标状态
                <select
                  required
                  aria-label={`指标${index + 1}状态`}
                  value={
                    i.enabled === null ? "" : i.enabled ? "enabled" : "disabled"
                  }
                  onChange={(event) =>
                    update(index, {
                      enabled:
                        event.target.value === "enabled"
                          ? true
                          : event.target.value === "disabled"
                            ? false
                            : null,
                    })
                  }
                >
                  <option value="">请选择，历史状态未留存</option>
                  <option value="enabled">启用 · 参与权重归一</option>
                  <option value="disabled">停用 · 不参与评分</option>
                </select>
              </label>
              {(["code", "name", "weight", "max_score"] as const).map((key) => (
                <label key={key}>
                  {
                    {
                      code: "指标代码",
                      name: "指标名称",
                      weight: "占比权重",
                      max_score: "指标满分",
                    }[key]
                  }
                  <input
                    required
                    aria-label={`指标${index + 1}${key}`}
                    value={i[key]}
                    onChange={(event) =>
                      update(index, { [key]: event.target.value })
                    }
                  />
                </label>
              ))}
              <label>
                所属维度
                <select
                  aria-label={`指标${index + 1}分类`}
                  value={i.category}
                  onChange={(event) =>
                    update(index, { category: event.target.value })
                  }
                >
                  <option value="">请选择维度</option>
                  {categories.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                数据来源
                <select
                  aria-label={`指标${index + 1}来源`}
                  value={i.source}
                  onChange={(event) =>
                    update(index, {
                      source:
                        event.target.value === "runtime" ? "runtime" : "manual",
                      field_name:
                        event.target.value === "runtime"
                          ? "equipment_count"
                          : "expert_score",
                    })
                  }
                >
                  <option value="manual">材料/专家原值</option>
                  <option value="runtime">平台当前台账观测</option>
                </select>
              </label>
              <label>
                字段名称
                <select
                  aria-label={`指标${index + 1}字段`}
                  value={i.field_name}
                  disabled={!fields.length}
                  onChange={(event) => {
                    const field = fields.find(
                      (field) => field.code === event.target.value,
                    );
                    if (field)
                      update(index, {
                        field_name: field.code,
                      });
                  }}
                >
                  <option value="">请选择字段</option>
                  {fields
                    .filter((field) => field.source === i.source)
                    .map((field) => (
                      <option key={field.code} value={field.code}>
                        {field.name}（{field.unit}）
                      </option>
                    ))}
                </select>
              </label>
              <label>
                生效日期
                <input
                  required
                  aria-label={`指标${index + 1}生效日期`}
                  type="date"
                  value={i.effective_from}
                  onChange={(event) =>
                    update(index, { effective_from: event.target.value })
                  }
                />
              </label>
            </div>
            <p className="inline-note">
              {fields.find((field) => field.code === i.field_name)
                ?.description || "正在读取字段说明"}
            </p>
            <label className="rating-indicator-description">
              指标说明
              <textarea
                required
                maxLength={2000}
                aria-label={`指标${index + 1}说明`}
                value={i.description}
                onChange={(event) =>
                  update(index, { description: event.target.value })
                }
              />
            </label>
            <p className="inline-note">
              阈值采用左闭右开区间 [下界,
              上界)，从0连续覆盖；最后上界留空表示无上限。人工原值限0–100，档位得分不能超过指标满分。
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "阈值下界（含）",
                      "阈值上界（不含）",
                      "档位说明",
                      "得分",
                      "操作",
                    ].map((h) => (
                      <th key={h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {i.bands.map((band, n) => (
                    <tr key={n}>
                      {(["lower", "upper", "label", "score"] as const).map(
                        (key) => (
                          <td key={key}>
                            <input
                              aria-label={`指标${index + 1}档位${n + 1}${key}`}
                              required={key !== "upper"}
                              value={band[key] ?? ""}
                              placeholder={
                                key === "upper" ? "无上限" : undefined
                              }
                              onChange={(event) =>
                                update(index, {
                                  bands: i.bands.map((old, k) =>
                                    k === n
                                      ? {
                                          ...old,
                                          [key]:
                                            key === "upper" &&
                                            !event.target.value
                                              ? null
                                              : event.target.value,
                                        }
                                      : old,
                                  ),
                                })
                              }
                            />
                          </td>
                        ),
                      )}
                      <td>
                        <button
                          className="outline"
                          type="button"
                          disabled={i.bands.length === 1}
                          onClick={() =>
                            update(index, {
                              bands: i.bands.filter((_, k) => k !== n),
                            })
                          }
                        >
                          移除档位
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="business-form">
              <button
                className="outline"
                type="button"
                disabled={i.bands.length >= 20}
                onClick={() =>
                  update(index, {
                    bands: [
                      ...i.bands,
                      {
                        lower: i.bands.at(-1)?.upper ?? "",
                        upper: null,
                        score: "",
                        label: "",
                      },
                    ],
                  })
                }
              >
                增加档位
              </button>
              <button
                className="outline"
                type="button"
                disabled={indicators.length === 1}
                onClick={() =>
                  setIndicators(indicators.filter((_, n) => n !== index))
                }
              >
                移除此指标
              </button>
            </div>
          </fieldset>
        ))}
        <button
          type="button"
          className="outline"
          disabled={busy || indicators.length >= 50}
          onClick={() =>
            setIndicators([
              ...indicators,
              {
                ...newIndicator("", "", "10"),
                category: categories[0]?.code ?? "",
              },
            ])
          }
        >
          增加指标
        </button>
        <h3>星级门槛</h3>
        <div className="business-form">
          {thresholds.map((t, index) => (
            <div className="rating-threshold-row" key={index}>
              <label>
                星级
                <select
                  required
                  aria-label={`门槛${index + 1}星级`}
                  value={t.stars}
                  onChange={(event) =>
                    setThresholds(
                      thresholds.map((item, n) =>
                        n === index
                          ? { ...item, stars: event.target.value }
                          : item,
                      ),
                    )
                  }
                >
                  <option value="">请选择</option>
                  {[1, 2, 3, 4, 5].map((star) => (
                    <option key={star} value={star}>
                      {star}星
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t.stars || "未选星级"}星最低总分
                <input
                  required
                  type="number"
                  min={0}
                  max={100}
                  step="0.01"
                  value={t.minimum}
                  onChange={(event) =>
                    setThresholds(
                      thresholds.map((item, n) =>
                        n === index
                          ? { ...item, minimum: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
              </label>
              <button
                className="outline"
                type="button"
                onClick={() =>
                  setThresholds(thresholds.filter((_, n) => n !== index))
                }
              >
                移除此星级门槛
              </button>
            </div>
          ))}
          <button
            className="outline"
            type="button"
            disabled={thresholds.length >= 5}
            onClick={() => {
              const star = [1, 2, 3, 4, 5].find(
                (value) =>
                  !thresholds.some((item) => item.stars === String(value)),
              );
              setThresholds([
                ...thresholds,
                { minimum: "", stars: star ? String(star) : "" },
              ]);
            }}
          >
            增加星级门槛
          </button>
        </div>
        <p className="inline-note">
          分类和指标分别按各自权重总和归一，原始规则和版本保存为快照；这不是已确认的福州正式评分办法。
        </p>
        <p className="inline-note">
          停用指标不计缺失或零分，不进入分类内权重分母；每类必须保留至少一个启用指标。分类权重保持，旧申请与报告不重算。
        </p>
        <button
          type="button"
          className="outline"
          data-readonly-action="true"
          disabled={busy || previewBusy}
          onClick={previewWeights}
        >
          {previewBusy ? "正在预览权重…" : "预览参与指标与权重"}
        </button>
        {preview && (
          <section
            className="indicator-weight-preview"
            aria-label="指标参与权重预览"
          >
            <h3>参与范围与权重预览</h3>
            <p>
              {preview.basis}
              。百分比约显示四位小数，计算使用服务端未舍入比例；此处不产生评分或业务记录。
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "指标",
                      "状态",
                      "配置权重",
                      "分类内启用权重之和",
                      "最终权重占比",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.items.map((row) => (
                    <tr key={row.code}>
                      <td>{row.name}</td>
                      <td>{row.enabled ? "参与" : "停用，不参与"}</td>
                      <td>{row.configured_weight}</td>
                      <td>{row.normalization_weight}</td>
                      <td>
                        约 {Number(row.effective_weight_percent).toFixed(4)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {onDraftSave && (
          <button
            type="button"
            className="outline"
            disabled={busy}
            onClick={() => onDraftSave(data())}
          >
            暂存标准草稿
          </button>
        )}
        <button className="outline" disabled={busy}>
          保存标准版本
        </button>
      </fieldset>
    </form>
  );
}
