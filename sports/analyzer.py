"""
Sports analyzer -- construit les predictions a partir des probabilites du modele
et des predictions BSD. Plus de LLM, plus de mock.

Sortie garantie :
  {
    "predictions": [{"market", "label", "model_prob", "recommended"}],
    "value_bets":  [...],
    "confidence":  int,
    "commentary":  str
  }
"""
from __future__ import annotations


_FOOTBALL_MARKETS = [
    ("1",        "Victoire {home}"),
    ("X",        "Match nul"),
    ("2",        "Victoire {away}"),
    ("over_2_5", "Plus de 2.5 buts"),
    ("btts",     "Les deux equipes marquent"),
]

_BASKETBALL_MARKETS = [
    ("home", "Victoire {home}"),
    ("away", "Victoire {away}"),
    ("over", "Total over {ou_line}"),
]

# Seuils de recommandation par sport
_RECOMMEND_THRESHOLD = {
    "FOOTBALL":   0.55,
    "BASKETBALL": 0.60,
}


async def analyze_event(
    sport: str,
    event_data: dict,
    model_probs: dict,
    value_bets_data: list,
) -> dict:
    event   = event_data.get("event", {})
    home    = event.get("home_team", "Domicile")
    away    = event.get("away_team", "Exterieur")
    ou_line = event_data.get("model_probs_cache", {}).get("ou_line", "?")
    threshold = _RECOMMEND_THRESHOLD.get(sport, 0.55)

    markets = _FOOTBALL_MARKETS if sport == "FOOTBALL" else _BASKETBALL_MARKETS

    predictions = []
    for market, label_tpl in markets:
        label = label_tpl.format(home=home, away=away, ou_line=ou_line)
        prob  = float(model_probs.get(market, 0.0))
        predictions.append({
            "market":      market,
            "label":       label,
            "model_prob":  round(prob, 4),
            "recommended": prob > threshold,
        })

    # Confiance : dispersion des probs -- plus c'est tranché, plus c'est confiant
    main_probs = [p["model_prob"] for p in predictions[:3]]  # 1X2 ou home/away
    confidence = _compute_confidence(main_probs)

    return {
        "predictions": predictions,
        "value_bets":  value_bets_data,
        "confidence":  confidence,
        "commentary":  "",
    }


def _compute_confidence(probs: list[float]) -> int:
    """
    Confiance 0-100 basee sur la concentration des probabilites.
    Si une issue domine franchement (>60%), confiance elevee.
    Si tout est equilibre (~33%), confiance basse.
    """
    if not probs:
        return 50
    max_prob = max(probs)
    # Lineaire entre 33% (confiance=20) et 75% (confiance=90)
    raw = (max_prob - 0.33) / (0.75 - 0.33) * 70 + 20
    return max(0, min(100, round(raw)))
