from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from composer import compose


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_dataset(expanded_dir: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[str, dict], list[dict]]:
    categories = {
        path.stem: load_json(path)
        for path in (expanded_dir / "categories").glob("*.json")
    }
    merchants = {
        path.stem: load_json(path)
        for path in (expanded_dir / "merchants").glob("*.json")
    }
    customers = {
        path.stem: load_json(path)
        for path in (expanded_dir / "customers").glob("*.json")
    }
    triggers = {
        path.stem: load_json(path)
        for path in (expanded_dir / "triggers").glob("*.json")
    }
    pairs = load_json(expanded_dir / "test_pairs.json")["pairs"]
    return categories, merchants, customers, triggers, pairs


from concurrent.futures import ThreadPoolExecutor

def generate(expanded_dir: Path, output_path: Path) -> int:
    categories, merchants, customers, triggers, pairs = load_dataset(expanded_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_pairs = pairs[:30]

    def _process_pair(pair: dict[str, Any]) -> dict[str, Any]:
        trigger = triggers[pair["trigger_id"]]
        merchant = merchants[pair["merchant_id"]]
        category = categories[merchant["category_slug"]]
        customer_id = pair.get("customer_id")
        customer = customers.get(customer_id) if customer_id else None
        message = compose(category, merchant, trigger, customer)
        return {"test_id": pair["test_id"], **message}

    with ThreadPoolExecutor(max_workers=10) as executor:
        records = list(executor.map(_process_pair, selected_pairs))

    with output_path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the offline Vera challenge JSONL submission.")
    parser.add_argument("--expanded", type=Path, default=Path("expanded"))
    parser.add_argument("--output", type=Path, default=Path("submission.jsonl"))
    args = parser.parse_args()
    count = generate(args.expanded, args.output)
    print(f"Wrote {count} records to {args.output}")


if __name__ == "__main__":
    main()
