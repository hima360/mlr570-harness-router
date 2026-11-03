"""MLR570 Phase 2: routed multi-agent harness (Harbor external agent)."""
import os

# The Ledger prices every call from config, so litellm's price list is never used; the bundled copy
# avoids a GitHub download at import time that stalls when the network is flaky.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

__version__ = "0.1.0"
