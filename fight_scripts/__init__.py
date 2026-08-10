"""Registre des scripts de combat (checkbox UI + règles boss)."""

from .korriandre import KorriandreScript

# id -> classe (instances créées par CombatAI, une par session)
REGISTRY = {
    KorriandreScript.id: KorriandreScript,
}


def create_scripts():
    """Nouvelles instances (état vide) pour une CombatAI."""
    return {sid: cls() for sid, cls in REGISTRY.items()}


def known_ids():
    return list(REGISTRY)
