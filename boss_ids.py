"""Templates des boss de DONJON du serveur Nexus.

Source : table `boss_definitions` (boss_kind='BOSS', is_enabled=1) du dump
game.sql — la liste faisant autorite cote serveur. Les WORLD_BOSS en sont
exclus : on ne veut que les boss de donjon. En combat, le paquet GM donne le
template du monstre (getPacketsName renvoie le template id pour un mob), donc
on reconnait un boss sans ambiguite (voir CombatAI._read_fight_gm).

Regenerer : SELECT monster_id, display_name FROM boss_definitions
WHERE boss_kind='BOSS' AND is_enabled=1 ORDER BY monster_id.
"""

DUNGEON_BOSS_IDS = frozenset({
    58,     # Gelée Royale Bleue
    85,     # Gelée Royale Menthe
    86,     # Gelée Royale Fraise
    107,    # Dark Vlad
    113,    # Dragon Cochon
    121,    # Minotoror
    173,    # Abraknyde Ancestral
    180,    # Wa Wabbit
    226,    # Moon
    230,    # Le Chouque
    232,    # Meulou
    257,    # Chêne Mou
    382,    # Tofu Royal
    423,    # Kralamoure Géant
    430,    # Gelée Royale Citron
    457,    # Shin Larve
    519,    # Bulbig
    568,    # Tanukouï San
    605,    # Péki Péki
    612,    # Maître Pandore
    649,    # Coffre Sombre
    669,    # Craqueleur Légendaire
    670,    # Koulosse
    792,    # Bworkette
    797,    # Scarabosse Doré
    799,    # Tournesol Affame
    800,    # Batofu
    827,    # Minotot
    854,    # Crocabulia
    928,    # Mob l Eponge
    929,    # Péki Garou
    939,    # Rat Noir
    940,    # Rat Blanc
    943,    # Sphincter Cell
    1001,   # Milimilou
    1015,   # Wa Wabbit (variante)
    1027,   # Corailleur Magistral
    1051,   # Gourlo le Terrible
    1085,   # Tynril Déconcerté
    1086,   # Tynril Perfide
    1087,   # Tynril Ahuri
    1159,   # Ougah
    1170,   # Ilyzaelle
    1184,   # Blop Coco Royal
    1185,   # Blop Griotte Royal
    1186,   # Blop Indigo Royal
    1187,   # Blop Reinette Royal
    1188,   # Blop Multicolore Royal
    1195,   # Qu Tan
    2611,   # Minotoror (variante)
    31001,  # Royalmouth
    31006,  # Mansot Royal
    31012,  # Ben le Ripate
    31019,  # Obsidiantre
    31025,  # Tengu Givrefoux
    31026,  # Fuji Givrefoux Nourriciere
    31032,  # Korriandre
    31038,  # Kolosso
    31039,  # Professeur Xa
    31046,  # Glourseleste
    31053,  # Sylargh
    31059,  # Klime
    31065,  # Missiz Frizz
    31071,  # Nileza
    31077,  # Comte Harebourg
    31083,  # Ombre
    31104,  # Chafer d
    31190,  # Le Juge du Destin
})
