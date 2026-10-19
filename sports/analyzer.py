"""
Sports LLM analyzer -- analyse qualitative d'un evenement sportif via Claude ou Groq.

Meme provider-pattern que analyzer.py hippique :
  AI_PROVIDER=anthropic|groq|mock  (defaut: anthropic)
  ANTHROPIC_MODEL, GROQ_MODEL via env

Principe NON NEGOCIABLE :
  Claude recoit les probabilites du modele statistique et doit les utiliser
  EXACTEMENT. Il fournit l'analyse qualitative (forme, domicile, enjeux,
  back-to-back) et le reperage des value bets -- il n'invente aucun chiffre.

Sortie garantie :
  {
    "predictions": [{"market", "label", "model_prob", "recommended", "rationale"}],
    "value_bets":  [...],
    "confidence":  int,
    "commentary":  str
  }
"""
from __future__ import annotations

import json
import os

from loguru import logger

_FOOTBALL_PROMPT = """\
Tu es un expert en analyse de matchs de football. Tu vas rediger une analyse qualitative \
en utilisant EXACTEMENT les probabilites calculees par le modele statistique ci-dessous -- \
tu n'inventes ou ne recalcules AUCUN chiffre.

DONNEES DU MATCH :
{match_json}

REGLES IMPERATIVES :
1. Utilise EXACTEMENT les valeurs de "PROBABILITES_MODELE_NE_PAS_MODIFIER" dans predictions.model_prob.
2. predictions doit contenir au moins ces marches : 1 (victoire domicile), X (nul), 2 (victoire exterieur), over_2_5, btts.
3. recommended = true uniquement si le modele affiche > 55 % ET le contexte qualitatif confirme.
4. value_bets = reprends EXACTEMENT la liste "value_bets_modele" fournie (ne la recalcule pas).
5. confidence (0-100) = coherence des signaux (forme + H2H + classement), pas la certitude du resultat.
6. commentary = 2 a 4 phrases en francais : forme recente des equipes, avantage domicile/exterieur, enjeux du match, H2H notable.
7. JSON valide uniquement -- sans markdown, sans ```, sans commentaire.

Reponds UNIQUEMENT avec ce JSON :
{{
  "predictions": [
    {{"market": "1",        "label": "Victoire {home}",              "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "X",        "label": "Match nul",                    "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "2",        "label": "Victoire {away}",              "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "over_2_5", "label": "Plus de 2.5 buts",            "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "btts",     "label": "Les deux equipes marquent",    "model_prob": 0.0, "recommended": false, "rationale": "..."}}
  ],
  "value_bets": [],
  "confidence": 0,
  "commentary": ""
}}
"""

_BASKETBALL_PROMPT = """\
Tu es un expert en analyse de matchs de basketball. Tu vas rediger une analyse qualitative \
en utilisant EXACTEMENT les probabilites calculees par le modele statistique ci-dessous -- \
tu n'inventes ou ne recalcules AUCUN chiffre.

DONNEES DU MATCH :
{match_json}

REGLES IMPERATIVES :
1. Utilise EXACTEMENT les valeurs de "PROBABILITES_MODELE_NE_PAS_MODIFIER" dans predictions.model_prob.
2. predictions doit contenir : home (moneyline domicile), away (moneyline exterieur), over (total points).
3. recommended = true uniquement si le modele affiche > 60 % ET le contexte qualitatif confirme.
4. value_bets = reprends EXACTEMENT la liste "value_bets_modele" fournie (ne la recalcule pas).
5. confidence (0-100) = coherence des signaux (forme, back-to-back, avantage domicile, H2H).
6. commentary = 2 a 4 phrases en francais : forme recente, fatigue/back-to-back, avantage de terrain, H2H notable.
7. JSON valide uniquement -- sans markdown, sans ```, sans commentaire.

Reponds UNIQUEMENT avec ce JSON :
{{
  "predictions": [
    {{"market": "home",  "label": "Victoire {home}",         "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "away",  "label": "Victoire {away}",         "model_prob": 0.0, "recommended": false, "rationale": "..."}},
    {{"market": "over",  "label": "Total over {ou_line}",    "model_prob": 0.0, "recommended": false, "rationale": "..."}}
  ],
  "value_bets": [],
  "confidence": 0,
  "commentary": ""
}}
"""


