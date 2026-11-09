"""Small registered tool surface: no shell, eval, or provider credentials in arguments."""

from decimal import Decimal, InvalidOperation
from .common import DomainError


def execute(adapter_id, arguments, knowledge):
    if not isinstance(arguments, dict):
        raise DomainError("tool_arguments", "Arguments must be an object")
    if adapter_id == "knowledge_search":
        if (
            set(arguments) - {"query", "kind", "limit"}
            or not isinstance(arguments.get("query"), str)
            or not isinstance(arguments.get("kind", "atom"), str)
            or not isinstance(arguments.get("limit", 8), int)
        ):
            raise DomainError(
                "tool_arguments", "Expected query text and optional kind/limit"
            )
        return knowledge.search(**arguments)
    if adapter_id == "offer_arithmetic":
        if set(arguments) != {"unit_price", "quantity", "discount", "shipping", "tax"}:
            raise DomainError(
                "tool_arguments",
                "Expected unit_price, quantity, discount, shipping, tax",
            )
        try:
            n = {k: Decimal(str(v)) for k, v in arguments.items()}
            if any(
                not v.is_finite()
                or v < 0
                or v > Decimal("1e15")
                or abs(v.as_tuple().exponent) > 100
                for v in n.values()
            ):
                raise ValueError()
            if n["quantity"] != int(n["quantity"]):
                raise ValueError()
            subtotal = n["unit_price"] * n["quantity"]
            if n["discount"] > subtotal:
                raise ValueError()
        except (ValueError, InvalidOperation, TypeError):
            raise DomainError(
                "tool_arguments",
                "Use finite nonnegative amounts, integer quantity, and discount no greater than subtotal",
            )
        return {
            "subtotal": str(subtotal),
            "total": str(subtotal - n["discount"] + n["shipping"] + n["tax"]),
            "formula": "unit_price * quantity - discount + shipping + tax",
            "currency": "caller_defined; inputs must share currency",
            "limitations": ["Tax is a supplied amount, not legal tax determination."],
        }
    if adapter_id == "asset_measurements":
        if set(arguments) - {"text", "max_characters"} or not isinstance(
            arguments.get("text"), str
        ):
            raise DomainError(
                "tool_arguments", "Expected text and optional max_characters"
            )
        text = arguments["text"]
        maximum = arguments.get("max_characters")
        if maximum is not None and (not isinstance(maximum, int) or maximum < 0):
            raise DomainError(
                "tool_arguments", "max_characters must be a nonnegative integer"
            )
        return {
            "characters": len(text),
            "words": len(text.split()),
            "within_character_limit": None if maximum is None else len(text) <= maximum,
            "limitations": [
                "Counts do not establish persuasion quality or current platform policy."
            ],
        }
    raise DomainError(
        "unknown_adapter",
        "Available: knowledge_search, offer_arithmetic, asset_measurements",
    )
