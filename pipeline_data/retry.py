"""Retries bornes, uniquement pour les erreurs classees temporaires."""

import logging
import time
from collections.abc import Callable
from typing import TypeVar


Result = TypeVar("Result")
logger = logging.getLogger(__name__)


def retry(
    operation: Callable[[], Result],
    is_transient: Callable[[Exception], bool],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
) -> Result:
    if attempts < 1 or base_delay < 0:
        raise ValueError("attempts doit etre positif et base_delay non negatif")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1)
            # Aucun secret ni contenu de transaction dans le journal.
            logger.warning("Erreur temporaire %s, tentative %d/%d, reprise dans %.1fs",
                           type(exc).__name__, attempt, attempts, delay)
            time.sleep(delay)
    raise AssertionError("boucle de retry inaccessible")
