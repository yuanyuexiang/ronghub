"""Export deterministic contracts without connecting to PostgreSQL or including credentials."""

import argparse
import json
import os
from pathlib import Path


def artifacts():
    # Schema generation must work without a .env or reachable DB. No queries are executed.
    original_url = os.environ.get("DATABASE_URL")
    if original_url is None:
        os.environ["DATABASE_URL"] = "postgresql+psycopg://contract:unused@127.0.0.1:1/contract"
    try:
        from app.main import app

        schema = app.openapi()
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
    operations = [
        (method.upper(), path, operation)
        for path, value in schema["paths"].items()
        for method, operation in value.items()
        if method in {"get", "post", "patch", "put", "delete", "head", "options"}
    ]
    validated = [row for row in operations if row[2]["x-response-contract"] == "validated"]
    lines = [
        "# API契约覆盖清单",
        "",
        "由 `uv run python -m app.export_contract` 生成。validated表示FastAPI配置了响应校验模型；unspecified不能当作已定义响应结构，二进制/HTML接口也需单独完善媒体类型。",
        "",
        f"共{len(operations)}个操作，{len(validated)}个有响应校验模型。该数量不代表业务验收完成。",
        "",
        "| 方法 | 路径 | 响应契约 | 认证 |",
        "|---|---|---|---|",
    ]
    for method, path, operation in sorted(operations):
        auth = ", ".join(operation["security"][0]) if operation["security"] else "public"
        lines.append(f"| {method} | `{path}` | {operation['x-response-contract']} | {auth} |")
    return {
        "docs/api/openapi.json": json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        "docs/api/契约覆盖清单.md": "\n".join(lines) + "\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    for relative, content in artifacts().items():
        path = root / relative
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Contract drift: regenerate {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    print(
        "API contract artifacts match source."
        if args.check
        else "API contract artifacts generated."
    )


if __name__ == "__main__":
    main()
