def classFactory(iface):
    from .plugin import EiaAiAssistantPlugin
    return EiaAiAssistantPlugin(iface)
