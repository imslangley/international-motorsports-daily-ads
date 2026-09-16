"""The matching social description.

Used only when production.mode = 'produce'. When ChatGPT supplies the description,
this module is not called - but `check_description` still validates whatever arrives.

Every fact in a generated description comes from the Unit, which came from the live
listing. The template provider invents nothing. The OpenAI provider is given the
verified facts and told in the strongest terms not to add any.
"""
from __future__ import annotations

import os
import re

from .core import Unit, get_logger, money_str


class CopyError(RuntimeError):
    """Raised when a description cannot be produced or fails its own rules."""


def build_from_template(unit: Unit, config: dict) -> str:
    """Deterministic, no API key, no invention. The safe default."""
    biz = config["business"]
    lines = [
        f"{unit.identity.upper()}",
        "",
    ]
    if unit.color:
        lines.append(f"Colour: {unit.color}")
    if unit.stock_number:
        lines.append(f"Stock #: {unit.stock_number}")
    lines.append("")

    if unit.sale_price is not None:
        lines.append(f"On Sale: {money_str(unit.sale_price)}")
    if unit.regular_price is not None:
        lines.append(f"Was: {money_str(unit.regular_price)}")
    if unit.savings is not None:
        lines.append(f"You Save: {money_str(unit.savings)}")

    lines += [
        "",
        "Financing available OAC. Trades welcome.",
        biz["ship_line"],
        "",
        f"See the full listing: {unit.inventory_url}",
        f"Call or Text {biz['phone']}",
        "",
        biz["name"],
        biz["tagline"],
    ]
    return "\n".join(lines).strip() + "\n"


def build_with_openai(unit: Unit, config: dict) -> str:
    """Optional. Requires OPENAI_API_KEY. Facts are supplied, not researched."""
    import json
    import urllib.request

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise CopyError(
            "description.provider is 'openai' but OPENAI_API_KEY is not set.\n"
            "Add it to .env (see .env.example), or set description.provider to "
            "'template' in config/config.json."
        )

    biz = config["business"]
    facts = {
        "year": unit.year, "make": unit.make, "model": unit.model, "trim": unit.trim,
        "colour": unit.color, "stock_number": unit.stock_number,
        "sale_price": money_str(unit.sale_price),
        "regular_price": money_str(unit.regular_price),
        "savings": money_str(unit.savings),
        "inventory_url": unit.inventory_url,
    }
    system = (
        f"You write social captions for {biz['name']}, a powersports dealership in "
        f"{' and '.join(biz['stores'])}, BC.\n"
        "You will be given verified facts as JSON. Use ONLY those facts.\n"
        "HARD RULES:\n"
        "- Never state a number, spec, feature, promotion or date that is not in the facts.\n"
        "- Never claim stock levels or availability counts.\n"
        "- Never invent a model name, trim, colour or finance rate.\n"
        "- Include the inventory_url exactly as given.\n"
        f"- Include the phone {biz['phone']}, the line '{biz['ship_line']}', "
        f"'{biz['name']}' and '{biz['tagline']}'.\n"
        "- Canadian English. Sparing emoji. End with a clear call to action and hashtags."
    )
    body = json.dumps({
        "model": config["description"].get("openai_model", "gpt-4o"),
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "Verified facts:\n" + json.dumps(facts, indent=2)},
        ],
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"].strip() + "\n"
    except Exception as exc:
        raise CopyError(f"OpenAI description generation failed: {exc}") from exc


def build_description(unit: Unit, config: dict) -> str:
    provider = config["description"].get("provider", "template")
    log = get_logger()
    log.info("Building description via '%s' provider.", provider)
    if provider == "template":
        return build_from_template(unit, config)
    if provider == "openai":
        return build_with_openai(unit, config)
    raise CopyError(f"Unknown description.provider '{provider}'. "
                    f"Use 'template' or 'openai'.")


# ------------------------------------------------------------------ validation

def check_description(text: str, unit: Unit, config: dict) -> tuple[list[str], list[str]]:
    """Returns (blocking_failures, warnings) for a description from any source."""
    cfg = config["description"]
    vcfg = config["validation"]
    failures: list[str] = []
    warnings: list[str] = []

    if not text or not text.strip():
        return (["description is empty"], [])

    lowered = text.lower()

    # The inventory URL contains the unit's own slug (…/2024-mv-agusta-lxp-…), so a
    # naive substring search finds the year, make and model inside the link even when
    # the prose is about a completely different motorcycle. Year/make/model agreement
    # is therefore checked against the copy with URLs stripped out.
    prose = re.sub(r"https?://\S+", " ", text)
    prose_lower = prose.lower()

    for phrase in cfg.get("required_phrases", []):
        if phrase.lower() not in lowered:
            failures.append(f"description is missing the required phrase '{phrase}'")

    phone_digits = re.sub(r"\D", "", config["business"]["phone"])
    if phone_digits not in re.sub(r"\D", "", text):
        failures.append(f"description is missing the phone number "
                        f"{config['business']['phone']}")

    if vcfg.get("require_url_in_description", True):
        if unit.inventory_url and unit.inventory_url not in text:
            failures.append("description does not contain the unit's inventory URL")

    # The description must name the same unit as the graphic and the listing.
    if unit.year and unit.year not in prose:
        failures.append(f"description does not mention the year {unit.year}")
    for token in [t for t in unit.make.lower().split() if len(t) > 1]:
        if token not in prose_lower:
            failures.append(f"description does not mention the make '{unit.make}'")
            break
    model_tokens = [t for t in unit.model.lower().split() if len(t) > 1]
    if model_tokens and not any(t in prose_lower for t in model_tokens):
        failures.append(f"description does not mention the model '{unit.model}'")

    # Prices in the copy must agree with the listing.
    if vcfg.get("require_price_agreement", True) and unit.sale_price is not None:
        amounts = {float(a.replace(",", "")) for a in
                   re.findall(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", text)}
        if amounts and not any(abs(a - unit.sale_price) < 1 for a in amounts):
            failures.append(
                f"description prices {sorted(amounts)} do not include the listing's "
                f"sale price {money_str(unit.sale_price)}")

    for pattern in cfg.get("banned_patterns", []):
        if pattern.lower() in lowered:
            failures.append(f"description contains the banned phrase '{pattern}'")

    limit = cfg.get("max_chars")
    if limit and len(text) > limit:
        warnings.append(f"description is {len(text)} characters, over the "
                        f"{limit} guideline")
    if unit.color and unit.color.lower() not in lowered:
        warnings.append(f"description does not mention the colour '{unit.color}'")

    return failures, warnings
