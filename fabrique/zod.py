"""Traduction d'un JSON Schema d'entrée en expression `zod` (import `zod/v4`).

Ne traduit que ce que `zod` dit sans raffinement (`refine`), pour que le schéma servi
et le schéma validé restent le même : types (listes avec `null` comprises), `enum`,
`const` (réduits aux valeurs que les contraintes voisines admettent), bornes de chaîne, de nombre et de liste, `pattern`, objets stricts ou
ouverts, `anyOf`, et `oneOf` quand ses branches s'excluent à coup sûr. Les
annotations (`description`, `default`, `format`, `contentMediaType`, `title`,
`examples`, `deprecated`) passent par `.meta()` sans rien valider : en JSON Schema
2020-12, `format` n'est qu'une annotation, et c'est ce que le test des descriptions
applique.

Tout le reste lève `Untranslatable`, qui dit le mot-clé et son emplacement : la
fonction n'est pas générée plutôt que de l'être avec un schéma faux.
"""
from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

ANNOTATIONS = ("title", "description", "default", "examples", "format", "contentMediaType", "deprecated")
IGNORED = ("$comment",)
STRING_KEYS = ("minLength", "maxLength", "pattern")
NUMBER_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")
ARRAY_KEYS = ("items", "minItems", "maxItems", "uniqueItems")
OBJECT_KEYS = ("properties", "required", "additionalProperties")
VALUE_KEYS = ("enum", "const")
COMBINATORS = ("oneOf", "anyOf")
TYPES = ("string", "integer", "number", "boolean", "null", "array", "object")
KNOWN = set(ANNOTATIONS + IGNORED + STRING_KEYS + NUMBER_KEYS + ARRAY_KEYS + OBJECT_KEYS + VALUE_KEYS + COMBINATORS + ("type",))


class Untranslatable(ValueError):
    """Un JSON Schema que la fabrique ne sait pas dire en `zod` sans le fausser."""


def js(value: Any) -> str:
    """Un littéral JavaScript : JSON en est un sous-ensemble."""
    return json.dumps(value, ensure_ascii=False)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _types_of(schema: dict) -> list[str]:
    declared = schema.get("type")
    if declared is None:
        return []
    return [declared] if isinstance(declared, str) else list(declared)


def _values(values: list[Any], at: str) -> str:
    """`enum`/`const` : des valeurs primitives seulement."""
    if any(isinstance(v, (dict, list)) for v in values):
        raise Untranslatable(f"{at}: enum or const with an object or array value")
    plain = [v for v in values if v is not None]
    parts = []
    if plain:
        if all(isinstance(v, str) for v in plain) and len(plain) > 1:
            parts.append(f"z.enum({js(plain)})")
        elif len(plain) == 1:
            parts.append(f"z.literal({js(plain[0])})")
        else:
            parts.append(f"z.literal({js(plain)})")
    if not values:
        return "z.never()"
    if None in values:
        return f"{parts[0]}.nullable()" if parts else "z.null()"
    return parts[0]


def _bounds(schema: dict, keys: dict[str, str]) -> str:
    return "".join(f".{method}({js(schema[key])})" for key, method in keys.items() if key in schema)


def _string(schema: dict) -> str:
    out = "z.string()" + _bounds(schema, {"minLength": "min", "maxLength": "max"})
    if "pattern" in schema:
        out += f".regex(new RegExp({js(schema['pattern'])}))"
    return out


def _number(schema: dict, kind: str) -> str:
    base = "z.int()" if kind == "integer" else "z.number()"
    return base + _bounds(schema, {
        "minimum": "gte", "maximum": "lte", "exclusiveMinimum": "gt", "exclusiveMaximum": "lt", "multipleOf": "multipleOf",
    })


def _array(schema: dict, at: str) -> str:
    if schema.get("uniqueItems") is True:
        raise Untranslatable(f"{at}: uniqueItems needs a refinement")
    items = schema.get("items", {})
    if not isinstance(items, dict):
        raise Untranslatable(f"{at}: items is not a schema")
    return f"z.array({translate(items, at + '[]')})" + _bounds(schema, {"minItems": "min", "maxItems": "max"})


def _object(schema: dict, at: str, indent: int) -> str:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [key for key in required if key not in properties]
    if missing:
        raise Untranslatable(f"{at}: required key(s) {', '.join(missing)} not declared in properties")
    extra = schema.get("additionalProperties", True)
    if not properties and isinstance(extra, dict):
        return f"z.record(z.string(), {translate(extra, at + '.*', indent)})"
    pad = "  " * (indent + 1)
    lines = []
    for key, sub in properties.items():
        if not isinstance(sub, dict):
            raise Untranslatable(f"{at}.{key}: boolean schema")
        expression = translate(sub, f"{at}.{key}", indent + 1)
        if key not in required:
            expression += ".optional()"
        lines.append(f"{pad}{js(key)}: {expression},")
    shape = "{\n" + "\n".join(lines) + "\n" + "  " * indent + "}" if lines else "{}"
    if extra is False:
        return f"z.strictObject({shape})"
    if extra is True:
        return f"z.looseObject({shape})"
    return f"z.object({shape}).catchall({translate(extra, at + '.*', indent)})"


def _typed(schema: dict, kind: str, at: str, indent: int) -> str:
    if kind == "string":
        return _string(schema)
    if kind in ("integer", "number"):
        return _number(schema, kind)
    if kind == "boolean":
        return "z.boolean()"
    if kind == "null":
        return "z.null()"
    if kind == "array":
        return _array(schema, at)
    if kind == "object":
        return _object(schema, at, indent)
    raise Untranslatable(f"{at}: unknown type {kind!r}")


