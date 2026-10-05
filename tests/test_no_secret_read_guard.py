"""Garde mécanique : la lib ne lit aucun secret et n'a aucune surface de commande.

Le secret est toujours fourni par le consommateur (`oto.tools.common.credentials`).
Ce test refuse, dans tout fichier Python de `oto/` :
- un import de `oto.config` ou `oto.secrets` (résolution locale retirée), relatif ou absolu ;
- un import de `typer` (surfaces de commande retirées) ;
- une lecture `os.environ[...]`, `os.environ.get(...)` ou `os.getenv(...)` de tout nom
  absent de `ALLOWED_ENV` (réglages non secrets), et de tout nom calculé.

La garde vise l'axe (« lire l'environnement »), pas la forme d'un nom : un secret
s'appelle aussi `…_JSON`, `…_PASSWORD` ou `…_CREDENTIALS` (cas Slides,
`GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`, passé sous une garde par suffixe).
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "oto"
FORBIDDEN_MODULES = ("oto.config", "oto.secrets", "typer")
# Seules lectures d'environnement admises : des réglages qui ne sont pas des secrets.
ALLOWED_ENV = frozenset({"OTO_API_URL", "LINKEDIN_NO_RATE_LIMIT"})
DYNAMIC = "<nom calculé>"


def _module_of(path: pathlib.Path, root: pathlib.Path) -> list[str]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _resolve(path: pathlib.Path, root: pathlib.Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = _module_of(path, root)
    if path.name != "__init__.py":
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _forbidden(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ")


def _env_name(node: ast.AST):
    """Nom lu dans l'environnement par ce nœud (`DYNAMIC` s'il n'est pas littéral)."""
    arg = None
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        arg = node.slice
    elif isinstance(node, ast.Call) and node.args:
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value):
            arg = node.args[0]
        elif (isinstance(f, ast.Attribute) and f.attr == "getenv") or (
                isinstance(f, ast.Name) and f.id == "getenv"):
            arg = node.args[0]
    if arg is None:
        return None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return DYNAMIC


def _violations(path: pathlib.Path, root: pathlib.Path = ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"import {a.name}" for a in node.names if _forbidden(a.name)]
        elif isinstance(node, ast.ImportFrom):
            module = _resolve(path, root, node)
            if _forbidden(module) or any(
                    _forbidden(f"{module}.{a.name}") for a in node.names if module == "oto"):
                out.append(f"from {module} import …")
        name = _env_name(node)
        if name and name not in ALLOWED_ENV:
            out.append(f"lecture d'environnement {name}")
    return [f"{path.relative_to(root)}:{v}" for v in out]


def test_aucun_module_ne_lit_de_secret_ni_n_importe_une_surface_de_commande():
    violations = [v for p in sorted(PKG.rglob("*.py")) for v in _violations(p)]
    assert not violations, "\n".join(violations)


def test_la_garde_voit_ce_qu_elle_doit_refuser(tmp_path):
    """Preuve que la garde mord : chaque motif interdit est détecté."""
    pkg = tmp_path / "oto" / "tools" / "x"
    pkg.mkdir(parents=True)
    bad = pkg / "client.py"
    bad.write_text(
        "import os\n"
        "import typer\n"
        "from ...config import require_secret\n"
        "from oto.secrets import make_provider\n"
        "from oto import config\n"
        "a = os.environ.get('SERPER_API_KEY')\n"
        "b = os.getenv('SLACK_BOT_TOKEN')\n"
        "c = os.environ['ZOHO_CLIENT_SECRET']\n"
        "d = os.environ.get('OTO_API_URL')\n"
        "e = os.getenv('GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON')\n"
        "f = os.environ.get(name)\n",
        encoding="utf-8",
    )
    joined = "\n".join(_violations(bad, tmp_path))
    for attendu in ("import typer", "from oto.config import", "from oto.secrets import",
                    "from oto import", "SERPER_API_KEY", "SLACK_BOT_TOKEN", "ZOHO_CLIENT_SECRET",
                    "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", DYNAMIC):
        assert attendu in joined, (attendu, joined)
    assert "OTO_API_URL" not in joined
