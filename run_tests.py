"""Lance les tests de regles metier sans dependance externe (`pytest` optionnel)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "tests"))
import test_rules

failures = 0
for name in sorted(n for n in dir(test_rules) if n.startswith("test_")):
    try:
        getattr(test_rules, name)()
        print(f"  OK    {name}")
    except Exception as exc:
        failures += 1
        print(f"  ECHEC {name}: {exc}")

print(f"\n{failures} echec(s)")
sys.exit(1 if failures else 0)
