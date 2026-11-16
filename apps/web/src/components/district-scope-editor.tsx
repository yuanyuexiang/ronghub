"use client";
import { useState } from "react";
import { useDistrictScope } from "./permissions";
export function DistrictScopeEditor({
  label,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string[] | null;
  onChange: (value: string[] | null) => void;
  disabled?: boolean;
}) {
  const own = useDistrictScope(),
    [text, setText] = useState(value?.join("，") || "");
  const parse = (input: string) => [
    ...new Set(
      input
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
  return (
    <fieldset className="district-editor" disabled={disabled}>
      <legend>{label}数据区域</legend>
      {own === null && (
        <label>
          <input
            aria-label={`${label}全部区域`}
            type="checkbox"
            checked={value === null}
            onChange={(e) => onChange(e.target.checked ? null : parse(text))}
          />
          全部区域
        </label>
      )}
      <label>
        区域名称清单
        <input
          aria-label={`${label}区域清单`}
          value={text}
          disabled={value === null}
          onChange={(e) => {
            setText(e.target.value);
            onChange(parse(e.target.value));
          }}
          placeholder={own?.join("，") || "例如：鼓楼区，仓山区"}
          maxLength={2500}
        />
      </label>
      <p className="muted">
        按场站区域名称精确匹配，多个区域用逗号分隔。清单留空表示不允许查看任何场站；企业归属限制仍生效。
        {own !== null && `可下发范围：${own.join("、") || "无场站区域"}`}
      </p>
    </fieldset>
  );
}
