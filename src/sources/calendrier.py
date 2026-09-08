"""Calendrier francais : jours feries, saison Tempo."""
from datetime import date, timedelta
from functools import lru_cache


def easter(year):
    """Dimanche de Paques (algorithme de Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=64)
def holidays(year):
    p = easter(year)
    return frozenset([
        date(year, 1, 1),
        p + timedelta(days=1),    # lundi de Paques
        date(year, 5, 1),
        date(year, 5, 8),
        p + timedelta(days=39),   # Ascension
        p + timedelta(days=50),   # lundi de Pentecote
        date(year, 7, 14),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 11, 11),
        date(year, 12, 25),
    ])


def is_holiday(d):
    return d in holidays(d.year)


def season_of(d):
    """Annee Tempo au format '2024-2025' (1er sept -> 31 aout)."""
    start = d.year if d.month >= 9 else d.year - 1
    return f"{start}-{start + 1}"


def season_start(season):
    return date(int(season.split("-")[0]), 9, 1)


def season_end(season):
    return date(int(season.split("-")[1]), 8, 31)


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
