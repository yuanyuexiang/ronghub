"use client";

import { Upload } from "lucide-react";
import {
  forwardRef,
  useLayoutEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
} from "react";

type FilePickerProps = InputHTMLAttributes<HTMLInputElement> & {
  /** Selected files held by a draft; clearing them also resets the native input. */
  selection?: readonly File[];
};

/** Keep the actual file input, its labels, refs and existing upload handlers intact. */
export const FilePicker = forwardRef<HTMLInputElement, FilePickerProps>(
  function FilePicker(
    { selection, onChange, className, ...props },
    forwardedRef,
  ) {
    const acceptNames: Record<string, string> = {
      "application/pdf": "PDF",
      "image/png": "PNG",
      "image/jpeg": "JPG",
      "application/zip": "ZIP",
    };
    const formats = [
      ...new Set(
        (props.accept ?? "")
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean)
          .map(
            (value) =>
              acceptNames[value] ?? value.replace(/^\./, "").toUpperCase(),
          ),
      ),
    ].join(" / ");
    const input = useRef<HTMLInputElement | null>(null);
    const [names, setNames] = useState("");
    useLayoutEffect(() => {
      if (!input.current) return;
      if (selection && !selection.length) input.current.value = "";
      const next = Array.from(selection ?? input.current.files ?? [])
        .map((file) => file.name)
        .join("、");
      setNames((previous) => (previous === next ? previous : next));
    });
    return (
      <span className="rong-file-picker" data-selected={Boolean(names)}>
        <input
          {...props}
          type="file"
          className={className}
          ref={(node) => {
            input.current = node;
            if (typeof forwardedRef === "function") forwardedRef(node);
            else if (forwardedRef) forwardedRef.current = node;
          }}
          onChange={(event) => {
            onChange?.(event);
            // Handlers may synchronously reject a file or clear after starting upload.
            setNames(
              Array.from(event.currentTarget.files ?? [])
                .map((file) => file.name)
                .join("、"),
            );
          }}
        />
        <span className="rong-file-trigger" aria-hidden="true">
          <Upload size={16} />
          选择文件
        </span>
        <span
          className="rong-file-name"
          aria-hidden="true"
          title={names || undefined}
        >
          {names ||
            `${formats || "从本机选取"}${props.multiple ? " · 支持多选" : ""}`}
        </span>
      </span>
    );
  },
);
