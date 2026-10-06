"""Optional language ID via fastText lid.176.ftz (~1 MB). Silently disabled if unavailable."""
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("language")
MODEL_PATH = os.getenv("FASTTEXT_MODEL_PATH", str(Path(__file__).resolve().parent.parent / "data" / "lid.176.ftz"))
_model = None
_disabled = False


def _load():
    global _model, _disabled
    if _model is not None or _disabled:
        return _model
    try:
        import fasttext
        _model = fasttext.load_model(MODEL_PATH)
    except Exception as e:
        _disabled = True
        log.warning("Language detection disabled: %s", e)
    return _model


def detect_language(text: str, min_chars: int = 20) -> tuple[str | None, float]:
    clean = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", text or "")).strip()
    if len(clean) < min_chars:
        return None, 0.0
    model = _load()
    if model is None:
        return None, 0.0
    global _disabled
    try:
        labels, probs = model.predict(clean[:1000], k=1)
    except Exception as e:   # e.g. fasttext + numpy 2 incompatibility
        _disabled = True
        log.warning("Language detection disabled: %s", e)
        return None, 0.0
    return labels[0].removeprefix("__label__"), float(probs[0])