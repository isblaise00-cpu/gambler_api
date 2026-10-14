"""Classement du jour : presse 70 %, cotes 30 %, sept candidats, vingt groupes.

Les scores servent uniquement a classer les selections. Ce ne sont pas des
probabilites. Aucune base de donnees, aucun historique et aucun appel IA ici.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import math
import unicodedata

from loguru import logger

MODEL_VERSION = "consensus_v1"


def race_size(race_type: str) -> int:
    if not isinstance(race_type, str):
        raise ValueError("Type de course absent du programme.")
    normalized = "".join(c for c in unicodedata.normalize("NFD", race_type.upper())
                         if not unicodedata.combining(c)).replace(" ", "")
    sizes = {"TIERCE": 3, "QUARTE": 4, "QUINTE": 5, "QUINTE+": 5, "4+1": 5}
    if normalized not in sizes:
        raise ValueError(f"Type de course non reconnu : {race_type!r}")
    return sizes[normalized]


def decimal_odds(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", ".")
        if "/" in text:
            numerator, denominator = map(float, text.split("/"))
            if numerator <= 0 or denominator <= 0:
                return None
            result = 1 + numerator / denominator
        else:
            result = float(text)
        return result if math.isfinite(result) and result > 1 else None
    except (ValueError, ZeroDivisionError):
        return None


def _odds_points(odds: dict[int, float]) -> dict[int, float]:
    groups = defaultdict(list)
    for number, value in odds.items():
        groups[value].append(number)
    points = {}
    position = 0
    for value in sorted(groups):
        numbers = groups[value]
        shared = sum(max(0, 5 - rank) for rank in range(position, position + len(numbers))) / len(numbers)
        points.update({number: shared for number in numbers})
        position += len(numbers)
    return points


def build_pronostic(data: dict) -> dict:
    """Mode strict -- seuils originaux garantissant la qualite maximale."""
    return _build(data, strict=True)


def build_pronostic_degraded(data: dict) -> dict:
    """Mode degrade -- seuils assouplis, utilise uniquement en fallback."""
    return _build(data, strict=False)


def _build(data: dict, strict: bool) -> dict:
    race = data.get("race", {})
    size = race_size(race.get("type", ""))
    horses = data.get("horses")
    if not isinstance(horses, list) or not horses:
        raise ValueError("Liste des chevaux absente du programme.")
    all_numbers = set()
    for horse in horses:
        number = horse.get("num")
        if type(number) is not int or number <= 0 or number in all_numbers:
            raise ValueError("Numeros de chevaux invalides ou dupliques.")
        if not isinstance(horse.get("nom"), str) or not horse["nom"].strip():
            raise ValueError(f"Nom du cheval {number} absent.")
        if type(horse.get("non_partant", False)) is not bool:
            raise ValueError(f"Statut non-partant du cheval {number} invalide.")
        all_numbers.add(number)
    if type(race.get("num_concurrents")) is not int or race["num_concurrents"] <= 0:
        raise ValueError("num_concurrents absent ou invalide dans le programme.")

    active = {h["num"]: h for h in horses if not h.get("non_partant", False)}

    min_active = 7 if strict else size + 1
    if len(active) < min_active:
        mode_label = "Mode strict" if strict else "Mode degrade"
        raise ValueError(
            f"{mode_label} : "
            f"il faut au moins {min_active} partants actifs ({len(active)} disponible(s))."
        )

    partners: dict = {}
    raw_partners = data.get("partner_predictions")
    if not isinstance(raw_partners, list):
        if strict:
            raise ValueError("Pronostics partenaires absents du programme.")
        raw_partners = []

    excluded_partners: list[str] = []
    for partner in raw_partners:
        name = " ".join(str(partner.get("source", "")).upper().replace("\u2019", "'").split())
        nums = partner.get("nums")
        if not name or not isinstance(nums, list) or any(type(n) is not int or n not in all_numbers for n in nums):
            if strict:
                raise ValueError("Pronostic partenaire invalide : source ou numero inconnu.")
            continue
        if len(set(nums)) != len(nums):
            if strict:
                raise ValueError(f"Cheval repete dans le pronostic de {name}.")
            continue
        nums = [n for n in nums if n in active]
        if name in partners and partners[name] != nums:
            if strict:
                raise ValueError(f"Deux pronostics contradictoires pour {name}.")
            continue
        min_partner_size = 5 if strict else size
        if len(nums) >= min_partner_size:
            partners[name] = nums
        elif nums:
            excluded_partners.append(f"{name} ({len(nums)} partant(s) restant(s) apres NP)")

    if strict and len(partners) < 2:
        sep = ", "
        suffix = (" -- exclus : " + sep.join(excluded_partners)) if excluded_partners else ""
        raise ValueError(f"Au moins deux pronostics partenaires complets sont necessaires{suffix}.")
    if not strict:
        if len(partners) == 0:
            logger.warning(
                "[Mode degrade] Aucun partenaire utilisable -- classement sur cotes uniquement."
                + (f" Exclus : {', '.join(excluded_partners)}" if excluded_partners else "")
            )
        elif len(partners) == 1:
            logger.warning("[Mode degrade] Un seul partenaire -- ponderation presse reduite.")

    press = {n: 0 for n in active}
    citations = {n: 0 for n in active}
    for nums in partners.values():
        for rank, number in enumerate(nums[:5]):
            press[number] += 5 - rank
            citations[number] += 1

    columns = {}
    for key in ("cote_pt", "cote_tm"):
        values = {n: decimal_odds(h.get(key)) for n, h in active.items()}
        if all(value is not None for value in values.values()):
            columns[key] = values
    if not columns:
        if strict:
            raise ValueError("Aucune colonne de cotes complete et valide pour les partants.")
        logger.warning("[Mode degrade] Aucune cote complete -- classement sur presse uniquement.")

    column_points = [_odds_points(values) for values in columns.values()]
    ranked = []
    for number, horse in active.items():
        press_score = press[number] / (5 * len(partners)) * 100 if partners else 0.0
        odds_score = (
            sum(points[number] for points in column_points) / (5 * len(columns)) * 100
            if columns else 0.0
        )
        if not partners:
            score = odds_score
        elif not columns:
            score = press_score
        else:
            score = .7 * press_score + .3 * odds_score
        mean_odds = (
            sum(values[number] for values in columns.values()) / len(columns)
            if columns else 0.0
        )
        ranked.append({
            "num": number, "nom": horse["nom"],
            "press_score": press_score, "odds_score": odds_score,
            "score": score, "citations": citations[number], "mean_odds": mean_odds,
        })
    ranked.sort(key=lambda h: (-h["score"], -h["press_score"], h["mean_odds"], h["num"]))

    n_select = 7 if strict else min(7, len(ranked))
    selected = ranked[:n_select]
    groups = list(combinations(range(len(selected)), size))
    groups.sort(key=lambda indices: (-sum(selected[i]["score"] for i in indices), indices))
    proposals = []
    for index, indices in enumerate(groups[:20]):
        proposals.append({
            "id": "prono_du_jour" if index == 0 else f"consensus_{index + 1:02d}",
            "title": "LE PRONO DU JOUR" if index == 0 else f"COMBINAISON #{index + 1}",
            "subtitle": "Selection en desordre - ordre de preference affiche",
            "nums": [selected[i]["num"] for i in indices],
            "score": round(sum(selected[i]["score"] for i in indices) / size, 2),
            "source": MODEL_VERSION,
        })
    proposals[0]["selection"] = {
        "version": MODEL_VERSION,
        "press_weight": 0.0 if not partners else (1.0 if not columns else .7),
        "odds_weight":  0.0 if not columns  else (1.0 if not partners else .3),
        "mode": "strict" if strict else "degraded",
        "candidates": [{k: round(v, 2) if isinstance(v, float) else v for k, v in h.items()} for h in selected],
        "partners": [{"source": name, "nums": nums} for name, nums in sorted(partners.items())],
        "odds_columns": list(columns),
        "possible_combinations": len(groups),
        "generated_combinations": len(proposals),
    }
    if not partners:
        weighting = "cotes 100 %"
    elif not columns:
        weighting = "presse 100 %"
    else:
        weighting = "presse 70 %, cotes 30 %"
    mode_label = "" if strict else " [MODE DEGRADE]"
    commentary = (
        f"{len(selected)} chevaux classes a partir de {len(partners)} partenaire(s) "
        f"et de {len(columns)} colonne(s) de cotes. "
        f"Ponderation : {weighting}.{mode_label} {len(proposals)} groupes distincts de {size} chevaux "
        f"sur {len(groups)} possibles dans cette selection. Les scores sur 100 sont des indices de selection, "
        "pas des probabilites de reussite."
    )
    return {**data, "proposals": proposals, "commentary": commentary}
