from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("lifemate")
except PackageNotFoundError:
    __version__ = "dev"