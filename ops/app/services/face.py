"""Модуль лица (опциональный): YuNet (детектор) + SFace (распознавание) из OpenCV Zoo, на CPU.

Лицензия моделей — Apache-2.0 (коммерческое использование разрешено), в отличие от
InsightFace buffalo_l (non-commercial research only). Публичный интерфейс модуля не
изменился: те же функции, что вызывают pipeline и bot.

Используется в двух местах:
  1. Верификация кружка (M1, шаг 2): кадры из video_note сравниваются с эталонами.
     Порог косинусного сходства SFace ~0.363 — «та же», ниже NOT_SAME — «не та»,
     между — на оператора. Пороги калибруются на эталонном тесте.
  2. Поиск лица на страницах находок (M4): дополняет pHash.

Нужны: opencv-python-headless (cv2 с FaceDetectorYN/FaceRecognizerSF, >=4.5.4), ffmpeg
(кадры из кружка) и два файла моделей из OpenCV Zoo (скачиваются при первом запуске в
<storage>/../models/). Если cv2 или модели недоступны — функции возвращают None/пусто,
и конвейер работает без них.
"""
from __future__ import annotations

import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import settings
from .base import client, log_call

# SFace: косинус, выше = более похоже. OpenCV рекомендует 0.363 для «та же личность».
# Значения — стартовые, калибруются на эталонном тесте (эталонная персона + контролируемые страницы).
SAME = 0.363
NOT_SAME = 0.28

# Модели лежат в хранилище (том), чтобы переживать пересоздание контейнера и не качаться на каждый деплой.
_MODELS_DIR = settings.storage_dir / "_models"
# Файлы в opencv_zoo — Git LFS, поэтому берём их с media-CDN (raw-ссылка отдаёт только указатель LFS).
_YUNET = (
    "face_detection_yunet_2023mar.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
)
_SFACE = (
    "face_recognition_sface_2021dec.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
)


# ---------------------------------------------------------------------------
# Чистка кружка (политика приватности — не зависит от наличия модуля лица)

def delete_circle(client_row) -> bool:
    """Удаляет видео-кружок сразу после сверки личности — политика PRIVÉ:
    «кружок удаляем после сверки». Чистит файл на диске и очищает поле у клиентки.
    Работает и без cv2/моделей, идемпотентна."""
    rel = (getattr(client_row, "circle_file", "") or "").strip()
    if not rel:
        return False
    code = getattr(client_row, "code", "?")
    try:
        (settings.storage_dir / rel).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log_call("face", f"delete circle {code}", False, repr(e))
        return False
    client_row.circle_file = ""
    log_call("face", f"delete circle {code}", True, "удалён после сверки")
    return True


# ---------------------------------------------------------------------------
# Доступность и модели

@lru_cache(maxsize=1)
def _cv2_ok() -> bool:
    try:
        import cv2  # noqa: F401
        return hasattr(cv2, "FaceDetectorYN_create") and hasattr(cv2, "FaceRecognizerSF_create")
    except Exception:
        return False


def _models_present() -> bool:
    return all((_MODELS_DIR / name).exists() and (_MODELS_DIR / name).stat().st_size > 0 for name, _ in (_YUNET, _SFACE))


def _ensure_models() -> bool:
    if _models_present():
        return True
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in (_YUNET, _SFACE):
        dst = _MODELS_DIR / name
        if dst.exists() and dst.stat().st_size > 0:
            continue
        try:
            with client() as c:
                r = c.get(url, timeout=120, follow_redirects=True)
                r.raise_for_status()
                data = r.content
            if len(data) < 10_000:  # LFS-указатель вместо самого файла
                log_call("face", f"download {name}", False, f"слишком мал ({len(data)} b) — не тот URL/LFS")
                return False
            dst.write_bytes(data)
            log_call("face", f"download {name}", True, f"{len(data)} bytes")
        except Exception as e:  # noqa: BLE001
            log_call("face", f"download {name}", False, repr(e))
            return False
    return _models_present()


