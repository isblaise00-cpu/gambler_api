"""
Determination du type de course selon le jour de la semaine (regles LONAB/PMU Benin).
  - TIERCE (3 arrivants) : Mercredi, Samedi
  - QUARTE (4 arrivants) : Lundi, Jeudi, Mardi (hors dernier du mois)
  - 4+1    (5 arrivants) : Vendredi, Dimanche, dernier mardi du mois
"""
from __future__ import annotations
from datetime import date, timedelta


def race_type_from_date(d: date) -> str:
    wd = d.weekday()  # 0=Lun ... 6=Dim
    if wd in (2, 5):  # Mercredi, Samedi
        return "TIERCE"
    if wd in (4, 6):  # Vendredi, Dimanche
        return "4+1"
    if wd == 1:       # Mardi : dernier du mois -> 4+1, sinon QUARTE
        return "4+1" if (d + timedelta(weeks=1)).month != d.month else "QUARTE"
    return "QUARTE"   # Lundi, Jeudi
