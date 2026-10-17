"""Groupes d'utilisateurs Slack (`usergroups.*`) — ce que le client ENVOIE.

Le contrat est celui de la référence Slack : listes (`users`, `channels`) en chaînes
séparées par des virgules, envoyées en formulaire ; `usergroups.users.update`
REMPLACE toute la liste et refuse une liste vide (on vide un groupe en le
désactivant). Lecture → bot par défaut ; écriture → `default_as_user` du client,
comme `post_message`.
"""
import pytest

from oto.tools.slack.client import SlackClient, SlackError


def _espion(client, reponse=None):
    appels = []

    def fake(method, endpoint, as_user=None, **kwargs):
        appels.append({"method": method, "endpoint": endpoint, "as_user": as_user, **kwargs})
        return reponse or {"ok": True, "usergroups": [{"id": "S1"}], "users": ["U1", "U2"],
                           "usergroup": {"id": "S1"}}

    client._request = fake
    return appels


def test_lecture_par_le_bot_et_drapeaux_en_texte():
    c = SlackClient(bot_token="xoxb-1", user_token="xoxp-1", default_as_user=True)
    appels = _espion(c)

    assert c.list_usergroups(include_users=True, include_disabled=True) == [{"id": "S1"}]
    assert appels[-1]["endpoint"] == "usergroups.list"
    assert appels[-1]["as_user"] is False                   # lecture → bot
    # Slack lit un booléen de query string comme du TEXTE.
    assert appels[-1]["params"] == {"include_users": "true", "include_disabled": "true"}

    assert c.usergroup_users("S1") == ["U1", "U2"]
    assert appels[-1]["endpoint"] == "usergroups.users.list"
    assert appels[-1]["params"] == {"usergroup": "S1"}


def test_lecture_sans_bot_retombe_sur_le_user_token():
    c = SlackClient(user_token="xoxp-1")
    appels = _espion(c)
    c.list_usergroups()
    assert appels[-1]["as_user"] is True
    assert appels[-1]["params"] == {}


def test_creation_n_envoie_que_ce_qui_est_donne_et_joint_les_canaux():
    c = SlackClient(bot_token="xoxb-1")
    appels = _espion(c)
    c.create_usergroup("Ventes", handle="ventes", channels=["C1", "C2"])
    a = appels[-1]
    assert (a["method"], a["endpoint"]) == ("POST", "usergroups.create")
    assert a["data"] == {"name": "Ventes", "handle": "ventes", "channels": "C1,C2"}
    assert "json" not in a and "params" not in a


def test_mise_a_jour_partielle():
    c = SlackClient(bot_token="xoxb-1")
    appels = _espion(c)
    c.update_usergroup("S1", description="")
    # Une chaîne vide est une valeur DONNÉE (effacer la description), pas une absence.
    assert appels[-1]["data"] == {"usergroup": "S1", "description": ""}


def test_membres_remplaces_en_une_chaine_et_jeton_choisi():
    c = SlackClient(bot_token="xoxb-1", user_token="xoxp-1")
    appels = _espion(c)
    c.set_usergroup_users("S1", ["U1", " U2 "], as_user=True)
    a = appels[-1]
    assert a["endpoint"] == "usergroups.users.update"
    assert a["data"] == {"usergroup": "S1", "users": "U1,U2"}
    assert a["as_user"] is True


@pytest.mark.parametrize("vide", [[], [""], ""])
def test_zero_membre_refuse_avant_l_appel(vide):
    c = SlackClient(bot_token="xoxb-1")
    appels = _espion(c)
    with pytest.raises(ValueError, match="disable"):
        c.set_usergroup_users("S1", vide)
    assert appels == []


def test_activer_desactiver():
    c = SlackClient(bot_token="xoxb-1")
    appels = _espion(c)
    c.disable_usergroup("S1")
    c.enable_usergroup("S1")
    assert [a["endpoint"] for a in appels] == ["usergroups.disable", "usergroups.enable"]
    assert all(a["data"] == {"usergroup": "S1"} for a in appels)


@pytest.mark.parametrize("code,status", [
    ("no_such_subteam", 404), ("permission_denied", 403),
    ("plan_upgrade_required", 403), ("paid_teams_only", 403),
    ("name_already_exists", 409), ("handle_already_exists", 409),
])
def test_codes_de_refus_types(code, status):
    assert SlackError(code).status == status
