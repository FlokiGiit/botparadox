# Mettre en place les mises à jour automatiques (GitHub)

À faire **une seule fois**. Ensuite chaque nouvelle version se publie en une commande.

## 0. Choisir le dépôt
Le dépôt (ou au moins ses *releases*) doit être **PUBLIC** : c'est ce qui permet
au bot des amis de télécharger la mise à jour sans coller de mot de passe/token
dans l'exe. Le code source y sera visible.

Renseigne `release_config.json` :
```json
{ "repo": "TON-PSEUDO/botparadox", "version": "1.0.0" }
```

## 1. Connexion GitHub (interactif)
```
gh auth login
```
(choisir GitHub.com → HTTPS → se connecter dans le navigateur)

## 2. Créer le dépôt public et pousser le code
Depuis le dossier du projet :
```
git init
git add .
git commit -m "Bot Paradox"
gh repo create TON-PSEUDO/botparadox --public --source=. --remote=origin --push
```

## 3. Publier la première version
```
python publish_release.py
```
Ça build l'installateur et crée la release `v1.0.0` avec `BotParadox-Setup.exe`.

## Pour livrer une nouvelle version plus tard
1. incrémente `"version"` dans `release_config.json` (ex. `1.0.1`)
2. `python publish_release.py`

Les amis déjà équipés verront « Mettre à jour » au prochain lancement de
Bot Paradox : un clic télécharge et installe la nouvelle version.

> La toute première fois, tu envoies quand même `BotParadox-Setup.exe` à la main
> à tes amis (celui de `Publish\`). C'est le seul envoi manuel : après, tout est
> automatique.
