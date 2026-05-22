# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Astrix client package

import importlib.metadata

try:
    __version__ = importlib.metadata.version("astrix-client")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"
