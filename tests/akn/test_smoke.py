from importlib import import_module


def test_subpackages_importable() -> None:
    for name in (
        "codify.akn",
        "codify.pipeline",
        "codify.embed",
        "codify.retrieve",
        "codify.compare",
        "codify.types",
    ):
        import_module(name)
