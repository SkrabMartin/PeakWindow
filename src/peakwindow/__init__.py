"""PeakWindow: historical Czech electricity-demand forecasting laboratory."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("peakwindow")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"

__all__ = ["__version__"]
