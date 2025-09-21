# utils/billing.py
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Max
from .models import BillTransaction, BottlePricing
from django.utils.timezone import make_aware
from collections import Counter


Q2 = Decimal('0.01')

def _q2(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_UP)
 
# def compute_totals(quantity: int,
#                    price_per_bottle: Decimal,
#                    discount_pct: Decimal = Decimal('0'),
#                    gst_pct: Decimal = Decimal('18')) -> dict:
#     """
#     Returns dict: subtotal, discount_pct, discount_amount,
#                   taxable, gst_pct, gst_amount, final
#     Business rule: GST is applied on (subtotal - discount) i.e., taxable value.
#     """
#     qty = int(quantity or 0)
#     price = Decimal(price_per_bottle)
#     d = Decimal(discount_pct or 0)
#     g = Decimal(gst_pct or 0)

#     if d < 0 or d > 100:
#         raise ValueError("Discount must be between 0 and 100.")
#     if g < 0 or g > 100:
#         raise ValueError("GST must be between 0 and 100.")

#     subtotal = _q2(Decimal(qty) * price)
#     discount_amount = _q2((subtotal * d) / Decimal('100'))
#     taxable = _q2(subtotal - discount_amount)
#     gst_amount = _q2((taxable * g) / Decimal('100'))
#     final = _q2(taxable + gst_amount)

#     return {
#         'subtotal': subtotal,
#         'discount_pct': d,
#         'discount_amount': discount_amount,
#         'taxable': taxable,
#         'gst_pct': g,
#         'gst_amount': gst_amount,
#         'final': final,
#     }


def create_default_users():
    from django.contrib.auth.models import User
    ADMIN_USERNAME = 'Rg'
    ADMIN_PASSWORD = 'Rg@110'
    DELIVERY_USERNAME = 'delivery'
    DELIVERY_PASSWORD = 'boy@123'
    if not User.objects.filter(username=ADMIN_USERNAME).exists():
        User.objects.create_superuser(ADMIN_USERNAME, 'admin@example.com', ADMIN_PASSWORD)
    if not User.objects.filter(username=DELIVERY_USERNAME).exists():
        User.objects.create_user(DELIVERY_USERNAME, 'delivery@example.com', DELIVERY_PASSWORD)
        

def fiscal_year_start(dt: date):
    """
    Given a date (or datetime), return the fiscal year start date (April 1) for that date's fiscal year.
    E.g. if dt = 2025-09-01 -> returns date(2025, 4, 1)
          if dt = 2025-02-01 -> returns date(2024, 4, 1)
    """
    if hasattr(dt, "date"):
        dt = dt.date()
    year = dt.year
    if dt.month <= 3:  # Jan-Mar -> belong to previous fiscal year start Apr 1 (year-1)
        year = year - 1
    return date(year, 4, 1)

def fiscal_year_end(dt: date):
    start = fiscal_year_start(dt)
    return date(start.year + 1, 3, 31)

def get_next_challan_number():
    """
    Determine next challan number for the current fiscal year across BillTransaction.challan_number.
    The sequence starts at 0 each fiscal year and increments by 1 for each assigned challan.
    """
    today = date.today()
    start = fiscal_year_start(today)
    end = fiscal_year_end(today)
    # BillTransaction.created_at is a datetime; filter between start and end (inclusive)
    # To avoid timezone issues, use aware datetimes if needed
    start_dt = datetime(start.year, start.month, start.day)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
    # No direct timezone conversion here — if you store aware datetimes, you may make_aware
    # Use Max to find the current maximum challan_number for this fiscal year
    qs = BillTransaction.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    max_val = qs.aggregate(max_ch=Max('challan_number'))['max_ch']
    if max_val is None:
        return 0
    return int(max_val) + 1

