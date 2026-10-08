"""Lecture et contrôle des fichiers de description.

Un fichier est lu, validé contre `connectors/connector.schema.json` (chaque exemple
contre l'entrée de sa fonction), puis contre les règles que le schéma JSON ne sait
pas dire :

1. chaque argument d'entrée va à un seul endroit de `call` (chemin, query, corps ou
   en-tête), chaque `{param}` du chemin et chaque valeur de `query`, `body` et
   `headers` nomme un argument déclaré, un argument du chemin est requis, aucun
   argument n'est sans place, et le `request_param` d'une pagination est un
   argument ;
2. chaque référence de `auth` (`key`, `token`, `username`, `password`, `client_id`,
   `client_secret`) nomme un champ de `credential` ;
3. un nom côté API reçoit une seule valeur : une constante de `call.constants` ne
   reprend pas un nom de la même place, un en-tête constant (du connecteur ou de la
   fonction) ni celui de l'authentification ni un autre en-tête, à la casse près, et
   une clé passée en query (`auth.in: query`) n'est le nom d'aucun paramètre de
   query ; chaque clé de `call.encode` nomme un argument placé en query, en corps ou
   en en-tête ;
4. chaque contrôle (`checks`, `expect`) nomme un refus de sa fonction ; `equal_sums`
   porte sur un argument liste dont les éléments déclarent les deux champs ;
5. la sonde, et l'identité d'un `oauth2_user`, nomment une fonction de lecture du
   connecteur, appelée par `call`, sans argument requis ;
6. les réglages : un nom n'est déclaré qu'une fois parmi `credential`, `settings` et
   `from_token` ; le défaut d'une liste fermée en est une valeur ; un gabarit d'adresse
   (`base_url`, `token_url`, `authorize_url`) ne cite que des réglages déclarés (et,
   pour `base_url`, des valeurs de `from_token`), une adresse libre en tête seulement,
   un autre réglage après `https://` seulement ; `base_urls` et `token_urls` portent
   sur une liste fermée et donnent une adresse à chacune de ses valeurs, et rien
   d'autre ; tout réglage déclaré est cité.

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
    for argument in call.get("encode") or {}:
        if argument not in placed or placed[argument] == "the path":
            raise DescriptionError(
                f"{where}: function {name}: encode names {argument!r}, which is not placed in query, body or headers"
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


def _auth_header(auth: dict) -> str | None:
    if auth["kind"] == "api_key":
        return auth["name"] if auth["in"] == "header" else None
    if auth["kind"] in ("bearer", "basic", "oauth2_client_credentials", "oauth2_user"):
        return "Authorization"
    return None


def _auth_query(auth: dict) -> str | None:
    return auth["name"] if auth["kind"] == "api_key" and auth["in"] == "query" else None


def check_constants(connector: dict, function: dict, where: str) -> None:
    """Règle 3 : un nom côté API, une seule valeur."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    constants = call.get("constants") or {}
    for kind in ("query", "body"):
        for api_name in constants.get(kind) or {}:
            if api_name in (call.get(kind) or {}):
                raise DescriptionError(
                    f"{where}: function {name}: {kind} {api_name!r} is both a constant and an argument"
                )
    auth_query = _auth_query(connector["auth"])
    if auth_query and (auth_query in (call.get("query") or {}) or auth_query in (constants.get("query") or {})):
        raise DescriptionError(
            f"{where}: function {name}: query {auth_query!r} is set twice (the authentication and the function)"
        )
    seen: dict[str, str] = {}
    auth_header = _auth_header(connector["auth"])
    if auth_header:
        seen[auth_header.lower()] = "the authentication"
    sources = [
        ("the connector headers", connector.get("headers") or {}),
        ("the function constant headers", constants.get("headers") or {}),
        ("the function headers", call.get("headers") or {}),
    ]
    for source, headers in sources:
        for header in headers:
            if header.lower() in seen:
                raise DescriptionError(
                    f"{where}: function {name}: header {header!r} is set twice ({seen[header.lower()]} and {source})"
                )
            seen[header.lower()] = source


def check_checks(function: dict, where: str) -> None:
    """Règle 4 : un contrôle nomme un refus de sa fonction, et `equal_sums` une liste qui a ses deux champs."""
    name = function["name"]
    refusals = {refusal["code"] for refusal in function.get("refusals", [])}
    for check in function.get("checks", []) + function.get("expect", []):
        if check["refusal"] not in refusals:
            raise DescriptionError(
                f"{where}: function {name}: {check['kind']} names refusal {check['refusal']!r}, which the function does not declare"
            )
        if check["kind"] == "equal_sums":
            argument = function["input"]["properties"].get(check["items"])
            item = (argument or {}).get("items") if (argument or {}).get("type") == "array" else None
            missing = [f for f in check["fields"] if f not in ((item or {}).get("properties") or {})]
            if item is None or missing:
                raise DescriptionError(
                    f"{where}: function {name}: equal_sums needs {check['items']!r} to be a list argument whose items "
                    f"declare {', '.join(check['fields'])}"
                )


