"""OCR кода с селфи — опционально (pytesseract + tesseract-ocr).

Нужно только для авто-сверки тарифа $29 (селфи с кодом): прочитать 4-значный код с
фото. Без tesseract модуль недоступен — тогда код сверяет оператор (селфи и
ожидаемый код видны в панели). Рукописные цифры OCR читает нестабильно, поэтому
авто срабатывает на чётко написанных/печатных кодах, остальное уходит оператору.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .base import log_call


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def read_digits(data: bytes) -> str:
    """Все цифры, распознанные на картинке (для поиска кода). Пусто, если OCR недоступен."""
    if not available():
        return ""
    import cv2
    import numpy as np
    import pytesseract
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return ""
    try:
        txt = pytesseract.image_to_string(img, config="--psm 11 -c tessedit_char_whitelist=0123456789")
    except Exception as e:  # noqa: BLE001
        log_call("ocr", "read", False, repr(e))
        return ""
    return re.sub(r"\D", "", txt)


def has_code(data: bytes, code: str) -> bool:
    """Виден ли код на фото (точное вхождение среди распознанных цифр)."""
    return bool(code) and code in read_digits(data)
