
import sys
from pathlib import Path

# Add project root/src to PYTHONPATH so `import trading_hydra` works
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
