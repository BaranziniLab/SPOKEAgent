"""SPOKEAgent database connector."""
__version__ = "0.5.0"
__all__ = ['create_spoke_server', 'main', 'SPOKEConfig', '__version__']


def __getattr__(name):
    if name in __all__:
        from . import server
        return getattr(server, name)
    raise AttributeError(name)
