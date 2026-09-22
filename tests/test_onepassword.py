"""Contrat de `OnePasswordProvider` : mêmes garanties que `SopsProvider`, sans
jamais toucher au vrai `op` (aucun appel réseau/1Password dans ce fichier).
"""
import subprocess

import pytest

from oto.secrets import MISSING, STORE_ABSENT, AmbiguousSecretError
from oto.secrets.onepassword import OnePasswordError, OnePasswordProvider, invalidate_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _write_table(tmp_path, cles=None, ambigues=None):
    import yaml
    path = tmp_path / "secrets.1password.yaml"
    data = {}
    if cles is not None:
        data["cles"] = cles
    if ambigues is not None:
        data["ambigues"] = ambigues
    path.write_text(yaml.safe_dump(data))
    return path


def _provider(path):
    return OnePasswordProvider({"onepassword_file": str(path)})


def test_store_absent_when_file_missing(tmp_path):
    provider = _provider(tmp_path / "nope.yaml")
    assert provider.lookup("X") is STORE_ABSENT
    assert provider.store_exists() is False


def test_store_exists_when_file_present(tmp_path):
    path = _write_table(tmp_path, cles={"X": "op://Otomata/x/field"})
    assert _provider(path).store_exists() is True


def test_missing_key_not_in_table(tmp_path):
    path = _write_table(tmp_path, cles={"X": "op://Otomata/x/field"})
    assert _provider(path).lookup("Y") is MISSING


def test_ambiguous_key_raises(tmp_path):
    path = _write_table(
        tmp_path,
        cles={},
        ambigues={"DATABASE_URL": ["op://Otomata/mm-db/url", "op://Otomata/tulina-db/url"]},
    )
    with pytest.raises(AmbiguousSecretError):
        _provider(path).lookup("DATABASE_URL")


def test_resolves_via_op_read(tmp_path, monkeypatch):
    path = _write_table(tmp_path, cles={"SLACK_BOT_TOKEN": "op://Otomata/slack/token"})

    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert cmd == ["op", "read", "--no-newline", "op://Otomata/slack/token"]
        return subprocess.CompletedProcess(cmd, 0, stdout="xoxb-fake", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    provider = _provider(path)
    assert provider.lookup("SLACK_BOT_TOKEN") == "xoxb-fake"
    # Second lookup: cached, no second `op` call.
    assert provider.lookup("SLACK_BOT_TOKEN") == "xoxb-fake"
    assert len(calls) == 1


def test_op_read_never_leaves_a_file_on_disk(tmp_path, monkeypatch):
    """La résolution ne doit jamais matérialiser le secret ailleurs qu'en mémoire."""
    path = _write_table(tmp_path, cles={"X": "op://Otomata/x/field"})
    before = set(tmp_path.iterdir())

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="secret-value", stderr=""),
    )
    _provider(path).lookup("X")

    after = set(tmp_path.iterdir())
    assert after == before, "aucun fichier ne doit apparaître pendant la résolution"


def test_op_cli_absent_raises_explicit_error(tmp_path, monkeypatch):
    path = _write_table(tmp_path, cles={"X": "op://Otomata/x/field"})

    def _missing(cmd, **kwargs):
        raise FileNotFoundError("op")

    monkeypatch.setattr(subprocess, "run", _missing)
    with pytest.raises(OnePasswordError, match="op` introuvable"):
        _provider(path).lookup("X")


def test_op_refuses_raises_explicit_error(tmp_path, monkeypatch):
    path = _write_table(tmp_path, cles={"X": "op://Otomata/x/field"})

    def _denied(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="[ERROR] authorization prompt dismissed"
        )

    monkeypatch.setattr(subprocess, "run", _denied)
    with pytest.raises(OnePasswordError, match="authorization prompt dismissed"):
        _provider(path).lookup("X")


def test_dotted_notation_key(tmp_path, monkeypatch):
    """Les groupes SOPS imbriqués (`compta: {mm_admin2_login: ...}`) sont
    aplatis en notation pointée côté 1Password : `compta.mm_admin2_login`
    est une clé plate comme une autre, pas un dict."""
    path = _write_table(tmp_path, cles={"compta.mm_admin2_login": "op://Otomata/compta/mm-admin2"})
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="login-value", stderr=""),
    )
    assert _provider(path).lookup("compta.mm_admin2_login") == "login-value"
    assert _provider(path).lookup("compta") is MISSING


def test_provider_registered_in_factory(tmp_path):
    from oto.secrets import make_provider
    provider = make_provider("onepassword", {"onepassword_file": str(tmp_path / "nope.yaml")})
    assert isinstance(provider, OnePasswordProvider)
