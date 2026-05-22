# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Astrix server package

import importlib.metadata

try:
    __version__ = importlib.metadata.version("astrix-server")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"
