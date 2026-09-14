#!/usr/bin/env python3
"""Общая база: контуры денег, страны турниров, горизонты сигналов."""
import re

# LAB — мелкие рынки (ITF/UTR/квалификации): только лаборатория мелочью.
# MAIN — основа WTA/ATP: туда идёт размер.
LAB_RE = re.compile(r"ITF|UTR|квалифик|челленджер|challenger", re.I)
MAIN_RE = re.compile(r"WTA\.|ATP\.", re.I)


def tour_circuit(tour):
    t = tour or ""
    if LAB_RE.search(t):
        return "LAB"
    if MAIN_RE.search(t):
        return "MAIN"
    return "LAB"


CIRCUIT_STAKE = {"LAB": 0.5, "MAIN": 1.0, "ASYM": 1.5}

# турнир -> страна (для «местных голодных» и акклиматизации)
TOUR_COUNTRY = {
    "Хургада": "EGY", "Hurghada": "EGY", "Шарм": "EGY", "Sharm": "EGY",
    "Монастир": "TUN", "Monastir": "TUN",
    "Таллахасси": "USA", "Tallahassee": "USA",
    "Тибурон": "USA", "Tiburon": "USA", "Ньюпорт": "USA", "Newport": "USA",
    "Гвадалахара": "MEX", "Guadalajara": "MEX",
    "Сан-Паулу": "BRA", "Sao Paulo": "BRA",
    "Ренн": "FRA", "Rennes": "FRA",
    "Фантхьет": "VIE", "Анталья": "TUR", "Antalya": "TUR",
    "Шымкент": "KAZ", "Кайсери": "TUR",
}

ALLOWED_HORIZONS = ("game", "set", "match", "prematch")


def tour_country(tour):
    t = tour or ""
    for k, v in TOUR_COUNTRY.items():
        if k.lower() in t.lower():
            return v
    return ""