async def analyze_event(
    sport: str,
    event_data: dict,
    model_probs: dict,
    value_bets_data: list,
) -> dict:
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()

    match_json = _build_match_json(sport, event_data, model_probs, value_bets_data)
    prompt     = _build_prompt(sport, event_data, match_json)

    if provider == "mock" or (
        provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY")
    ) or (
        provider == "groq" and not os.getenv("GROQ_API_KEY")
    ):
        logger.warning(f"[SportsAnalyzer] Provider {provider!r} sans cle -- mock deterministe.")
        return _mock_analyze(sport, event_data, model_probs, value_bets_data)

    try:
        if provider == "groq":
            raw = await _call_groq(prompt)
        else:
            raw = await _call_anthropic(prompt)
        result = _parse_json(raw)
        _validate(result)
        return result
    except Exception as exc:
        logger.error(f"[SportsAnalyzer] LLM error ({provider}): {exc} -- fallback mock.")
        return _mock_analyze(sport, event_data, model_probs, value_bets_data)


async def _call_anthropic(prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def _call_groq(prompt: str) -> str:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    response = await client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama3-70b-8192"),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _build_prompt(sport: str, event_data: dict, match_json: str) -> str:
    event = event_data.get("event", {})
    home  = event.get("home_team", "Domicile")
    away  = event.get("away_team", "Exterieur")
    template = _FOOTBALL_PROMPT if sport == "FOOTBALL" else _BASKETBALL_PROMPT
    ou_line = event_data.get("model_probs_cache", {}).get("ou_line", "?")
    return template.format(match_json=match_json, home=home, away=away, ou_line=ou_line)


def _build_match_json(sport, event_data, model_probs, value_bets_data) -> str:
    event     = event_data.get("event", {})
    hf        = event_data.get("home_form", {})
    af        = event_data.get("away_form", {})
    h2h       = event_data.get("h2h", [])[:5]
    standings = event_data.get("standings", [])
    odds_raw  = event_data.get("odds", {})
    bsd_preds = odds_raw.get("bsd") if isinstance(odds_raw, dict) else None
    odds      = {k: v for k, v in odds_raw.items() if k != "bsd"}

    home_name = event.get("home_team", "")
    away_name = event.get("away_team", "")

    home_rank = next((s["rank"] for s in standings if s.get("team") == home_name), None)
    away_rank = next((s["rank"] for s in standings if s.get("team") == away_name), None)

    if sport == "FOOTBALL":
        form_section = {
            "forme_domicile":  _football_form_summary(hf),
            "forme_exterieur": _football_form_summary(af),
        }
    else:
        form_section = {
            "forme_domicile":  _basketball_form_summary(hf),
            "forme_exterieur": _basketball_form_summary(af),
        }

    h2h_lines = [
        f"{m.get('home_team')} {m.get('home_score')}-{m.get('away_score')} "
        f"{m.get('away_team')} ({m.get('date', '')[:10]})"
        for m in h2h
    ]

    data = {
        "match": {
            "competition": event.get("league", ""),
            "domicile":    home_name,
            "exterieur":   away_name,
            "heure":       event.get("kickoff", ""),
            "classement":  {home_name: home_rank, away_name: away_rank},
        },
        **form_section,
        "historique_h2h": h2h_lines if h2h_lines else ["Pas d'historique disponible"],
        "cotes_marche":   odds,
        "PROBABILITES_MODELE_NE_PAS_MODIFIER": model_probs,
        "value_bets_modele": value_bets_data,
    }
    if bsd_preds:
        data["predictions_ia_bsd"] = bsd_preds
    return json.dumps(data, ensure_ascii=False, indent=2)


def _football_form_summary(form: dict) -> dict:
    matches = form.get("last_matches", [])
    n = max(len(matches), 1)
    return {
        "5_derniers":         form.get("form_string", "N/A"),
        "buts_marques_moy":   round(form.get("goals_scored", 0) / n, 2),
        "buts_encaisses_moy": round(form.get("goals_conceded", 0) / n, 2),
    }


def _basketball_form_summary(form: dict) -> dict:
    matches = form.get("last_matches", [])
    n = max(len(matches), 1)
    return {
        "5_derniers":        form.get("form_string", "N/A"),
        "pts_marques_moy":   form.get("points_scored_avg", round(form.get("goals_scored", 0) / n, 2)),
        "pts_encaisses_moy": form.get("points_conceded_avg", round(form.get("goals_conceded", 0) / n, 2)),
    }


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _validate(result: dict) -> None:
    for key in ("predictions", "value_bets", "confidence", "commentary"):
        if key not in result:
            raise ValueError(f"Cle manquante dans la reponse LLM : {key!r}")
    if not isinstance(result["predictions"], list) or not result["predictions"]:
        raise ValueError("predictions doit etre une liste non vide")
    if not isinstance(result["confidence"], int) or not (0 <= result["confidence"] <= 100):
        raise ValueError(f"confidence invalide : {result['confidence']}")


def _mock_analyze(sport, event_data, model_probs, value_bets_data) -> dict:
    event = event_data.get("event", {})
    home  = event.get("home_team", "Domicile")
    away  = event.get("away_team", "Exterieur")

    if sport == "FOOTBALL":
        p1 = model_probs.get("prob_1", 1 / 3)
        px = model_probs.get("prob_x", 1 / 3)
        p2 = model_probs.get("prob_2", 1 / 3)
        po = model_probs.get("prob_over_2_5", 0.50)
        pb = model_probs.get("prob_btts", 0.40)
        xs = model_probs.get("expected_score", ["-", "-"])
        predictions = [
            {"market": "1",        "label": f"Victoire {home}",          "model_prob": round(p1, 4), "recommended": p1 > 0.55, "rationale": "Base sur la force relative des equipes."},
            {"market": "X",        "label": "Match nul",                  "model_prob": round(px, 4), "recommended": px > 0.38, "rationale": "Niveau d'incertitude estime par le modele."},
            {"market": "2",        "label": f"Victoire {away}",          "model_prob": round(p2, 4), "recommended": p2 > 0.55, "rationale": "Base sur la forme recente de l'equipe."},
            {"market": "over_2_5", "label": "Plus de 2.5 buts",          "model_prob": round(po, 4), "recommended": po > 0.55, "rationale": "Estimation basee sur les attaques et defenses."},
            {"market": "btts",     "label": "Les deux equipes marquent",  "model_prob": round(pb, 4), "recommended": pb > 0.55, "rationale": "Base sur les efficacites offensives."},
        ]
        commentary = (
            f"Le modele statistique estime {round(p1 * 100)}% de victoire pour {home}, "
            f"{round(px * 100)}% pour le nul et {round(p2 * 100)}% pour {away}. "
            f"Score attendu : {xs[0]}-{xs[1]}."
        )
        conf_base = max(p1, px, p2)
    else:
        ph = model_probs.get("prob_home", 0.50)
        pa = model_probs.get("prob_away", 0.50)
        po = model_probs.get("prob_over", 0.50)
        xt = model_probs.get("expected_total", "?")
        xm = model_probs.get("expected_margin", "?")
        xl = model_probs.get("ou_line")
        xph = model_probs.get("xPts_home", "?")
        xpa = model_probs.get("xPts_away", "?")
        ou_label = f"over {xl}" if xl else "over total"
        predictions = [
            {"market": "home", "label": f"Victoire {home}",    "model_prob": round(ph, 4), "recommended": ph > 0.60, "rationale": "Estime par le modele d'efficacite ajuste au pace."},
            {"market": "away", "label": f"Victoire {away}",    "model_prob": round(pa, 4), "recommended": pa > 0.60, "rationale": "Estime par le modele d'efficacite ajuste au pace."},
            {"market": "over", "label": f"Total {ou_label}",   "model_prob": round(po, 4), "recommended": po > 0.60, "rationale": "Base sur les efficacites offensives et defensives."},
        ]
        commentary = (
            f"Le modele predit {round(ph * 100)}% de victoire pour {home} "
            f"avec un ecart attendu de {xm} pts ({xph}-{xpa}). "
            f"Total de points attendu : {xt}."
        )
        conf_base = max(ph, pa)

    confidence = min(95, max(50, int(conf_base * 100)))
    return {"predictions": predictions, "value_bets": value_bets_data, "confidence": confidence, "commentary": commentary}
