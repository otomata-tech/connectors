# Packaging — ce que publie oto-core, et ce qui le borne

Extrait de `CLAUDE.md`, qui n'en garde que les règles. Cette page porte le pourquoi et les mesures.

## `uv.lock` est commité et ne gouverne AUCUNE install

Les deps sont déclarées en **plancher** (`>=`) : le graphe de dépendances GitHub ne peut alors
attribuer aucune version aux paquets du `pyproject.toml`, donc **aucune alerte de sécurité ne peut
se déclencher** — zéro depuis la création du dépôt, alors que des libs qu'il consomme portent des
avis publiés.

Le lock donne au graphe des versions exactes, rien de plus :

- il est **absent de la wheel ET du sdist** (donc invisible à `pip install oto-core`) ;
- un consommateur `uv` **ignore le lock de sa dépendance** (mesuré, y compris sur une dép `path`) —
  oto-backend et oto-cli continuent de résoudre oto-core depuis son `pyproject.toml`, à l'identique.

Le régénérer par `uv lock` quand une dépendance bouge.

⚠️ **Monter un plancher reste un geste séparé** : ce fichier rend le dépôt observable, il ne le
répare pas.

## `MANIFEST.in` borne l'ARCHIVE SOURCE (sdist)

`[tool.setuptools.packages.find]` ne gouverne que la **roue**. Le sdist, lui, est composé par les
défauts de setuptools, qui y versaient `tests/` **en entier** — 419 fichiers publiés contre 334 dans
la roue, et six fixtures y nommaient un tiers. **Une fixture est une surface publiée** tant que le
sdist n'est pas borné (corrigé à partir de 1.122.0).

Toute reprise du packaging se vérifie **sur le tarball**, jamais sur le pyproject :

```bash
python -m build --sdist && tar tzf dist/*.tar.gz
```

Il ne doit en sortir que `oto/`, ses `package-data` et les métadonnées.

⚠️ Les data files runtime (`sirene/data`, `pdf/templates`) sont déclarés en `package-data` — tout
nouveau fichier chargé via `Path(__file__)` doit y être ajouté, sinon la wheel casse.
