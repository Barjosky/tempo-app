"""Lance les tests sans dependance externe (`pytest` optionnel).

Deux familles : `test_rules` confronte les regles Tempo aux VRAIES donnees collectees
et se saute donc quand la base n'est pas la (machine neuve, CI sans cache) ;
`test_pipeline` tourne sur une base synthetique et doit passer partout.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


class Skip(Exception):
    """Le test n'a pas de quoi s'executer -- ce n'est pas un echec."""


import test_pipeline
import test_rules

failures = skipped = 0
for module in (test_rules, test_pipeline):
    print(f"\n{module.__name__} :")
    for name in sorted(n for n in dir(module) if n.startswith("test_")):
        try:
            getattr(module, name)()
            print(f"  OK    {name}")
        except Skip as exc:
            skipped += 1
            print(f"  SAUTE {name} ({exc})")
        except Exception as exc:
            failures += 1
            print(f"  ECHEC {name}: {exc}")

print(f"\n{failures} echec(s), {skipped} saute(s)")
sys.exit(1 if failures else 0)
