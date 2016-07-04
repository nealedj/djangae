try:
    import search
except ImportError:
    raise ImportError("Cannot import search package. To use djangae.contrib.search please make sure that https://github.com/potatolondon/search is available")
