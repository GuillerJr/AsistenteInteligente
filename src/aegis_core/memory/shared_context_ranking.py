"""Ephemeral ranking of authenticated conversation evidence, without an index."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    queries: tuple[frozenset[str], ...]
    entities: frozenset[str] = frozenset()

    @property
    def original(self) -> frozenset[str]:
        return self.queries[0]

    @property
    def wanted(self) -> frozenset[str]:
        return frozenset().union(*self.queries)

    def weights(self, chat_frequency: Mapping[str, int], scanned_chats: int) -> dict[str, float]:
        weights = {}
        for term in self.wanted:
            rarity = 1 + math.log((scanned_chats + 1) / (chat_frequency.get(term, 0) + 1))
            priority = 2 if term in self.original else 0.75
            if term in self.entities:
                priority *= 2.5
            if term.isdigit():
                priority *= 1.4
            weights[term] = rarity * priority
        return weights

    def score(self, found: frozenset[str], weights: Mapping[str, float]) -> float:
        score = sum(weights.get(term, 0) for term in found)
        # Queries represent complementary formulations, not extra votes for a
        # repeated word. Reward coverage, including exact multi-term matches.
        for index, query in enumerate(self.queries):
            if query:
                overlap = len(found & query) / len(query)
                score += overlap * (3 if index == 0 else 1)
        return score


def correction_statement(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:correcci[oó]n|rectifico|rectificamos|corrijo|corregimos|"
            r"reemplaza|sustituye|en realidad|definitiv[oa]|queda fijad[oa]|"
            r"ya no|en lugar de|no eran?|no son|no ser[aá])\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def numerical_evidence(text: str) -> bool:
    return bool(
        re.search(
            r"\b\d+(?:[.,]\d+)*\s*(?:%|EUR\b|USD\b|GB\b|MB\b|ms\b|euros?\b|d[oó]lares?\b)",
            text,
            flags=re.IGNORECASE,
        )
    )


def factual_statement(text: str) -> bool:
    return numerical_evidence(text) or bool(
        re.search(
            r"\b(?:decidimos|acordamos|elegimos|mantener|conservar|debe|requiere|"
            r"prohibid[oa]|restricci[oó]n|no permite|no usar|presupuesto|responsable|"
            r"fecha l[ií]mite|plazo|exactamente|no cambiar|sin notificaciones)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
