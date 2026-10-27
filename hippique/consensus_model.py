"""Modele Plackett-Luce multi-signaux pour les pronostics hippiques.

Signaux combines (poids dans _WEIGHTS) :
  cotes  (0.25) -- 1/cote normalise
  delta  (0.15) -- mouvement de cotes (signal insider)
  perf   (0.20) -- forme recente (colonne PERF)
  gains  (0.05) -- gains de carriere
  humain (0.05) -- driver / entraineur en forme
  presse (0.30) -- consensus presse (partenaires)

La probabilite de chaque combinaison est calculee par Plackett-Luce exact
(somme sur les k! permutations). Les 20 meilleures combinaisons sont retournees.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from itertools import combinations, permutations

from loguru import logger

MODEL_VERSION = "plackett_luce_v1"

_WEIGHTS: dict[str, float] = {
    "cotes":  0.25,
    "delta":  0.15,
    "perf":   0.20,
    "gains":  0.05,
    "humain": 0.05,
    "presse": 0.30,
}

# ── Fonctions utilitaires ──────────────────────────────────────────────────────

def race_size(race_type: str) -> int:
    if not isinstance(race_type, str):
        raise ValueError("Type de course absent du programme.")
    normalized = "".join(
        c for c in unicodedata.normalize("NFD", race_type.upper())
        if not unicodedata.combining(c)
    ).replace(" ", "")
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


def _normalize_prob(d: dict[int, float]) -> dict[int, float]:
    """Normalise pour que la somme soit 1 (distribution de probabilite)."""
    total = sum(d.values())
    if total <= 0:
        n = len(d)
        return {k: 1.0 / n for k in d}
    return {k: v / total for k, v in d.items()}


def _normalize_max(d: dict[int, float]) -> dict[int, float]:
    """Normalise par le max ; retourne uniforme si tous nuls."""
    mx = max(d.values()) if d else 0.0
    if mx <= 0:
        n = len(d)
        return {k: 1.0 / n for k in d}
    return {k: v / mx for k, v in d.items()}


# ── Signaux ───────────────────────────────────────────────────────────────────

def _signal_cotes(active: dict) -> dict[int, float]:
    """1/cote_pt (plus basse cote = cheval favori = signal fort)."""
    result: dict[int, float] = {}
    for n, h in active.items():
        cote = decimal_odds(h.get("cote_pt")) or decimal_odds(h.get("cote_tm"))
        result[n] = 1.0 / cote if cote else 0.0
    return _normalize_prob(result)


def _signal_delta(active: dict) -> dict[int, float]:
    """Signal insider : la cote a baisse entre PT et TM (argent entrant).
    delta = (cote_pt - cote_tm) / cote_pt, positif si baisse."""
    result: dict[int, float] = {}
    for n, h in active.items():
        pt = decimal_odds(h.get("cote_pt"))
        tm = decimal_odds(h.get("cote_tm"))
        if pt and tm and pt > tm:
            result[n] = (pt - tm) / pt
        else:
            result[n] = 0.0
    return _normalize_max(result)


def _signal_perf(active: dict) -> dict[int, float]:
    """Forme recente a partir de la colonne PERF (ex: '1.1.2.3.4').
    Place 1 = 10 pts, place 2 = 8 pts, ..., place 10+ = 1 pt.
    Les resultats les plus recents sont ponderes davantage (decay 0.3)."""
    result: dict[int, float] = {}
    for n, h in active.items():
        perf_str = str(h.get("perf") or "")
        positions = [int(x) for x in re.findall(r"\d+", perf_str)][:6]
        if not positions:
            result[n] = 0.0
            continue
        weights = [math.exp(-0.3 * i) for i in range(len(positions))]
        total_w = sum(weights)
        # Score : 1ere place = 10, 2e = 8, 3e = 6, 4e = 4, 5e = 2, 6+ = 1
        place_pts = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2}
        score = sum(
            w * place_pts.get(p, 1) for w, p in zip(weights, positions)
        ) / total_w
        result[n] = score
    return _normalize_max(result)


def _signal_gains(active: dict) -> dict[int, float]:
    """Gains de carriere normalises (indice de qualite globale du cheval)."""
    result: dict[int, float] = {}
    for n, h in active.items():
        g = h.get("gains")
        try:
            result[n] = float(g) if g is not None else 0.0
        except (ValueError, TypeError):
            result[n] = 0.0
    return _normalize_max(result)


def _signal_humain(
    active: dict,
    entraineurs_en_forme: list,
    drivers_en_forme: list,
) -> dict[int, float]:
    """Bonus si le driver ou l'entraineur du cheval est cite en forme."""
    ef = {s.strip().upper() for s in entraineurs_en_forme if isinstance(s, str)}
    df = {s.strip().upper() for s in drivers_en_forme if isinstance(s, str)}
    result: dict[int, float] = {}
    for n, h in active.items():
        trainer = str(h.get("trainer") or "").strip().upper()
        driver  = str(h.get("driver") or "").strip().upper()
        score = 0.0
        if trainer and trainer in ef:
            score += 1.0
        if driver and driver in df:
            score += 1.0
        result[n] = score
    return _normalize_max(result)



