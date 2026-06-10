"""Shim de compatibilité : CLI opérateur Targetly (ex vitryne_leads.py).

Usage : python3 targetly_leads.py "métier[,métier2,...]" "zone[,zone2,...]"
        [--dry-run] [--csv[=chemin]] [--healthcheck] [--report] [--history]
La logique vit dans targetly/cli/leads.py.
"""
from targetly.cli.leads import main

if __name__ == "__main__":
    main()