def available() -> bool:
    return _cv2_ok() and _ensure_models()


@lru_cache(maxsize=1)
def _detector():
    import cv2
    # input_size переопределяется под каждый кадр через setInputSize; score/nms/top_k — дефолты OpenCV.
    return cv2.FaceDetectorYN_create(str(_MODELS_DIR / _YUNET[0]), "", (320, 320), 0.6, 0.3, 5000)


@lru_cache(maxsize=1)
def _recognizer():
    import cv2
    return cv2.FaceRecognizerSF_create(str(_MODELS_DIR / _SFACE[0]), "")


# ---------------------------------------------------------------------------
# Детекция и эмбеддинги

def _img(data: bytes):
    import cv2
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _detect(img) -> list:
    """Лица на изображении: список строк YuNet [x,y,w,h, 5×(x,y) ключевых точек, score]."""
    if img is None:
        return []
    det = _detector()
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    return [] if faces is None else [f for f in faces]


def _feature(img, face_row) -> np.ndarray | None:
    """Выровнять лицо и получить L2-нормированный 128-мерный эмбеддинг SFace."""
    rec = _recognizer()
    try:
        aligned = rec.alignCrop(img, face_row)
        feat = np.asarray(rec.feature(aligned)).flatten().astype(np.float32)
    except Exception:
        return None
    n = float(np.linalg.norm(feat))
    return feat / n if n > 0 else None


def embeddings(data: bytes) -> list[np.ndarray]:
    """Все лица на картинке → L2-нормированные эмбеддинги (косинус = скалярное произведение)."""
    if not available():
        return []
    img = _img(data)
    out = []
    for f in _detect(img):
        emb = _feature(img, f)
        if emb is not None:
            out.append(emb)
    return out


def best_similarity(a: list[np.ndarray], b: list[np.ndarray]) -> float:
    if not a or not b:
        return -1.0
    return float(max(float(np.dot(x, y)) for x in a for y in b))


def reference_embeddings(client_row) -> list[np.ndarray]:
    """Эмбеддинги эталонов; кэшируются в .sface.npy рядом с фото, чтобы не считать повторно.
    Суффикс отличается от прежнего .emb.npy (ArcFace 512-мерные) — старый кэш не подхватывается."""
    out = []
    for p in client_row.reference_photos or []:
        f = settings.storage_dir / p["file"]
        cache = f.with_suffix(f.suffix + ".sface.npy")
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
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "fps=1,scale=640:-1", "-frames:v", str(n), f"{td}/f%02d.jpg"],
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
    return {
        "engine": "yunet+sface",
        "opencv": _cv2_ok(),
        "models": _models_present(),
        "ffmpeg": subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0,
    }


# ---------------------------------------------------------------------------
# Автовыбор эталонов для внешнего поиска (SOP-1, шаг 11)

def _pose_bucket(face_row) -> str:
    """Грубый ракурс по ключевым точкам YuNet [x,y,w,h, re_x,re_y, le_x,le_y, nose_x,nose_y, ...].
    Нос между глаз → анфас, смещён → три четверти вбок (левый/правый — приблизительно)."""
    try:
        re_x, le_x, nose_x = float(face_row[4]), float(face_row[6]), float(face_row[8])
        width = max(abs(le_x - re_x), 1.0)
        rel = ((nose_x - re_x) / width) - 0.5  # 0 = анфас
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
            faces = _detect(img)
            if not faces:
                e["reason"] = "лицо не найдено"
            elif len(faces) > 1:
                e["reason"] = f"на фото {len(faces)} лица"
            else:
                fr = faces[0]
                x, y, w, h = [int(v) for v in fr[0:4]]
                e["face_px"] = max(w, h)
                crop = img[max(y, 0):y + h, max(x, 0):x + w]
                e["sharpness"] = round(_sharpness(crop), 1) if crop.size else 0.0
                e["pose"] = _pose_bucket(fr)
                e["emb"] = _feature(img, fr)
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
        if e["consistency"] < 0.2:
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