def _signal_presse(active: dict, partners: dict) -> dict[int, float]:
    """Consensus presse : somme ponderee des citations sur les 5 premiers."""
    result: dict[int, float] = {n: 0.0 for n in active}
    for nums in partners.values():
        for rank, n in enumerate(nums[:5]):
            if n in result:
                result[n] += 5 - rank
    return _normalize_max(result)


# ── Calcul des lambdas ────────────────────────────────────────────────────────

def _compute_lambdas(
    active: dict,
    partners: dict,
    entraineurs_en_forme: list,
    drivers_en_forme: list,
) -> dict[int, float]:
    """Combine tous les signaux en un lambda Plackett-Luce par cheval."""
    signals = {
        "cotes":  _signal_cotes(active),
        "delta":  _signal_delta(active),
        "perf":   _signal_perf(active),
        "gains":  _signal_gains(active),
        "humain": _signal_humain(active, entraineurs_en_forme, drivers_en_forme),
        "presse": _signal_presse(active, partners),
    }
    lambdas: dict[int, float] = {}
    for n in active:
        lam = sum(_WEIGHTS[s] * signals[s].get(n, 0.0) for s in _WEIGHTS)
        lambdas[n] = max(lam, 1e-6)  # plancher pour eviter les zeros
    return lambdas, signals


# ── Probabilite Plackett-Luce ──────────────────────────────────────────────────

def _pl_combo_prob(combo: tuple, lambdas: dict) -> float:
    """P(ces k chevaux finissent dans le top-k, dans n'importe quel ordre).

    Calcule la somme exacte sur les k! permutations :
      P(π) = prod_{j=1}^{k} λ_{π(j)} / (Σ_{remainders})
    Les chevaux hors combinaison restent dans le denominateur tout au long.
    """
    sum_all = sum(lambdas.values())
    total_prob = 0.0
    for perm in permutations(combo):
        p = 1.0
        remaining = sum_all
        for horse in perm:
            p *= lambdas[horse] / remaining
            remaining -= lambdas[horse]
        total_prob += p
    return total_prob


# ── Fonctions publiques ────────────────────────────────────────────────────────

def build_pronostic(data: dict) -> dict:
    """Mode strict -- seuils originaux garantissant la qualite maximale."""
    return _build(data, strict=True)


def build_pronostic_degraded(data: dict) -> dict:
    """Mode degrade -- seuils assouplis, utilise uniquement en fallback."""
    return _build(data, strict=False)


