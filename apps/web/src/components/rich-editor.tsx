"use client";
import { useEffect, useRef, useState } from "react";
import { Node } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import {
  documentFromEditor,
  editorDocument,
  imageUrl,
  type RichDocument,
} from "./rich-document";
export function RichEditor({
  document,
  body,
  articleId,
  disabled,
  onChange,
}: {
  document: RichDocument | null;
  body: string;
  articleId: number | null;
  disabled: boolean;
  onChange: (document: RichDocument, plain: string) => void;
}) {
  const linkRange = useRef<{ from: number; to: number } | null>(null);
  const callback = useRef(onChange);
  callback.current = onChange;
  const [error, setError] = useState(""),
    [link, setLink] = useState(""),
    [showLink, setShowLink] = useState(false);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        link: { autolink: false, linkOnPaste: false, openOnClick: false },
      }),
      TableKit.configure({ table: { resizable: false } }),
      Node.create({
        name: "privateImage",
        group: "block",
        atom: true,
        addAttributes() {
          return { attachment_id: { default: null }, alt: { default: "" } };
        },
        parseHTML() {
          return [];
        },
        renderHTML({ node }) {
          return [
            "img",
            {
              "data-article-image": node.attrs.attachment_id,
              src: articleId
                ? imageUrl(articleId, Number(node.attrs.attachment_id), true)
                : undefined,
              alt: node.attrs.alt,
            },
          ];
        },
      }),
    ],
    content: editorDocument(document, body),
    immediatelyRender: false,
    shouldRerenderOnTransaction: true,
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-label": "内容正文",
        "aria-multiline": "true",
        class: "rich-document rich-editor-content",
      },
    },
    onUpdate: ({ editor }) => {
      try {
        const doc = documentFromEditor(editor.getJSON());
        callback.current(doc, editor.getText({ blockSeparator: "\n" }));
        setError("");
      } catch (error) {
        setError(error instanceof Error ? error.message : "正文格式无效");
      }
    },
  });
  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [editor, disabled]);
  useEffect(() => {
    if (!editor) return;
    const incoming = editorDocument(document, body);
    try {
      if (
        JSON.stringify(documentFromEditor(editor.getJSON())) !==
        JSON.stringify(documentFromEditor(incoming))
      )
        editor.commands.setContent(incoming, { emitUpdate: false });
    } catch {
      editor.commands.setContent(incoming, { emitUpdate: false });
    }
  }, [editor, document, body]);
  if (!editor) return <p className="muted">正在加载正文编辑器…</p>;
  const button = (label: string, action: () => void, active = false) => (
    <button
      key={label}
      type="button"
      disabled={disabled}
      className={active ? "editor-tool active" : "editor-tool"}
      aria-label={label}
      aria-pressed={active}
      onMouseDown={(event) => event.preventDefault()}
      onClick={action}
    >
      {label}
    </button>
  );
  return (
    <div className="rich-editor">
      <div className="editor-toolbar" role="toolbar" aria-label="正文格式工具">
        <select
          aria-label="正文段落格式"
          disabled={disabled}
          value={
            [1, 2, 3].find((level) => editor.isActive("heading", { level })) ??
            0
          }
          onChange={(e) => {
            const level = Number(e.target.value);
            if (level === 1 || level === 2 || level === 3)
              editor.chain().focus().setHeading({ level }).run();
            else editor.chain().focus().setParagraph().run();
          }}
        >
          <option value="0">正文段落</option>
          <option value="1">一级标题</option>
          <option value="2">二级标题</option>
          <option value="3">三级标题</option>
        </select>
        {button(
          "加粗",
          () => {
            editor.chain().focus().toggleBold().run();
          },
          editor.isActive("bold"),
        )}
        {button(
          "斜体",
          () => {
            editor.chain().focus().toggleItalic().run();
          },
          editor.isActive("italic"),
        )}
        {button(
          "下划线",
          () => {
            editor.chain().focus().toggleUnderline().run();
          },
          editor.isActive("underline"),
        )}
        {button(
          "删除线",
          () => {
            editor.chain().focus().toggleStrike().run();
          },
          editor.isActive("strike"),
        )}
        {button(
          "项目列表",
          () => {
            editor.chain().focus().toggleBulletList().run();
          },
          editor.isActive("bulletList"),
        )}
        {button(
          "编号列表",
          () => {
            editor.chain().focus().toggleOrderedList().run();
          },
          editor.isActive("orderedList"),
        )}
        {button(
          "引用",
          () => {
            editor.chain().focus().toggleBlockquote().run();
          },
          editor.isActive("blockquote"),
        )}
        {button(
          "链接",
          () => {
            linkRange.current = {
              from: editor.state.selection.from,
              to: editor.state.selection.to,
            };
            setLink(String(editor.getAttributes("link").href ?? ""));
            setShowLink(!showLink);
          },
          editor.isActive("link"),
        )}
        {button("插入表格", () => {
          editor
            .chain()
            .focus()
            .insertTable({ rows: 2, cols: 3, withHeaderRow: true })
            .run();
        })}
        {editor.isActive("table") && (
          <>
            {button("表格加行", () => {
              editor.chain().focus().addRowAfter().run();
            })}
            {button("表格加列", () => {
              editor.chain().focus().addColumnAfter().run();
            })}
            {button("删除表格", () => {
              editor.chain().focus().deleteTable().run();
            })}
          </>
        )}
        {button("撤销", () => {
          editor.chain().focus().undo().run();
        })}
        {button("重做", () => {
          editor.chain().focus().redo().run();
        })}
      </div>
      {showLink && (
        <div className="editor-link">
          <input
            type="url"
            aria-label="正文链接地址"
            placeholder="https://example.com"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            disabled={disabled}
          />
          <button
            type="button"
            className="outline"
            disabled={disabled}
            onClick={() => {
              try {
                const url = new URL(link);
                if (
                  !["http:", "https:"].includes(url.protocol) ||
                  url.username ||
                  url.password
                )
                  throw new Error();
                const range = linkRange.current ?? {
                  from: editor.state.selection.from,
                  to: editor.state.selection.to,
                };
                editor
                  .chain()
                  .setTextSelection(range)
                  .setLink({ href: link })
                  .setTextSelection(range.to)
                  .focus(range.to)
                  .run();
                setShowLink(false);
                setError("");
              } catch {
                setError("链接请使用不含凭据的完整HTTP/HTTPS地址");
              }
            }}
          >
            应用链接
          </button>
          <button
            type="button"
            className="outline"
            onClick={() => {
              editor.chain().focus().unsetLink().run();
              setShowLink(false);
            }}
          >
            移除链接
          </button>
        </div>
      )}
      <EditorContent editor={editor} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <small className="muted">
        支持标题、文字格式、列表、链接、引用和表格。正文图片需先保存草稿，再上传附件并插入。
      </small>
    </div>
  );
}
