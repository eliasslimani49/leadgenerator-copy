"""Ajoute la racine du dépôt au sys.path pour l'exécution directe des runners.

Permet `python3 tests/test_xxx.py` depuis la racine (ou n'importe où) sans
installation du package : sys.path[0] est tests/, on y préfixe la racine.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
