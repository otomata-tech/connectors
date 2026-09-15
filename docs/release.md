# Release — PyPI, tags, et le pin d'oto-backend

Sur PyPI depuis 1.6.0 (promesse ADR 0005).

## Le geste

1. Bumper `version` dans `pyproject.toml` — **seul endroit** (pas d'`oto/__init__.py`).
2. Tag `vX.Y.Z` → **publication PyPI automatique** par `.github/workflows/publish.yml`
   (trusted publishing OIDC, aucun jeton stocké). Jamais de `twine` à la main.
3. Bumper le pin `oto-core@vX.Y.Z` dans oto-backend (il pin **par tag git**).

Un tag poussé depuis un poste est déclenché par le push ; un tag posé par `tag-release.yml` est
dispatché par lui (un tag du `GITHUB_TOKEN` ne déclenche aucun événement `push`).
Rejouer une version : `gh workflow run publish.yml --ref main -f tag=vX.Y.Z`.

## Les gardes et les pièges

- Le job **refuse un tag dont la version ne se retrouve pas** dans le pyproject, le sdist et la roue.
- ⚠️ **Toujours bumper `version` AVEC le tag.** Un tag `vX.Y.Z` posé sans bumper le champ fait mentir
  `pip show oto-core` : la prod affiche l'ancienne version malgré le bon code, et on part en fausse
  piste « bump non appliqué ».
- ⚠️ PyPI reconnaît le workflow par son **nom de fichier** : le renommer coupe la publication.
- Les installs **editable** (box, oto-cli local) ne sont PAS affectées par un publish — `git pull`
  reste requis.
