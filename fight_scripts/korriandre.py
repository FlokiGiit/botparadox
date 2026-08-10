"""Script Korriandre (template 31032).

Méca ému (KorriandreMechanics) :
  - au début de chaque tour joueur : glyphe permanente sous les pieds
    (GDZ+<cell>;0;4)
  - finir le tour sur une glyphe = mort instantanée

Le bot force donc au moins 1 PM hors de la case de départ / hors glyphes
avant de passer, et refuse de s'arrêter sur une glyphe en approche.
"""

from .base import FightScript

KORRIANDRE_TEMPLATE = 31032
GLYPH_COLOR = 4


class KorriandreScript(FightScript):
    id = "korriandre"
    label = "Korriandre"

    def __init__(self):
        self._enabled = False
        self._boss_present = False
        self.glyph_cells = set()
        self.turn_start_cell = None

    def reset(self):
        self._boss_present = False
        self.glyph_cells.clear()
        self.turn_start_cell = None

    @property
    def active(self):
        return self._enabled and self._boss_present

    def on_templates(self, templates, enabled):
        self._enabled = bool(enabled)
        self._boss_present = any(
            t == KORRIANDRE_TEMPLATE for t in templates.values())
        if not self.active:
            # Checkbox off ou pas le boss : on ne garde pas les glyphes.
            if not self._enabled:
                self.glyph_cells.clear()
                self.turn_start_cell = None

    def on_packet(self, msg):
        if not msg.startswith("GDZ"):
            return
        # Même si le GM n'a pas encore armé le script, on lit les GDZ dès que
        # la checkbox est cochée (ordre des paquets variable).
        if not self._enabled:
            return
        body = msg[3:]
        if not body or body[0] not in "+-":
            return
        sign = body[0]
        for part in body[1:].split("|"):
            bits = part.split(";")
            if len(bits) < 3:
                continue
            try:
                cell = int(bits[0])
                color = int(bits[2])
            except ValueError:
                continue
            if color != GLYPH_COLOR:
                continue
            if sign == "+":
                self.glyph_cells.add(cell)
            else:
                self.glyph_cells.discard(cell)

    def on_turn_start(self, my_cell):
        if not self.active:
            self.turn_start_cell = None
            return
        self.turn_start_cell = my_cell
        # La glyphe apparaît sous nos pieds au début du tour : on l'anticipe
        # avant même le GDZ (sinon on pourrait passer trop tôt).
        if my_cell is not None:
            self.glyph_cells.add(my_cell)

    def forbids_stay(self, cell):
        if not self.active or cell is None:
            return False
        return cell in self.glyph_cells or cell == self.turn_start_cell

    def end_turn_forbidden(self):
        if not self.active:
            return set()
        out = set(self.glyph_cells)
        if self.turn_start_cell is not None:
            out.add(self.turn_start_cell)
        return out
