"""Minimal stand-in for WeTextProcessing's English normalizer: pass-through."""


class Normalizer:
    def __init__(self, *args, **kwargs):
        pass

    def normalize(self, text):
        return text