def _build(data: dict, strict: bool) -> dict:  # noqa: C901
    race   = data.get("race", {})
    size   = race_size(race.get("type", ""))
    horses = data.get("horses")

    if not isinstance(horses, list) or not horses:
        raise ValueError("Liste des chevaux absente du programme.")

    all_numbers: set[int] = set()
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

    # ── Partenaires presse ────────────────────────────────────────────────────
    partners: dict[str, list[int]] = {}
    raw_partners = data.get("partner_predictions")
    if not isinstance(raw_partners, list):
        if strict:
            raise ValueError("Pronostics partenaires absents du programme.")
        raw_partners = []

    excluded_partners: list[str] = []
    for partner in raw_partners:
        name = " ".join(
            str(partner.get("source", "")).upper().replace("\u2019", "'").split()
        )
        nums = partner.get("nums")
        if not name or not isinstance(nums, list) or any(
            type(n) is not int or n not in all_numbers for n in nums
        ):
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
        raise ValueError(
            f"Au moins deux pronostics partenaires complets sont necessaires{suffix}."
        )
    if not strict:
        if len(partners) == 0:
            logger.warning(
                "[Mode degrade] Aucun partenaire utilisable -- classement sur signaux quantitatifs."
                + (f" Exclus : {', '.join(excluded_partners)}" if excluded_partners else "")
            )
        elif len(partners) == 1:
            logger.warning("[Mode degrade] Un seul partenaire -- signal presse reduit.")

    # ── Signaux enrichis ──────────────────────────────────────────────────────
    entraineurs_en_forme = data.get("entraineurs_en_forme") or []
    drivers_en_forme     = data.get("drivers_en_forme") or []

    lambdas, _ = _compute_lambdas(
        active, partners, entraineurs_en_forme, drivers_en_forme
    )

    # ── Pool presse : union de tous les chevaux cites au moins une fois ───────
    # Reduit l'espace de recherche et accelere Plackett-Luce.
    press_pool: set[int] = set()
    for nums in partners.values():
        press_pool.update(nums)
    press_pool &= set(active.keys())

    # Completer si le pool est trop petit pour former une combo
    if len(press_pool) < size:
        ranked_by_lambda = sorted(active.keys(), key=lambda n: -lambdas[n])
        for h in ranked_by_lambda:
            press_pool.add(h)
            if len(press_pool) >= size:
                break

    logger.info(
        f"[PL] {len(active)} partants, pool presse={len(press_pool)} "
        f"-> {len(list(combinations(press_pool, size)))} combinaisons de {size}."
    )

    # ── Combinaisons depuis le pool presse, classees par prob PL ─────────────
    pool_combos = list(combinations(sorted(press_pool), size))
    combo_probs = sorted(
        [(combo, _pl_combo_prob(combo, lambdas)) for combo in pool_combos],
        key=lambda x: -x[1],
    )

    # ── Construction des 200 proposals ───────────────────────────────────────
    # score = lift sur le hasard dans le pool presse
    n_combos     = len(pool_combos)
    uniform_prob = 1.0 / n_combos if n_combos > 0 else 1.0

    proposals = []
    for index, (combo, prob) in enumerate(combo_probs[:200]):
        lift = round(min(prob / uniform_prob, 100.0), 1)
        proposals.append({
            "id":    "prono_du_jour" if index == 0 else f"consensus_{index + 1:03d}",
            "title": "LE PRONO DU JOUR" if index == 0 else f"COMBINAISON #{index + 1}",
            "nums":  list(combo),
            "score": lift,
        })

    # ── Metadonnees sur le 1er proposal uniquement ────────────────────────────
    mode_label = "" if strict else " [MODE DEGRADE]"
    proposals[0]["selection"] = {
        "version":              MODEL_VERSION,
        "mode":                 "strict" if strict else "degraded",
        "weights":              _WEIGHTS,
        "pool_size":            len(press_pool),
        "possible_combinations": n_combos,
        "generated_combinations": len(proposals),
        "partenaires":          len(partners),
    }

    commentary = (
        f"{len(active)} partants, pool presse {len(press_pool)} chevaux, "
        f"{n_combos} combinaisons de {size} evaluees "
        f"par modele Plackett-Luce ({MODEL_VERSION}).{mode_label} "
        f"{len(partners)} partenaire(s) presse. "
        "Score = lift sur le hasard (ex: 15 = 15x plus probable qu'une combo aleatoire)."
    )

    return {**data, "proposals": proposals, "commentary": commentary}
