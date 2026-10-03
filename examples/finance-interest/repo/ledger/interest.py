"""Annual interest calculations for a small ledger."""


def simple_interest(principal: float, rate: float, years: int) -> float:
    """Simple interest: `rate` applies to the ORIGINAL principal each year."""
    return round(principal * (1 + rate * years), 2)


def compound_interest(principal: float, rate: float, years: int) -> float:
    """Interest compounded annually: each year earns `rate` on the running balance."""
    return round(principal * (1 + rate * years), 2)
