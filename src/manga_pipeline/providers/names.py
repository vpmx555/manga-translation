"""GLiNER is the sole runtime detector; tests inject mocks into Pipeline."""


def create_detector(config):
    if config.get("ner", {}).get("backend") != "gliner":
        raise ValueError("Only GLiNER is supported")
    from .gliner_ner import GlinerNames
    return GlinerNames(config["ner"])
