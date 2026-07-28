Bot Paradox — overlay & assistant pour Nexus (Dofus Retro)
==========================================================

INSTALLATION
------------
1. Ferme le jeu Nexus s'il est ouvert.
2. Double-clique sur BotParadox-Setup.exe et laisse-toi guider.
   - Si le client n'est pas trouvé tout seul, une fenêtre te demandera
     où est installé ton launcher Nexus : sélectionne le dossier.
3. Un raccourci "Bot Paradox" apparaît sur ton Bureau.

Rien d'autre à installer : ni Python, ni .NET. Tout est inclus.

UTILISATION
-----------
1. Lance "Bot Paradox" (le raccourci du Bureau).
2. Clique "Démarrer" : ça lance le moteur en fond.
3. Lance Nexus et connecte-toi normalement.
   L'overlay (loot, fusion, stats) s'affiche sur le côté droit du jeu.
4. Dans la fenêtre Bot Paradox : compteurs, journal, et les modes
   Observer / Harvest / Farming.

EN CAS DE SOUCI
---------------
- Overlay absent ? Ferme le jeu, clique "Démarrer" dans Bot Paradox,
  puis relance le jeu (les patchs s'appliquent au démarrage du moteur).
- Diagnostic : ouvre une invite de commande dans
  %LOCALAPPDATA%\BotParadox et lance :  botcore\botcore.exe --selftest
- Le client n'est pas trouvé ? Crée le fichier
  %LOCALAPPDATA%\BotParadox\botcore\data\client_override.txt
  contenant le chemin de ton dossier ...\srv_nexus\resources\app

Après une mise à jour du launcher Nexus, relance simplement "Démarrer" :
les patchs se réappliquent automatiquement.
