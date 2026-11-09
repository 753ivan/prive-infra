"""CLI эталонного теста: замер полноты/FPR поиска по лицу и подбор порога SAME.

    python scripts/reference_test.py spec.json

spec.json:
    {"refs": ["/path/ref1.jpg", "/path/ref2.jpg"],
     "positives": ["https://site/anketa1", ...],
     "negatives": ["https://site/other-person", ...]}

Нужны модель лица (opencv + модели YuNet/SFace) и сеть к указанным URL.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import reftest  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/reference_test.py spec.json")
        return 2
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    res = reftest.evaluate(spec)
    print(f"позитивов: {res['positives']}, негативов: {res['negatives']}")
    print(f"цель: полнота ≥ {res['targets']['recall']}, FPR ≤ {res['targets']['fpr']}\n")
    print(f"{'порог':>6} {'полнота':>9} {'FPR':>7}")
    for r in res["rows"]:
        print(f"{r['threshold']:>6} {r['recall']:>9} {r['fpr']:>7}")
    rec = res["recommended"]
    if rec:
        print(f"\nрекомендуемый порог SAME: {rec['threshold']} (полнота {rec['recall']}, FPR {rec['fpr']})")
        print("вписать в app/services/face.py: SAME =", rec["threshold"])
    else:
        print("\nнет порога с FPR ≤ цели — нужны более качественные эталоны или больше данных")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
