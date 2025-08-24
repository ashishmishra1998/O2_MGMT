# utils/billing.py
from decimal import Decimal, ROUND_HALF_UP

Q2 = Decimal('0.01')

def _q2(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_UP)

def compute_totals(quantity: int,
                   price_per_bottle: Decimal,
                   discount_pct: Decimal = Decimal('0'),
                   gst_pct: Decimal = Decimal('18')) -> dict:
    """
    Returns dict: subtotal, discount_pct, discount_amount,
                  taxable, gst_pct, gst_amount, final
    Business rule: GST is applied on (subtotal - discount) i.e., taxable value.
    """
    qty = int(quantity or 0)
    price = Decimal(price_per_bottle)
    d = Decimal(discount_pct or 0)
    g = Decimal(gst_pct or 0)

    if d < 0 or d > 100:
        raise ValueError("Discount must be between 0 and 100.")
    if g < 0 or g > 100:
        raise ValueError("GST must be between 0 and 100.")

    subtotal = _q2(Decimal(qty) * price)
    discount_amount = _q2((subtotal * d) / Decimal('100'))
    taxable = _q2(subtotal - discount_amount)
    gst_amount = _q2((taxable * g) / Decimal('100'))
    final = _q2(taxable + gst_amount)

    return {
        'subtotal': subtotal,
        'discount_pct': d,
        'discount_amount': discount_amount,
        'taxable': taxable,
        'gst_pct': g,
        'gst_amount': gst_amount,
        'final': final,
    }