def _plain_read(data: dict, role: str, name: str, where: str) -> None:
    function = next((f for f in data["functions"] if f["name"] == name), None)
    if function is None:
        raise DescriptionError(f"{where}: {role} names {name!r}, which is not a function of the connector")
    if function["class"] != "read" or "call" not in function or function["input"].get("required"):
        raise DescriptionError(
            f"{where}: {role} {name!r} must be a read function with a call and no required argument"
        )


def check_probe(data: dict, where: str) -> None:
    """Règle 5 : la sonde et l'identité d'un consentement sont des lectures par `call`, appelables sans argument."""
    probe = data["connector"].get("probe")
    if probe is not None:
        _plain_read(data, "probe", probe["function"], where)
    identity = data["connector"]["auth"].get("identity")
    if identity is not None:
        _plain_read(data, "auth.identity", identity["function"], where)


def _check_template(template: str, at: str, settings: dict[str, dict], from_token: set[str], where: str) -> set[str]:
    """Un gabarit d'adresse : chaque `{nom}` est un réglage déclaré (ou une valeur du jeton), à sa place."""
    cited = PATH_PARAM.findall(template)
    for position, name in enumerate(cited):
        head = position == 0 and template.startswith("{")
        if name in from_token:
            continue
        setting = settings.get(name)
        if setting is None:
            known = sorted(settings) + sorted(from_token)
            raise DescriptionError(
                f"{where}: {at} cites {{{name}}}, which is not a declared setting ({', '.join(known) or 'none declared'})"
            )
        if head and setting["type"] != "url":
            raise DescriptionError(f"{where}: {at} starts with {{{name}}}, which is not a url setting")
        if not head and setting["type"] == "url":
            raise DescriptionError(f"{where}: {at} cites the url setting {{{name}}} elsewhere than at its head")
    return set(cited)


def _check_urls_by_setting(urls: dict, at: str, settings: dict[str, dict], where: str) -> set[str]:
    setting = settings.get(urls["setting"])
    if setting is None or setting["type"] != "choice":
        raise DescriptionError(f"{where}: {at}.setting names {urls['setting']!r}, which is not a choice setting")
    if set(urls["values"]) != set(setting["choices"]):
        raise DescriptionError(
            f"{where}: {at}.values must give a URL to each choice of {urls['setting']!r} "
            f"({', '.join(setting['choices'])}), and to nothing else"
        )
    return {urls["setting"]}


def check_settings(connector: dict, where: str) -> None:
    """Règle 6 : réglages déclarés une fois, cités à leur place, tous cités."""
    auth = connector["auth"]
    settings = {s["name"]: s for s in connector.get("settings", [])}
    from_token = set(auth.get("from_token", []))
    names = [f["name"] for f in connector.get("credential", [])] + [s["name"] for s in connector.get("settings", [])]
    names += list(from_token)
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: name(s) declared twice among credential, settings and from_token: {', '.join(twice)}")
    for setting in settings.values():
        if "default" in setting and setting["default"] not in setting["choices"]:
            raise DescriptionError(f"{where}: setting {setting['name']!r}: default {setting['default']!r} is not one of its choices")
    cited: set[str] = set()
    if "base_url" in connector:
        cited |= _check_template(connector["base_url"], "base_url", settings, from_token, where)
    if "base_urls" in connector:
        cited |= _check_urls_by_setting(connector["base_urls"], "base_urls", settings, where)
    for key in ("token_url", "authorize_url"):
        if key in auth:
            cited |= _check_template(auth[key], f"auth.{key}", settings, set(), where)
    if "token_urls" in auth:
        cited |= _check_urls_by_setting(auth["token_urls"], "auth.token_urls", settings, where)
    unused = sorted(set(settings) - cited)
    if unused:
        raise DescriptionError(f"{where}: setting(s) {', '.join(unused)} cited by no URL")


def check(data: dict, path: pathlib.Path) -> Description:
    where = _relative(path)
    if not isinstance(data, dict):
        raise DescriptionError(f"{where}: not a mapping")
    check_schema(data, where)
    if data["connector"]["name"] != path.parent.name:
        raise DescriptionError(f"{where}: connector.name {data['connector']['name']!r} is not the folder name")
    check_auth(data["connector"], where)
    check_settings(data["connector"], where)
    for function in data["functions"]:
        check_arguments(function, where)
        check_constants(data["connector"], function, where)
        check_checks(function, where)
    names = [function["name"] for function in data["functions"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: function name(s) declared twice: {', '.join(twice)}")
    check_probe(data, where)
    return Description(path=path, data=data)


def load(path: pathlib.Path) -> Description:
    return check(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def load_all(root: pathlib.Path = CONNECTORS) -> list[Description]:
    return [load(path) for path in sorted(root.glob("*/connector.yaml"))]
