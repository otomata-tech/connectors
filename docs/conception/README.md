# Conception

Les documents de conception de ce dépôt : pour chaque sujet, **comment** c'est construit et **pourquoi**, avec les alternatives écartées. Ils couvrent les connecteurs partagés d'oto 2 : leur fichier de description et la fabrique qui en tire Python et TypeScript. Ce qui se passe chez l'hôte qui exécute un connecteur (catalogue, comptes, secret fourni à l'appel, coffre) vit dans oto-pkg : docs/conception/connecteurs-et-comptes.md.

**Pour un agent qui code dans ce dépôt** : lire le document du sujet avant de toucher `connectors/` ou la fabrique. Une demande qui contredit un document se signale ; elle ne s'implémente pas en silence.

## Règle de vie

- Un document par sujet, qui répond à une question avec laquelle le lecteur arrive ; 200 lignes au plus. Une décision proche d'un sujet existant enrichit son document ; un sujet nouveau s'ajoute à la table ci-dessous dans le même commit.
- En tête : le statut (`proposé`, ou `validé avec X le JJ/MM/AAAA`) et la date de dernière révision.
- Neuf sections, dans cet ordre et jamais vides : Résumé ; Contexte ; Objectifs et non-objectifs ; Conception ; Décisions et alternatives écartées ; Sécurité et confidentialité ; Écart avec le code ; Questions ouvertes ; Historique.
- Le corps décrit l'état présent et se corrige en place ; une option abandonnée passe dans « Décisions et alternatives écartées », avec sa raison. « Écart avec le code » se tient à jour avec le code, dans le même commit.
- Historique : une ligne par révision de fond, `AAAA-MM-JJ : ce qui change — décidé par <rôle> (source : …)`.
- Dépôt public : aucun nom de client, de partenaire ni de personne ; « décidé par » nomme un rôle. Aucun numéro d'issue, de PR ni d'URL GitHub.

« Proposé » : guide le code mais peut encore changer. « Validé » : décidé à la date dite, et réalisé au moins en partie.

## Les documents

| Document | La question à laquelle il répond | Statut |
|---|---|---|
| [Format de description](format-de-description.md) | Comment décrit-on un connecteur partagé, et que doit contenir son fichier ? | validé avec Alexis le 29/09/2026 |
| [La fabrique](fabrique.md) | Comment un fichier de description devient-il des définitions TypeScript que le paquet d'oto 2 adapte, puis un client Python ? | proposé |

## Anciens identifiants → document

| Ancien identifiant | Document |
|---|---|
| Conception connecteurs d'oto 2 (oto-enterprise, archivé) : « Le format de connecteur », « Le fichier de description », « Faits du relevé de la lib Python », lot 2 | [Format de description](format-de-description.md) |
| Conception connecteurs d'oto 2 (oto-enterprise, archivé) : « Ce que la fabrique génère », « Périmètre, sortie TypeScript, secrets », « Deux sortes de connecteurs », « Bout 1 » | [La fabrique](fabrique.md) ; le contrat côté hôte : oto-pkg : docs/conception/connecteurs-et-comptes.md |
| ADR 0070 §7.8 (oto-enterprise, archivé) : un connecteur activé, ses outils ou `oto_call` | [Format de description](format-de-description.md), « Décisions et alternatives écartées » |
| ADR 0070 §7.4 (a) et §7.9 (oto-enterprise, archivé) : la lib reste un paquet à part, la cible est un deuxième oto | oto-saas : docs/conception/cible-et-depots.md |
