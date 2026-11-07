"""Server-side access to the AI Research Graph data (the same static shards the site uses)."""
from .search import SearchIndex, default_index

__all__ = ["SearchIndex", "default_index"]
