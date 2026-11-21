"use client";
export const ratingMaterialCategories = {
  inspection_report: "第三方检测报告",
  site_plan: "站点平面图",
  service_record: "服务记录",
  safety_record: "安全管理记录",
  field_record: "现场记录",
};
export function RatingIntakeMaterials({
  files,
  onChange,
  disabled,
  onError,
}: {
  files: Record<string, File>;
  onChange: (files: Record<string, File>) => void;
  disabled: boolean;
  onError: (message: string) => void;
}) {
  return (
    <section className="rating-intake-materials" aria-label="申报材料填报清单">
      <h3>申报材料</h3>
      <p>
        先选择场站并整理材料，保存后进入同一份申报继续核对、补充及提交。五类材料的正式必填要求待确认。
      </p>
      <div className="rating-material-cards">
        {Object.entries(ratingMaterialCategories).map(([category, label]) => (
          <div className="rating-material-card" key={category}>
            <div>
              <h4>{label}</h4>
              <span className={files[category] ? "green" : "muted"}>
                {files[category] ? "已选择，待保存" : "未选择"}
              </span>
            </div>
            <label>
              选择{label}
              <input
                aria-label={`填报-${category}`}
                disabled={disabled}
                type="file"
                accept=".zip,.rar,.jpg,.jpeg,.png,.pdf"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  const next = { ...files, [category]: file };
                  if (
                    !/\.(zip|rar|jpg|jpeg|png|pdf)$/i.test(file.name) ||
                    file.size === 0 ||
                    Object.values(next).reduce((n, f) => n + f.size, 0) >
                      20 * 1024 * 1024
                  ) {
                    onError(
                      "请选择非空ZIP、RAR、JPG、PNG或PDF，全部材料总量不得超过20MiB。",
                    );
                    e.target.value = "";
                    return;
                  }
                  onError("");
                  onChange(next);
                }}
              />
            </label>
            {files[category] && (
              <p>
                {files[category].name} ·{" "}
                {Math.ceil(files[category].size / 1024)}KB{" "}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    const next = { ...files };
                    delete next[category];
                    onChange(next);
                  }}
                >
                  移除选择
                </button>
              </p>
            )}
          </div>
        ))}
      </div>
      <small>
        单份及全部材料上限20MiB；服务端另行校验文件类型和额度。提交后保留冻结版本。
      </small>
    </section>
  );
}
