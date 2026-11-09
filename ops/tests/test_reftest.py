from app.services import reftest


def test_evaluate_recall_fpr_and_recommendation():
    # _sims задан напрямую — без сети и моделей
    spec = {
        "positives": ["p1", "p2", "p3", "p4", "p5"],
        "negatives": ["n1", "n2", "n3", "n4"],
        "_sims": {"p1": 0.9, "p2": 0.8, "p3": 0.7, "p4": 0.5, "p5": 0.1,
                  "n1": 0.1, "n2": 0.2, "n3": 0.3, "n4": 0.4},
    }
    res = reftest.evaluate(spec, thresholds=[0.3, 0.4, 0.5, 0.6])
    assert res["positives"] == 5 and res["negatives"] == 4
    r06 = next(r for r in res["rows"] if r["threshold"] == 0.6)
    assert r06["recall"] == 0.6 and r06["fpr"] == 0.0  # 3/5 позитивов, 0 ложных
    # лучший: порог 0.5 — полнота 0.8 при нулевом FPR (0.4 даёт ту же полноту, но FPR 0.25)
    assert res["recommended"]["threshold"] == 0.5
    assert res["recommended"]["recall"] == 0.8 and res["recommended"]["fpr"] == 0.0


def test_no_threshold_meets_target():
    spec = {"positives": ["p1"], "negatives": ["n1"], "_sims": {"p1": 0.5, "n1": 0.9}}
    res = reftest.evaluate(spec, thresholds=[0.3, 0.6])
    assert res["recommended"] is None  # негатив всегда «похож» сильнее позитива
