from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lifemate")
except PackageNotFoundError:
    __version__ = "dev"
