def classFactory(iface):
    from .scimap_provider import ScimapProviderPlugin
    return ScimapProviderPlugin(iface)