def _accepted(schema: dict) -> tuple[set[str], list[Any] | None]:
    """Ce qu'une branche peut accepter : ses types JSON, et ses valeurs si elle les énumère."""
    if "const" in schema:
        return {_json_type(schema["const"])}, [schema["const"]]
    if "enum" in schema:
        return {_json_type(v) for v in schema["enum"]}, list(schema["enum"])
    types = set(_types_of(schema))
    if "integer" in types:
        types.add("number")
    if "number" in types:
        types.add("integer")
    return (types or set(TYPES)), None


def _const_of(schema: dict) -> list[Any] | None:
    if "const" in schema:
        return [schema["const"]]
    if "enum" in schema:
        return list(schema["enum"])
    return None


def _objects_exclusive(a: dict, b: dict) -> bool:
    """Deux objets s'excluent : l'un exige une clé que l'autre, strict, interdit ; ou une clé exigée des deux côtés
    n'admet pas les mêmes valeurs."""
    for one, other in ((a, b), (b, a)):
        if other.get("additionalProperties") is False:
            if set(one.get("required", [])) - set(other.get("properties", {})):
                return True
    shared = set(a.get("required", [])) & set(b.get("required", []))
    for key in shared:
        left = _const_of(a.get("properties", {}).get(key, {}))
        right = _const_of(b.get("properties", {}).get(key, {}))
        if left is not None and right is not None and not any(v in right for v in left):
            return True
    return False


def _exclusive(a: dict, b: dict) -> bool:
    types_a, values_a = _accepted(a)
    types_b, values_b = _accepted(b)
    if values_a == [] or values_b == []:
        return True
    if values_a is not None and values_b is not None:
        return not any(v in values_b for v in values_a)
    if not types_a & types_b:
        return True
    if types_a == {"object"} == types_b:
        return _objects_exclusive(a, b)
    return False


def _combinator(schema: dict, key: str, at: str, indent: int) -> str:
    branches = schema[key]
    if not isinstance(branches, list) or not branches or not all(isinstance(b, dict) for b in branches):
        raise Untranslatable(f"{at}: {key} without schemas")
    if key == "oneOf":
        for i, a in enumerate(branches):
            for b in branches[i + 1:]:
                if not _exclusive(a, b):
                    raise Untranslatable(f"{at}: oneOf with branches that may overlap")
    parts = [translate(branch, f"{at}/{key}{i}", indent) for i, branch in enumerate(branches)]
    return parts[0] if len(parts) == 1 else f"z.union([{', '.join(parts)}])"


def _meta(schema: dict) -> str:
    meta = {key: schema[key] for key in ANNOTATIONS if key in schema}
    if not meta:
        return ""
    if list(meta) == ["description"]:
        return f".describe({js(meta['description'])})"
    return f".meta({js(meta)})"


def translate(schema: Any, at: str = "input", indent: int = 0) -> str:
    """L'expression `zod` d'un JSON Schema, ou `Untranslatable`."""
    if not isinstance(schema, dict):
        raise Untranslatable(f"{at}: boolean schema")
    unknown = sorted(set(schema) - KNOWN)
    if unknown:
        raise Untranslatable(f"{at}: {', '.join(unknown)} needs a refinement")
    types = _types_of(schema)
    for kind in types:
        if kind not in TYPES:
            raise Untranslatable(f"{at}: unknown type {kind!r}")
    assertions = set(schema) - set(ANNOTATIONS) - set(IGNORED) - {"type"}
    combinator = [key for key in COMBINATORS if key in schema]

    if combinator:
        if len(combinator) > 1 or assertions - set(combinator) or types:
            raise Untranslatable(f"{at}: {combinator[0]} with sibling constraints")
        return _combinator(schema, combinator[0], at, indent) + _meta(schema)

    if "enum" in schema or "const" in schema:
        if "enum" in schema and "const" in schema:
            raise Untranslatable(f"{at}: enum and const together")
        values = [schema["const"]] if "const" in schema else list(schema["enum"])
        # Les autres contraintes ne font que retirer des valeurs à l'énumération : on les applique ici.
        rest = Draft202012Validator({k: v for k, v in schema.items() if k not in VALUE_KEYS})
        return _values([v for v in values if rest.is_valid(v)], at) + _meta(schema)

    if not types:
        if assertions:
            raise Untranslatable(f"{at}: constraints without a type")
        return "z.unknown()" + _meta(schema)

    nullable = "null" in types and len(types) > 1
    kinds = [kind for kind in types if not (nullable and kind == "null")]
    if "integer" in kinds and "number" in kinds:
        kinds.remove("integer")
    parts = [_typed(schema, kind, at, indent) for kind in kinds]
    expression = parts[0] if len(parts) == 1 else f"z.union([{', '.join(parts)}])"
    if nullable:
        expression += ".nullable()"
    return expression + _meta(schema)


def translate_input(schema: dict, at: str) -> str:
    """L'entrée d'une fonction : un objet strict, sans contrainte au niveau de l'objet hors de ses propriétés."""
    extra = sorted(set(schema) - {"type", "additionalProperties", "properties", "required", *ANNOTATIONS, *IGNORED})
    if extra:
        raise Untranslatable(f"{at}: {', '.join(extra)} at the root needs a refinement")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise Untranslatable(f"{at}: the input is not a strict object")
    return translate(schema, at)
