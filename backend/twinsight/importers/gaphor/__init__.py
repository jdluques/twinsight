"""Importador de modelos ``.gaphor`` (SysML/UML) para TwinSight.

Un ``.gaphor`` es XML de datos (raíz ``<gaphor version="4">``). Este paquete lo
traduce al :class:`~twinsight.importers.base.GraphBatch` sin depender de la
librería de Gaphor (y por tanto sin GTK/PyGObject): el parseo se hace con
``xml.etree.ElementTree`` de la biblioteca estándar.

* :mod:`~twinsight.importers.gaphor.parser` — parseo XML puro a estructuras.
* :mod:`~twinsight.importers.gaphor.translator` — clase :class:`GaphorTranslator`
  que implementa el contrato :class:`~twinsight.importers.base.Translator`.
"""

from __future__ import annotations

from .translator import GaphorTranslator

__all__ = ["GaphorTranslator"]
