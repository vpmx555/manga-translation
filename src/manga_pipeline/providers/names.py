"""Config-aware NER factory; old runs retain their spaCy backend."""


def create_detector(config):
    ner = config.get("ner")
    if ner is None:
        from .spacy_ner import SpacyNames
        return SpacyNames(config["spacy_model"])
    if ner["backend"] == "spacy":
        from .spacy_ner import SpacyNames
        return SpacyNames(config["spacy_model"])
    if ner["backend"] == "gliner":
        from .gliner_ner import GlinerNames
        return GlinerNames(ner)
    raise ValueError("Unknown NER backend")
