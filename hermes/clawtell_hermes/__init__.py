"""ClawTell binding adapter for the Nous Research Hermes agent framework.

Per-message ``AIAgent`` instantiation (Hermes is not thread-safe per
docs). Combine with ``clawtell_core.subscribe`` and a Telegram sender.
"""

from clawtell_hermes.adapter import HermesAdapter

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("clawtell-hermes")
except PackageNotFoundError:
    __version__ = "0+unknown"
finally:
    try:
        del _pkg_version, PackageNotFoundError
    except NameError:
        pass

__all__ = ["HermesAdapter"]
