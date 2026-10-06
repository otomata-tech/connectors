"""Lecture et contrôle des fichiers de description.

Un fichier est lu, validé contre `connectors/connector.schema.json` (chaque exemple
contre l'entrée de sa fonction), puis contre les deux règles que le schéma JSON ne
sait pas dire :

1. chaque argument d'entrée va à un seul endroit de `call` (chemin, query, corps ou
   en-tête), chaque `{param}` du chemin et chaque valeur de `query`, `body` et
   `headers` nomme un argument déclaré, un argument du chemin est requis, aucun
   argument n'est sans place, et le `request_param` d'une pagination est un
   argument ;
2. chaque référence de `auth` (`key`, `token`, `username`, `password`, `client_id`,
   `client_secret`) nomme un champ de `credential`.

Un fichier fautif lève `DescriptionError`, qui nomme le fichier, la fonction et la
règle : la fabrique ne génère alors rien.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

REPO = pathlib.Path(__file__).resolve().parent.parent
CONNECTORS = REPO / "connectors"
SCHEMA_PATH = CONNECTORS / "connector.schema.json"

PATH_PARAM = re.compile(r"\{([^{}]*)\}")
AUTH_REFERENCES = ("key", "token", "username", "password", "client_id", "client_secret")
PLACES = ("query", "body", "headers")


class DescriptionError(ValueError):
    """Une description refusée : le message dit le fichier, la fonction et la règle."""


@dataclass(frozen=True)
class Description:
    path: pathlib.Path
    data: dict

    @property
    def name(self) -> str:
        return self.data["connector"]["name"]


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def check_schema(data: dict, where: str) -> None:
    """Le schéma du format, puis chaque exemple contre l'entrée de sa fonction."""
    errors = sorted(Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        at = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the description schema at {at}: {first.message}")
    for function in data["functions"]:
        validator = Draft202012Validator(function["input"])
        for example in function["examples"]:
            error = next(iter(validator.iter_errors(example["input"])), None)
            if error is not None:
                raise DescriptionError(
                    f"{where}: function {function['name']}: example {example['title']!r} "
                    f"does not match its input: {error.message}"
                )


def check_arguments(function: dict, where: str) -> None:
    """Règle 1 : un argument va à un seul endroit, et tout endroit nomme un argument."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    declared = set(function["input"]["properties"])
    required = set(function["input"].get("required", []))
    placed: dict[str, str] = {}

    def place(argument: str, at: str) -> None:
        if argument not in declared:
            raise DescriptionError(f"{where}: function {name}: {at} names {argument!r}, which is not an input argument")
        if argument in placed:
            raise DescriptionError(
                f"{where}: function {name}: argument {argument!r} goes to two places ({placed[argument]} and {at})"
            )
        placed[argument] = at

    for argument in PATH_PARAM.findall(call["path"]):
        place(argument, "the path")
        if argument not in required:
            raise DescriptionError(f"{where}: function {name}: path argument {argument!r} must be required")
    for kind in PLACES:
        for api_name, argument in (call.get(kind) or {}).items():
            place(argument, f"{kind} {api_name!r}")
    pagination = function.get("pagination")
    unplaced = declared - set(placed)
    if unplaced:
        raise DescriptionError(
            f"{where}: function {name}: argument(s) {', '.join(sorted(unplaced))} go nowhere in call"
        )
    if pagination and pagination["request_param"] not in declared:
        raise DescriptionError(
            f"{where}: function {name}: pagination request_param {pagination['request_param']!r} is not an input argument"
        )


def check_auth(connector: dict, where: str) -> None:
    """Règle 2 : une référence de `auth` désigne un champ de `credential` existant."""
    auth = connector["auth"]
    fields = {field["name"] for field in connector.get("credential", [])}
    for reference in AUTH_REFERENCES:
        if reference in auth and auth[reference] not in fields:
            raise DescriptionError(
                f"{where}: auth.{reference} names {auth[reference]!r}, which is not a credential field "
                f"({', '.join(sorted(fields)) or 'none declared'})"
            )


def check(data: dict, path: pathlib.Path) -> Description:
    where = _relative(path)
    if not isinstance(data, dict):
        raise DescriptionError(f"{where}: not a mapping")
    check_schema(data, where)
    if data["connector"]["name"] != path.parent.name:
        raise DescriptionError(f"{where}: connector.name {data['connector']['name']!r} is not the folder name")
    check_auth(data["connector"], where)
    for function in data["functions"]:
        check_arguments(function, where)
    names = [function["name"] for function in data["functions"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: function name(s) declared twice: {', '.join(twice)}")
    return Description(path=path, data=data)


def load(path: pathlib.Path) -> Description:
    return check(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def load_all(root: pathlib.Path = CONNECTORS) -> list[Description]:
    return [load(path) for path in sorted(root.glob("*/connector.yaml"))]
