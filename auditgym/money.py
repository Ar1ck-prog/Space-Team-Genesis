"""Money and FX arithmetic. Amounts are integer minor units, rates are decimal strings.

Rounding rule (contract manifest.rounding_rule = "half_up_to_currency_minor"): a converted
amount is rounded once, half away from zero, to the minor unit of the target currency.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext

# ISO-4217 minor-unit exponents of every currency the generator may use.
EXPONENTS = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "KZT": 2,
    "CNY": 2,
    "TRY": 2,
    "JPY": 0,
    "KWD": 3,
}

ROUNDING_RULE = "half_up_to_currency_minor"
JS_SAFE_INT = 9007199254740991


def convert_minor(amount_minor: int, source: str, target: str, rate: str) -> int:
    """base_minor = round_half_up(amount_minor * rate * 10**(exp(target) - exp(source)))."""
    if source == target:
        if Decimal(rate) != 1:
            raise ValueError("same-currency conversion requires rate 1")
        return int(amount_minor)
    with localcontext() as ctx:
        ctx.prec = 60
        value = Decimal(int(amount_minor)) * Decimal(rate)
        value = value.scaleb(EXPONENTS[target] - EXPONENTS[source])
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_rate(x: float, significant: int = 10) -> str:
    """Format a positive float as a plain decimal string (no exponent) with `significant` digits."""
    d = Decimal(repr(x))
    digits = significant - (d.adjusted() + 1)
    q = Decimal(1).scaleb(-digits)
    s = format(d.quantize(q, rounding=ROUND_HALF_EVEN).normalize(), "f")
    if "E" in s or "e" in s:
        raise ValueError(f"rate formatted with exponent: {s}")
    return s


def scale_minor(amount_minor: int, fraction: float) -> int:
    with localcontext() as ctx:
        ctx.prec = 60
        return int((Decimal(int(amount_minor)) * Decimal(repr(fraction))).quantize(Decimal(1), rounding=ROUND_HALF_UP))
