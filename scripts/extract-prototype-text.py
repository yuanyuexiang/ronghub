"""Read Mockplus page JSON without executing JavaScript. Output is evidence, not a UI spec."""

import argparse
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def texts(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"text", "value"} and isinstance(child, str):
                plain = html.unescape(re.sub(r"<[^>]+>", " ", child))
                plain = re.sub(r"\s+", " ", plain).strip()
                if plain:
                    yield plain
            elif isinstance(child, (dict, list)):
                yield from texts(child)
    elif isinstance(value, list):
        for child in value:
            yield from texts(child)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_ids", nargs="+")
    args = parser.parse_args()
    for page_id in args.page_ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", page_id):
            parser.error("Invalid page ID")
        path = ROOT / "references/customer/福州监管/data/pages" / f"{page_id}.js"
        raw = path.read_text(encoding="utf-8").split("=", 1)[1].strip().rstrip(";")
        print(json.dumps({"page_id": page_id, "texts": list(dict.fromkeys(texts(json.loads(raw))))}, ensure_ascii=False))
