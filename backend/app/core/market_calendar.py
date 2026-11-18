"""Feriados de la Bolsa de Nueva York (NYSE), calculados por regla.

Reglas publicadas por la NYSE: Año Nuevo, Martin Luther King (3.er lunes de enero), Presidentes
(3.er lunes de febrero), Viernes Santo, Memorial Day (último lunes de mayo), Juneteenth (19/6,
desde 2022), Independencia (4/7), Labor Day (1.er lunes de septiembre), Acción de Gracias (4.º
jueves de noviembre) y Navidad. Si cae sábado se cierra el viernes anterior; si cae domingo, el
lunes siguiente. Excepción: si Año Nuevo cae sábado, no se cierra el viernes 31/12.

No contempla cierres extraordinarios (duelos nacionales, emergencias): se avisan con poca
anticipación y no siguen una regla.
"""

from datetime import date, timedelta


def easter(year: int) -> date:
    """Domingo de Pascua (algoritmo anónimo gregoriano)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:  # sábado → viernes
        return d - timedelta(days=1)
    if d.weekday() == 6:  # domingo → lunes
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    fixed = [date(year, 7, 4), date(year, 12, 25)]
    if year >= 2022:
        fixed.append(date(year, 6, 19))
    days = {_observed(d) for d in fixed}
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # sábado: no se compensa el 31/12 anterior
        days.add(_observed(new_year))
    days |= {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King
        _nth_weekday(year, 2, 0, 3),  # Presidentes
        easter(year) - timedelta(days=2),  # Viernes Santo
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Acción de Gracias
    }
    return days


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt
