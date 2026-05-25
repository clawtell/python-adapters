"""ClawTell binding adapter for the Nous Research Hermes agent framework.

Per-message ``AIAgent`` instantiation (Hermes is not thread-safe per
docs). Combine with ``clawtell_core.subscribe`` and a Telegram sender.
"""

from clawtell_hermes.adapter import HermesAdapter

__version__ = "2026.5.25"
__all__ = ["HermesAdapter"]
