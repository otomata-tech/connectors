"""Génère la sortie TypeScript, l'écrit, ou vérifie que la sortie commitée est à jour."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

from .descriptions import CONNECTORS, REPO, load_all
from .typescript import GENERATED_MARK, Module, connector_module, index_source

OUTPUT = REPO / "ts" / "src"


@dataclass
class Result:
    modules: list[Module]
    files: dict[str, str]


def generate(root: pathlib.Path = CONNECTORS) -> Result:
    """Toutes les descriptions, contrôlées d'abord : une seule fautive, et rien n'est généré."""
    descriptions = load_all(root)
    modules = [connector_module(description) for description in descriptions]
    files = {f"{m.connector}.ts": m.source for m in modules}
    files["index.ts"] = index_source(modules)
    return Result(modules=modules, files=files)


def _generated_on_disk(output: pathlib.Path) -> set[str]:
    return {
        path.name for path in output.glob("*.ts")
        if path.read_text(encoding="utf-8").startswith(GENERATED_MARK)
    }


def write(result: Result, output: pathlib.Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for stale in _generated_on_disk(output) - set(result.files):
        (output / stale).unlink()
    for name, source in result.files.items():
        (output / name).write_text(source, encoding="utf-8")


def stale(result: Result, output: pathlib.Path = OUTPUT) -> list[str]:
    """Les fichiers dont la version commitée n'est pas celle que la fabrique produit."""
    differ = []
    for name, source in sorted(result.files.items()):
        path = output / name
        if not path.exists() or path.read_text(encoding="utf-8") != source:
            differ.append(name)
    differ += sorted(_generated_on_disk(output) - set(result.files))
    return differ
