"""Modelos de dominio compartidos.

Principio rector heredado de Financiero: **cero dato inventado**. Un valor ausente es `None` con
un `status` que explica por qué, nunca un `0` silencioso. Cada serie lleva su fuente y la hora en
que se consultó, para que cualquier número en pantalla se pueda rastrear.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DataStatus = Literal["OK", "NO_DISPONIBLE", "ERROR_FUENTE"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Metric(StrictModel):
    """Un número calculado o reportado, con su estado explícito."""

    value: float | None
    status: DataStatus
    note: str | None = None

    @classmethod
    def ok(cls, value: float) -> "Metric":
        return cls(value=value, status="OK")

    @classmethod
    def missing(cls, note: str) -> "Metric":
        return cls(value=None, status="NO_DISPONIBLE", note=note)


class Observation(StrictModel):
    period: str  # "2023" para datos anuales, "2024-05-17" para diarios
    value: float


class SourceInfo(StrictModel):
    name: str
    url: str
    fetched_at: datetime
