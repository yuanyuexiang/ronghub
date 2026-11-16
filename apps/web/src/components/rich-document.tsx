"use client";
import { Fragment, type ReactNode } from "react";
import type { JSONContent } from "@tiptap/core";
import type { ApiSchemas } from "../lib/typed-api";
export type RichDocument = ApiSchemas["RichDocument-Input"];
type RichNode = ApiSchemas["RichNode-Input"];
type RichMark = ApiSchemas["RichMark"];
const types = new Set<string>([
  "paragraph",
  "heading",
  "text",
  "hardBreak",
  "bulletList",
  "orderedList",
  "listItem",
  "blockquote",
  "codeBlock",
  "horizontalRule",
  "table",
  "tableRow",
  "tableCell",
  "tableHeader",
  "privateImage",
]);
function isType(value: string): value is RichNode["type"] {
  return types.has(value);
}
function markType(value: string): value is RichMark["type"] {
  return ["bold", "italic", "underline", "strike", "code", "link"].includes(
    value,
  );
}
export function documentFromEditor(json: JSONContent): RichDocument {
  function node(value: JSONContent): RichNode {
    if (!value.type || !isType(value.type))
      throw new Error("正文包含尚未支持的内容类型");
    const attrs: NonNullable<RichNode["attrs"]> = {};
    for (const key of [
      "level",
      "start",
      "colspan",
      "rowspan",
      "attachment_id",
    ]) {
      const item = value.attrs?.[key];
      if (typeof item === "number") attrs[key] = item;
    }
    if (typeof value.attrs?.alt === "string") attrs.alt = value.attrs.alt;
    if (typeof value.attrs?.language === "string")
      attrs.language = value.attrs.language;
    const marks = value.marks?.map((mark) => {
      if (!markType(mark.type)) throw new Error("正文包含尚未支持的格式");
      return {
        type: mark.type,
        ...(mark.type === "link"
          ? { attrs: { href: String(mark.attrs?.href ?? "") } }
          : {}),
      };
    });
    return {
      type: value.type,
      ...(value.text !== undefined ? { text: value.text } : {}),
      ...(Object.keys(attrs).length ? { attrs } : {}),
      ...(marks?.length ? { marks } : {}),
      ...(value.content ? { content: value.content.map(node) } : {}),
    };
  }
  return { type: "doc", content: (json.content ?? []).map(node) };
}
export function editorDocument(
  document: RichDocument | null,
  body: string,
): JSONContent {
  function node(value: RichNode): JSONContent {
    return {
      type: value.type,
      ...(value.text != null ? { text: value.text } : {}),
      ...(value.attrs ? { attrs: value.attrs } : {}),
      ...(value.marks?.length
        ? {
            marks: value.marks.map((mark) => ({
              type: mark.type,
              ...(mark.attrs ? { attrs: mark.attrs } : {}),
            })),
          }
        : {}),
      ...(value.content ? { content: value.content.map(node) } : {}),
    };
  }
  return document
    ? { type: "doc", content: document.content.map(node) }
    : {
        type: "doc",
        content: body.split("\n").map((line) => ({
          type: "paragraph",
          ...(line ? { content: [{ type: "text", text: line }] } : {}),
        })),
      };
}
export function imageUrl(articleId: number, imageId: number, manage: boolean) {
  return `/api/v1/content/${articleId}/attachments/${imageId}?inline=true&manage=${manage}`;
}
export function RichContent({
  document,
  body,
  articleId,
  manage,
}: {
  document: RichDocument | null;
  body: string;
  articleId: number;
  manage: boolean;
}) {
  if (!document) return <p className="article-body">{body}</p>;
  function render(node: RichNode, key: number): ReactNode {
    const children = node.content?.map((child, index) => render(child, index));
    const attrs = node.attrs ?? {};
    switch (node.type) {
      case "text": {
        let result: ReactNode = node.text ?? "";
        for (const mark of node.marks ?? []) {
          switch (mark.type) {
            case "bold":
              result = <strong>{result}</strong>;
              break;
            case "italic":
              result = <em>{result}</em>;
              break;
            case "underline":
              result = <u>{result}</u>;
              break;
            case "strike":
              result = <s>{result}</s>;
              break;
            case "code":
              result = <code>{result}</code>;
              break;
            case "link": {
              const href = mark.attrs?.href;
              result =
                typeof href === "string" && /^https?:\/\//.test(href) ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                  >
                    {result}
                  </a>
                ) : (
                  result
                );
              break;
            }
          }
        }
        return <Fragment key={key}>{result}</Fragment>;
      }
      case "paragraph":
        return <p key={key}>{children ?? <br />}</p>;
      case "heading":
        return attrs.level === 1 ? (
          <h1 key={key}>{children}</h1>
        ) : attrs.level === 2 ? (
          <h2 key={key}>{children}</h2>
        ) : (
          <h3 key={key}>{children}</h3>
        );
      case "bulletList":
        return <ul key={key}>{children}</ul>;
      case "orderedList":
        return (
          <ol
            key={key}
            start={typeof attrs.start === "number" ? attrs.start : 1}
          >
            {children}
          </ol>
        );
      case "listItem":
        return <li key={key}>{children}</li>;
      case "blockquote":
        return <blockquote key={key}>{children}</blockquote>;
      case "codeBlock":
        return (
          <pre key={key}>
            <code>{children}</code>
          </pre>
        );
      case "hardBreak":
        return <br key={key} />;
      case "horizontalRule":
        return <hr key={key} />;
      case "table":
        return (
          <div className="rich-table-wrap" key={key}>
            <table>
              <tbody>{children}</tbody>
            </table>
          </div>
        );
      case "tableRow":
        return <tr key={key}>{children}</tr>;
      case "tableHeader":
      case "tableCell": {
        const props = {
          colSpan: typeof attrs.colspan === "number" ? attrs.colspan : 1,
          rowSpan: typeof attrs.rowspan === "number" ? attrs.rowspan : 1,
        };
        return node.type === "tableHeader" ? (
          <th key={key} {...props}>
            {children}
          </th>
        ) : (
          <td key={key} {...props}>
            {children}
          </td>
        );
      }
      case "privateImage":
        return typeof attrs.attachment_id === "number" ? (
          <figure key={key}>
            <img
              src={imageUrl(articleId, attrs.attachment_id, manage)}
              alt={typeof attrs.alt === "string" ? attrs.alt : "正文图片"}
              loading="lazy"
            />
          </figure>
        ) : null;
    }
  }
  return (
    <div className="rich-document" aria-label="富文本正文">
      {document.content.map((node, index) => render(node, index))}
    </div>
  );
}
