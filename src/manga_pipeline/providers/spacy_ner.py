"""CPU spaCy NER; known names are matched separately by the scan stage."""


class SpacyNames:
    def __init__(self, model="en_core_web_sm"):
        self.model = model
        self.nlp = None

    def detect(self, text):
        if self.nlp is None:
            try:
                import spacy
                self.nlp = spacy.load(self.model, disable=["parser", "lemmatizer"])
            except (ImportError, OSError) as exc:
                raise RuntimeError(f"spaCy/model missing. Install requirements-names.txt and run "
                                   f"python -m spacy download {self.model}") from exc
        return [{"name": e.text, "start": e.start_char, "end": e.end_char, "source": "spacy"}
                for e in self.nlp(text).ents if e.label_ == "PERSON"]
