import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = root / "references/customer/福州监管/data"
libs = json.loads((base / "lib.js").read_text().split("=", 1)[1].strip().rstrip(";"))
masters = {}


def index(x):
    if isinstance(x, dict):
        if "_id" in x and "libID" in x and "value" in x:
            masters[x["_id"]] = x
        for v in x.values():
            if isinstance(v, (dict, list)):
                index(v)
    elif isinstance(x, list):
        for v in x:
            index(v)


index(libs)


def menus(x):
    if isinstance(x, dict):
        if x.get("type") == "verticalMenu":
            by = {c["_id"]: c for c in x.get("components", [])}

            def tree(ns):
                return [
                    {
                        "label": " ".join(
                            str(z.get("value", z.get("text", "")))
                            for z in by.get(n["id"], {}).get("components", [])
                            if z.get("type") == "pureText"
                        ),
                        "children": tree(n.get("children", [])),
                    }
                    for n in ns
                ]

            yield tree(x.get("value", {}).get("relation", []))
            return
        for v in x.values():
            if isinstance(v, (dict, list)):
                yield from menus(v)
    elif isinstance(x, list):
        for v in x:
            yield from menus(v)


result = {}
for role, mid in [
    ("regulator", "ZallcoxJD"),
    ("association", "meb8iKCWD"),
    ("operator", "d-ZytfYiL"),
]:
    result[role] = next(menus(masters[mid]["value"]))
p = root / "apps/web/tests/fixtures/canvas-navigation.json"
p.parent.mkdir(exist_ok=True)
p.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print({role: len(tree) for role, tree in result.items()})
