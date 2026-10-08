"""Scopes Microsoft — la lecture du `scope` rendu par Entra, pour qu'un consommateur
teste `short(FILES) <= normalize(grant.scope)` quelle que soit la forme rendue."""
from oto.tools.microsoft import scopes


def test_formes_courtes_uri_et_casse_confondues():
    granted = scopes.normalize(
        "openid profile email offline_access https://graph.microsoft.com/files.readwrite.all "
        "SITES.READWRITE.ALL User.Read  HTTPS://GRAPH.MICROSOFT.COM/Mail.Send")
    assert granted == {"openid", "profile", "email", "offline_access", "Files.ReadWrite.All",
                       "Sites.ReadWrite.All", "User.Read", "Mail.Send"}
    assert scopes.short(scopes.FILES) <= granted
    assert scopes.short(scopes.IDENTITY) <= granted
    assert not scopes.short(scopes.MAIL) <= granted


def test_short_de_nos_constantes():
    assert scopes.short(scopes.TEAMS) == {"Team.ReadBasic.All", "Channel.ReadBasic.All",
                                          "ChannelMessage.Send", "Chat.ReadWrite"}
    assert scopes.short(scopes.REFRESH) == {".default", "offline_access"}


def test_scope_inconnu_garde_tel_que_rendu_sans_prefixe():
    assert scopes.normalize("https://graph.microsoft.com/Tasks.ReadWrite") == {"Tasks.ReadWrite"}


def test_scope_vide():
    assert scopes.normalize("") == frozenset() and scopes.normalize(None) == frozenset()


def test_admin_a_part():
    every_user = (scopes.IDENTITY + scopes.FILES + scopes.MAIL + scopes.CALENDAR
                  + scopes.TEAMS)
    assert not set(scopes.TEAMS_ADMIN) & set(every_user)
