"""Модуль лица (опциональный): InsightFace (buffalo_l, ArcFace) на CPU.

Используется в двух местах:
  1. Верификация кружка (M1, шаг 2): кадры из video_note сравниваются с эталонами.
     Порог косинусного сходства 0.45 — «та же», ниже 0.30 — «не та», между — на оператора.
  2. Поиск лица на страницах находок (M4): дополняет pHash там, где фото обрезано
     или другой ракурс.

Установка: pip install insightface onnxruntime opencv-python-headless (модель ~300 МБ скачается при первом запуске).
Если библиотеки нет — все функции возвращают None, и конвейер работает без них.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import settings
from .base import client, log_call

SAME = 0.45
NOT_SAME = 0.30


def available() -> bool:
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _app():
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"], root=str(settings.storage_dir.parent / "models"))
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def _img(data: bytes):
    import cv2
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def embeddings(data: bytes) -> list[np.ndarray]:
    """Все лица на картинке → нормированные эмбеддинги."""
    img = _img(data)
    if img is None:
        return []
    faces = _app().get(img)
    return [f.normed_embedding for f in faces if f.det_score > 0.5]


def best_similarity(a: list[np.ndarray], b: list[np.ndarray]) -> float:
    if not a or not b:
        return -1.0
    return float(max(float(np.dot(x, y)) for x in a for y in b))


def reference_embeddings(client_row) -> list[np.ndarray]:
    """Эмбеддинги эталонов; кэшируются в .npy рядом с фото, чтобы не считать повторно."""
    out = []
    for p in client_row.reference_photos or []:
        f = settings.storage_dir / p["file"]
        cache = f.with_suffix(f.suffix + ".emb.npy")
        try:
            if cache.exists():
                out.extend(list(np.load(cache)))
                continue
            emb = embeddings(f.read_bytes())
            if emb:
                np.save(cache, np.stack(emb))
                out.extend(emb)
        except Exception as e:  # noqa: BLE001
            log_call("face", f"ref {p['file']}", False, repr(e))
    return out


def video_frames(path: Path, n: int = 6) -> list[bytes]:
    """n кадров из кружка через ffmpeg."""
    frames = []
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", f"fps=1,scale=640:-1", "-frames:v", str(n), f"{td}/f%02d.jpg"],
                       check=False, timeout=60)
        for f in sorted(Path(td).glob("f*.jpg")):
            frames.append(f.read_bytes())
    return frames


def verify_circle(client_row) -> dict | None:
    """Сверка кружка с эталонами. None — модуль недоступен или нет данных."""
    if not available() or not client_row.circle_file or not client_row.reference_photos:
        return None
    try:
        refs = reference_embeddings(client_row)
        frames = video_frames(settings.storage_dir / client_row.circle_file)
        if not refs or not frames:
            return {"score": None, "decision": "manual", "reason": "лицо не найдено на эталонах или в кружке"}
        scores = [best_similarity(embeddings(fr), refs) for fr in frames]
        valid = [s for s in scores if s >= 0]
        if not valid:
            return {"score": None, "decision": "manual", "reason": "лицо в кружке не распознано"}
        score = max(valid)
        # признак «живости» — минимальный: лицо есть на большинстве кадров и оценки не идентичны (не статичное фото)
        liveness = len(valid) >= max(2, len(frames) // 2) and (len(set(round(s, 3) for s in valid)) > 1)
        decision = "verified" if score >= SAME and liveness else "rejected" if score < NOT_SAME else "manual"
        log_call("face", f"circle {client_row.code}", True, f"score {score:.2f}, frames {len(valid)}/{len(frames)}, {decision}")
        return {"score": round(score, 3), "decision": decision, "frames_with_face": len(valid), "liveness": liveness}
    except Exception as e:  # noqa: BLE001
        log_call("face", f"circle {client_row.code}", False, repr(e))
        return {"score": None, "decision": "manual", "reason": repr(e)}


def match_page_faces(url: str, refs: list[np.ndarray], max_images: int = 25) -> dict:
    """Лицо клиентки на картинках страницы? Возвращает лучшее сходство."""
    from .images import page_images
    best = {"similarity": -1.0, "image_url": "", "images": 0}
    try:
        imgs = page_images(url, max_images)
    except Exception as e:  # noqa: BLE001
        log_call("face", f"page {url}", False, repr(e))
        return best
    best["images"] = len(imgs)
    with client() as c:
        for iu in imgs:
            try:
                r = c.get(iu)
                if r.status_code != 200 or len(r.content) > 15_000_000:
                    continue
                s = best_similarity(embeddings(r.content), refs)
                if s > best["similarity"]:
                    best.update({"similarity": s, "image_url": iu})
            except Exception:
                continue
    log_call("face", f"page {url}", True, f"{best['images']} img, best {best['similarity']:.2f}")
    return best


def status() -> dict:
    return {"insightface": available(), "ffmpeg": subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0}


# ---------------------------------------------------------------------------
# Автовыбор эталонов для внешнего поиска (SOP-1, шаг 11)

def _pose_bucket(face) -> str:
    """Грубый ракурс по ключевым точкам: нос между глаз → анфас, смещён → три четверти влево/вправо."""
    try:
        kps = face.kps  # [left_eye, right_eye, nose, mouth_l, mouth_r]
        eye_l, eye_r, nose = kps[0], kps[1], kps[2]
        width = max(abs(eye_r[0] - eye_l[0]), 1.0)
        rel = ((nose[0] - eye_l[0]) / width) - 0.5  # 0 = анфас
        if rel < -0.18:
            return "left"
        if rel > 0.18:
            return "right"
        return "frontal"
    except Exception:
        return "frontal"


def _sharpness(img) -> float:
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def score_reference_photos(client_row) -> list[dict]:
    """Оценивает каждый эталон: есть ли ровно одно лицо, его размер, резкость, ракурс, схожесть с остальными.
    Возвращает список {file, ok, reason, score, pose, face_px, sharpness, consistency}."""
    if not available():
        return []
    import cv2
    photos = list(client_row.reference_photos or [])
    entries = []
    for p in photos:
        f = settings.storage_dir / p["file"]
        e = {"file": p["file"], "ok": False, "reason": "", "score": 0.0, "pose": "", "face_px": 0, "sharpness": 0.0, "emb": None}
        try:
            img = _img(f.read_bytes())
            if img is None:
                e["reason"] = "не читается"
                entries.append(e)
                continue
            faces = [x for x in _app().get(img) if x.det_score > 0.5]
            if not faces:
                e["reason"] = "лицо не найдено"
            elif len(faces) > 1:
                e["reason"] = f"на фото {len(faces)} лица"
            else:
                face = faces[0]
                x1, y1, x2, y2 = [int(v) for v in face.bbox]
                e["face_px"] = max(x2 - x1, y2 - y1)
                crop = img[max(y1, 0):y2, max(x1, 0):x2]
                e["sharpness"] = round(_sharpness(crop), 1) if crop.size else 0.0
                e["pose"] = _pose_bucket(face)
                e["emb"] = face.normed_embedding
                if e["face_px"] < 80:
                    e["reason"] = "лицо слишком мелкое"
                elif e["sharpness"] < 40:
                    e["reason"] = "размыто"
                else:
                    e["ok"] = True
        except Exception as ex:  # noqa: BLE001
            e["reason"] = repr(ex)[:80]
        entries.append(e)
    # согласованность: насколько лицо похоже на остальные (отсекает чужие фото в наборе)
    embs = [e["emb"] for e in entries if e["emb"] is not None]
    for e in entries:
        if e["emb"] is None or len(embs) < 2:
            e["consistency"] = None
            continue
        others = [x for x in embs if x is not e["emb"]]
        e["consistency"] = round(float(np.mean([float(np.dot(e["emb"], o)) for o in others])), 3)
        if e["consistency"] < 0.25:
            e["ok"], e["reason"] = False, "не похоже на остальные эталоны"
    for e in entries:
        if e["ok"]:
            e["score"] = round(min(e["face_px"] / 400, 1.0) * 0.4 + min(e["sharpness"] / 300, 1.0) * 0.3 + (e["consistency"] or 0.5) * 0.3, 3)
        e.pop("emb", None)
    return entries


def pick_references(client_row, n: int = 3) -> dict:
    """Выбирает n эталонов: лучшие по качеству, по возможности с разными ракурсами."""
    scored = score_reference_photos(client_row)
    good = sorted([e for e in scored if e["ok"]], key=lambda e: -e["score"])
    picked, poses = [], set()
    for e in good:  # сначала по одному на ракурс
        if e["pose"] not in poses:
            picked.append(e)
            poses.add(e["pose"])
        if len(picked) >= n:
            break
    for e in good:  # добираем лучшими
        if len(picked) >= n:
            break
        if e not in picked:
            picked.append(e)
    log_call("face", f"pick refs {client_row.code}", True, f"{len(picked)}/{len(scored)}: " + ", ".join(e["pose"] for e in picked))
    return {"picked": [e["file"] for e in picked], "details": scored,
            "warning": "" if len(picked) >= min(n, 1) else "нет ни одного пригодного эталона — запросить фото с лицом"}