def compute_totals(quantity: int, price_per_bottle: Decimal, discount_pct: Decimal, gst_pct: Decimal):
    """
    Backwards-compatible helper that computes totals using quantity * price_per_bottle.
    Kept for compatibility but for accurate itemized bills use compute_totals_from_subtotal.
    """
    D = Decimal
    quant = D(quantity)
    subtotal = (quant * D(price_per_bottle)).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    discount_pct = (D(discount_pct) or D('0')).quantize(D('0.01'))
    discount_amount = (subtotal * discount_pct / D('100')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    taxable = (subtotal - discount_amount).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    gst_pct = (D(gst_pct) or D('0')).quantize(D('0.01'))
    gst_amount = (taxable * gst_pct / D('100')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    final = (taxable + gst_amount).quantize(D('0.01'), rounding=ROUND_HALF_UP)

    return {
        'subtotal': subtotal,
        'discount_pct': discount_pct,
        'discount_amount': discount_amount,
        'taxable': taxable,
        'gst_pct': gst_pct,
        'gst_amount': gst_amount,
        'final': final
    }

def compute_totals_from_subtotal(subtotal: Decimal, discount_pct: Decimal, gst_pct: Decimal):
    """
    Preferred for itemized bills: compute discount/gst/final using a given subtotal (sum of row amounts).
    """
    D = Decimal
    subtotal = (D(subtotal) or D('0')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    discount_pct = (D(discount_pct) or D('0')).quantize(D('0.01'))
    discount_amount = (subtotal * discount_pct / D('100')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    taxable = (subtotal - discount_amount).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    gst_pct = (D(gst_pct) or D('0')).quantize(D('0.01'))
    gst_amount = (taxable * gst_pct / D('100')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    final = (taxable + gst_amount).quantize(D('0.01'), rounding=ROUND_HALF_UP)

    return {
        'subtotal': subtotal,
        'discount_pct': discount_pct,
        'discount_amount': discount_amount,
        'taxable': taxable,
        'gst_pct': gst_pct,
        'gst_amount': gst_amount,
        'final': final
    }
    

def build_transaction_rows(transactions, admin_client):
    """
    Expand transactions into rows per (transaction, category).
    """
    rows = []
    subtotal = Decimal('0.00')

    for txn in transactions:
        # Count bottles per category
        category_counts = Counter(b.category.name for b in txn.bottles.all())

        for cat_name, qty in category_counts.items():
            # Rate: category-specific if available, else default
            first_bottle = txn.bottles.filter(category__name=cat_name).first()
            if first_bottle and hasattr(first_bottle.category, 'price'):
                rate = first_bottle.category.price or BottlePricing.get_solo().price
            else:
                rate = BottlePricing.get_solo().price

            amount = (Decimal(qty) * Decimal(rate)).quantize(Decimal('0.01'))
            subtotal += amount

            rows.append({
                'txn': txn,
                'date': txn.custom_date or txn.date,
                'gas': cat_name,
                'challan_no': None,  # set later
                'hsn': admin_client.hsn_code if admin_client and admin_client.hsn_code else '28044090',
                'qty': qty,
                'cum': admin_client.cum_value if admin_client and admin_client.cum_value else Decimal('7.00'),
                'total_qty': qty,
                'rate': rate,
                'amount': amount,
            })

    return rows, subtotal


def number_to_words(number):
    """
    Convert a number to words in Indian format.
    Example: 415 -> "four hundred and fifteen rupees"
    """
    if number == 0:
        return "zero rupees"
    
    # Convert to integer (remove decimal part)
    num = int(number)
    
    # Define word mappings
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", 
             "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    
    def convert_hundreds(n):
        result = ""
        
        # Handle hundreds
        if n >= 100:
            result += ones[n // 100] + " hundred"
            n %= 100
            if n > 0:
                result += " and "
        
        # Handle tens and ones
        if n >= 20:
            result += tens[n // 10]
            if n % 10 > 0:
                result += " " + ones[n % 10]
        elif n >= 10:
            result += teens[n - 10]
        elif n > 0:
            result += ones[n]
        
        return result
    
    def convert_lakhs(n):
        if n >= 100000:
            lakhs = n // 100000
            remainder = n % 100000
            result = convert_hundreds(lakhs) + " lakh"
            if remainder > 0:
                result += " " + convert_hundreds(remainder)
            return result
        else:
            return convert_hundreds(n)
    
    def convert_crores(n):
        if n >= 10000000:
            crores = n // 10000000
            remainder = n % 10000000
            result = convert_hundreds(crores) + " crore"
            if remainder > 0:
                result += " " + convert_lakhs(remainder)
            return result
        else:
            return convert_lakhs(n)
    
    # Convert the number
    words = convert_crores(num)
    
    # Add "rupees" at the end
    return words + " rupees"