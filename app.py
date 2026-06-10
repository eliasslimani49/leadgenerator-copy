"""Shim de compatibilité : démarre l'interface web Targetly.

Usage inchangé : python3 app.py
La logique vit dans targetly/platform/api/app.py.
"""
from targetly.platform.api.app import app, main  # noqa: F401 — ré-export (uvicorn app:app)

if __name__ == "__main__":
    main()
