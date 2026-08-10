"""Interface minimale d'un script de combat (checkbox + template)."""


class FightScript:
    """Règles boss qui complètent l'IA farming sans la remplacer.

    État léger uniquement (sets d'ints) : vidé à chaque reset de combat.
    """

    id = ""
    label = ""

    def reset(self):
        """Nouveau combat / fin de combat — libérer toute RAM du script."""

    def on_templates(self, templates, enabled):
        """templates: id_combattant -> template monstre. enabled = checkbox UI."""

    def on_packet(self, msg):
        """Paquets combat (GDZ, etc.). No-op si inactif."""

    def on_turn_start(self, my_cell):
        """Début de notre tour (case de départ)."""

    @property
    def active(self):
        """True si le script s'applique à ce combat (checkbox + boss présent)."""
        return False

    def forbids_stay(self, cell):
        """True si on ne doit pas finir le tour / s'arrêter sur `cell`."""
        return False

    def end_turn_forbidden(self):
        """Ensemble des cases interdites en fin de tour (peut être vide)."""
        return set()
