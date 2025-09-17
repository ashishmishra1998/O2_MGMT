from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.db.models.signals import post_migrate
from django.contrib.auth.models import User
from django.http import HttpResponse
from .forms import ClientForm, AddBottlesForm
from .models import Client
from .forms import TransactionForm, AdminProfileForm, BottlePricingForm, ClientForm, AddBottlesForm, BottleCategoryForm
from .models import Transaction, Bottle, Bill, BillTransaction, TransactionPhoto, BottleCategory
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponseForbidden
from .models import BottlePricing
from .forms import BottlePricingForm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
from django.db.models import Q
from datetime import datetime
from decimal import Decimal
from .utils import compute_totals, get_next_challan_number, compute_totals_from_subtotal, build_transaction_rows
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from django.db import transaction as db_transaction
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


# Ensure admin and delivery boy users exist
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'Admin@123'
DELIVERY_USERNAME = 'delivery'
DELIVERY_PASSWORD = 'boy@123'

# def create_default_users():
#     if not User.objects.filter(username=ADMIN_USERNAME).exists():
#         User.objects.create_superuser(ADMIN_USERNAME, 'admin@example.com', ADMIN_PASSWORD)
#     if not User.objects.filter(username=DELIVERY_USERNAME).exists():
#         User.objects.create_user(DELIVERY_USERNAME, 'delivery@example.com', DELIVERY_PASSWORD)

# create_default_users()

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user_type = request.POST.get('user_type')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            if user_type == 'admin':
                return redirect('admin_dashboard')
            elif user_type == 'delivery':
                return redirect('delivery_dashboard')
        else:
            return render(request, 'login.html', {'error': 'Invalid credentials'})
    return render(request, 'login.html')

def admin_dashboard(request):
    total_bottles = Bottle.objects.count()
    in_stock = Bottle.objects.filter(status='in_stock').count()
    delivered = Bottle.objects.filter(status='delivered').count()

    # Pending is same as delivered, since those are bottles with clients
    pending = delivered  

    # Returned count = number of bottles ever returned via transactions
    returned = (
        Transaction.objects.filter(transaction_type='returned')
        .values_list('bottles', flat=True)
        .distinct()
        .count()
    )

    recent_transactions = Transaction.objects.prefetch_related('bottles').order_by('-date')[:5]

    return render(request, 'admin_dashboard.html', {
        'total_bottles': total_bottles,
        'delivered': delivered,
        'returned': returned,
        'in_stock': in_stock,
        'pending': pending,
        'recent_transactions': recent_transactions,
    })



def delivery_dashboard(request):
    # Bottles delivered by this user
    delivered = sum(
        t.bottles.count()
        for t in Transaction.objects.filter(delivered_by=request.user, transaction_type='delivered')
    )

    # Bottles returned (transactions created as 'returned')
    returned = sum(
        t.bottles.count()
        for t in Transaction.objects.filter(delivered_by=request.user, transaction_type='returned')
    )

    pending = delivered - returned  # Bottles still with clients

    recent_transactions = Transaction.objects.filter(delivered_by=request.user).order_by('-date')[:5]

    return render(request, 'delivery_dashboard.html', {
        'delivered': delivered,
        'returned': returned,
        'pending': pending,
        'recent_transactions': recent_transactions,
    })

@staff_member_required
def client_create(request):
    if request.method == 'POST':
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('client_list')
    else:
        form = ClientForm()
    return render(request, 'client_create.html', {'form': form})

def client_list(request):
    query = request.GET.get('q', '')
    if query:
        clients = Client.objects.filter(name__icontains=query)
    else:
        clients = Client.objects.all()
    # Add stats for each client
    from .models import Transaction
    client_stats = []
    for client in clients:
        delivered_bottles = sum(
            t.bottles.count() for t in Transaction.objects.filter(client=client, transaction_type='delivered')
        )
        # Count bottles for returned transactions
        returned_bottles = sum(
            t.bottles.count() for t in Transaction.objects.filter(client=client, transaction_type='returned')
        )
        pending_bottles = delivered_bottles - returned_bottles
        
        # Total bottles in delivered transactions that are not yet billed
        pending_bill_bottles = sum(
            t.bottles.count() for t in Transaction.objects.filter(
                client=client,
                transaction_type='delivered',
                billed=False
            )
        )
        client_stats.append({
            'client': client,
            'delivered': delivered_bottles,
            'returned': returned_bottles,
            'pending': pending_bottles,
            'pending_bill_bottles': pending_bill_bottles,
        })
    return render(request, 'client_list.html', {'clients': clients, 'query': query, 'client_stats': client_stats})  

@login_required
def transaction_create(request):
    transaction_type = request.GET.get('transaction_type')
    if not transaction_type:
        # Show a simple form to select transaction type
        return render(request, 'transaction_type_select.html')
    message = None
    if request.method == 'POST':
        form = TransactionForm(request.POST, request.FILES, transaction_type=transaction_type)
        if form.is_valid():
            transaction = form.save(commit=False)
            transaction.delivered_by = request.user
            transaction.save()
            form.save_m2m()
            
            # Save multiple photos
            photos = request.FILES.getlist('photos')
            for photo in photos:
                print(f"Saving photo: {photo.name}")
                TransactionPhoto.objects.create(transaction=transaction, image=photo)

            # Update bottle status
            bottles = transaction.bottles.all()
            if transaction.transaction_type == 'delivered':
                bottles.update(status='delivered')
            elif transaction.transaction_type == 'returned':
                bottles.update(status='in_stock')
            return redirect('transaction_list')
    else:
        form = TransactionForm(transaction_type=transaction_type)
        if not form.fields['bottles'].queryset.exists():
            if transaction_type == 'delivered':
                message = 'No bottles available in stock for delivery.'
            elif transaction_type == 'returned':
                message = 'No bottles currently with clients for return.'
    return render(request, 'transaction_create.html', {'form': form, 'transaction_type': transaction_type, 'message': message})

@login_required
def transaction_list(request):
    if request.user.username == 'delivery':
        transactions = Transaction.objects.filter(delivered_by=request.user)
    else:
        transactions = Transaction.objects.all()
    
    transactions = transactions.order_by('-date')

    # Filtering
    client_id = request.GET.get('client')
    if client_id:
        transactions = transactions.filter(client_id=client_id)
    transaction_type = request.GET.get('type')
    if transaction_type:
        transactions = transactions.filter(transaction_type=transaction_type)
    transactions = transactions.prefetch_related('bottles')
    return render(request, 'transaction_list.html', {
        'transactions': transactions,
        'clients': Client.objects.all(),
        'selected_client': client_id,
        'selected_type': transaction_type,
    })

def reports_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('You do not have permission to view this page.')
    from django.db.models import Count
    import json
    user = request.user
    is_admin = user.is_staff
    client_id = request.GET.get('client')
    transactions = Transaction.objects.all()
    clients = Client.objects.all()
    if client_id:
        transactions = transactions.filter(client_id=client_id)
    # Date ranges
    from django.utils import timezone
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)
    # Stats
    def count_stats(qs):
        return {
            'delivered': qs.filter(transaction_type='delivered').count(),
            'returned': qs.filter(transaction_type='returned').count(),
        }
    stats = {
        'week': count_stats(transactions.filter(date__gte=week_ago)),
        'month': count_stats(transactions.filter(date__gte=month_ago)),
        'year': count_stats(transactions.filter(date__gte=year_ago)),
        'overall': count_stats(transactions),
    }
    # Prepare data for Chart.js
    chart_labels = ['Week', 'Month', 'Year', 'Overall']
    delivered_data = [stats['week']['delivered'], stats['month']['delivered'], stats['year']['delivered'], stats['overall']['delivered']]
    returned_data = [stats['week']['returned'], stats['month']['returned'], stats['year']['returned'], stats['overall']['returned']]
    return render(request, 'reports.html', {
        'is_admin': is_admin,
        'clients': clients,
        'selected_client': client_id,
        'chart_labels': json.dumps(chart_labels),
        'delivered_data': json.dumps(delivered_data),
        'returned_data': json.dumps(returned_data),
        'stats': stats,
    })

@staff_member_required
def inventory_view(request):
    status = request.GET.get('status', '')
    code_query = request.GET.get('q', '')
    bottles = Bottle.objects.all().order_by('code')
    category_id = request.GET.get('category', '')

    if status:
        bottles = bottles.filter(status=status)
    if code_query:
        bottles = bottles.filter(code__icontains=code_query)
    if category_id: 
        bottles = bottles.filter(category_id=category_id)

    categories = BottleCategory.objects.all()
    total = bottles.count()
    in_stock = bottles.filter(status='in_stock').count()
    delivered = bottles.filter(status='delivered').count()
    returned = bottles.filter(status='returned').count()
    return render(request, 'inventory.html', {
        'bottles': bottles,
        'total': total,
        'in_stock': in_stock,
        'delivered': delivered,
        'returned': returned,
        'status': status,
        'code_query': code_query,
        'categories': categories,
        'selected_category': category_id,
    })

@staff_member_required
def add_bottles_view(request):
    if request.method == 'POST':
        form = AddBottlesForm(request.POST)
        if form.is_valid():
            series = form.cleaned_data['series'].strip().upper()
            start = form.cleaned_data['start']
            end = form.cleaned_data['end']
            created = 0
            duplicates = []
            for i in range(start, end + 1):
                code = f"{series}-{i}"
                if not Bottle.objects.filter(code=code).exists():
                    category = form.cleaned_data['category']
                    Bottle.objects.create(code=code, status='in_stock', category=category)
                    created += 1
                else:
                    duplicates.append(code)
            if created:
                messages.success(request, f"{created} bottles ({series}-{start} to {series}-{end}) added to inventory.")
            if duplicates:
                messages.warning(request, f"Skipped duplicates: {', '.join(duplicates)}")
            return redirect('inventory')
    else:
        form = AddBottlesForm()
    return render(request, 'add_bottles.html', {'form': form})

def calculate_bill(total_amount, discount_percentage=Decimal("0"), gst_percentage=Decimal("18")):
    """
    Calculate discount, GST, and final amount.
    Returns dictionary with breakdown.
    """
    discount_amount = (total_amount * discount_percentage / Decimal("100")).quantize(Decimal("0.01"))
    subtotal_after_discount = total_amount - discount_amount

    gst_amount = (subtotal_after_discount * gst_percentage / Decimal("100")).quantize(Decimal("0.01"))
    final_amount = subtotal_after_discount + gst_amount

    return {
        "discount_percentage": discount_percentage,
        "discount_amount": discount_amount,
        "gst_percentage": gst_percentage,
        "gst_amount": gst_amount,
        "final_amount": final_amount,
    }
    
@staff_member_required
def bottle_photos_view(request, code):
    bottle = Bottle.objects.get(code=code)
    transactions = Transaction.objects.filter(bottle=bottle).order_by('-date')
    return render(request, 'bottle_photos.html', {'bottle': bottle, 'transactions': transactions})

@staff_member_required
def pricing_view(request):
    pricing = BottlePricing.get_solo()
    if request.method == 'POST':
        form = BottlePricingForm(request.POST, instance=pricing)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bottle price updated successfully.')
            return redirect('pricing')
    else:
        form = BottlePricingForm(instance=pricing)
    return render(request, 'pricing.html', {'form': form, 'pricing': pricing})

@staff_member_required
def custom_billing_view(request, client_id):
    """View client transactions for custom billing"""
    client = get_object_or_404(Client, id=client_id)
    
    # Get date filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    transaction_type = request.GET.get('transaction_type', '')
    
    # Get all transactions for this client
    transactions = Transaction.objects.filter(client=client, transaction_type='delivered').order_by('-date')
    
    # Apply filters
    if start_date:
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            transactions = transactions.filter(date__date__gte=start_date)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            transactions = transactions.filter(date__date__lte=end_date)
        except ValueError:
            pass
    
    if transaction_type:
        transactions = transactions.filter(transaction_type=transaction_type)
    
    # Group transactions by date
    transactions_by_date = {}
    for transaction in transactions:
        date_key = transaction.date.strftime('%Y-%m-%d')
        if date_key not in transactions_by_date:
            transactions_by_date[date_key] = []
        transactions_by_date[date_key].append(transaction)
    
    # Get pricing
    price = BottlePricing.get_solo().price
    
    # Get already custom billed transactions
    custom_billed_transactions = set()
    custom_bills = Bill.objects.filter(client=client, bill_type='custom')
    for bill in custom_bills:
        custom_billed_transactions.update(
            bill.bill_transactions.values_list('transaction_id', flat=True)
        )
    
    context = {
        'client': client,
        'transactions_by_date': transactions_by_date,
        'price': price,
        'custom_billed_transactions': custom_billed_transactions,
        'start_date': start_date,
        'end_date': end_date,
        'transaction_type': transaction_type,
    }
    
    return render(request, 'custom_billing.html', context)

@staff_member_required
def create_custom_bill(request, client_id):
    if request.method != 'POST':
        return redirect('custom_billing', client_id=client_id)

    client = get_object_or_404(Client, id=client_id)
    selected_transaction_ids = request.POST.getlist('selected_transactions')
    if not selected_transaction_ids:
        messages.error(request, 'Please select at least one transaction to bill.')
        return redirect('custom_billing', client_id=client_id)

    try:
        discount_percentage = Decimal(request.POST.get('discount', '0').strip() or '0')
        gst_percentage = Decimal(request.POST.get('gst', '18').strip() or '18')
    except Exception:
        messages.error(request, 'Invalid discount or GST value.')
        return redirect('custom_billing', client_id=client_id)

    selected_transactions = Transaction.objects.filter(id__in=selected_transaction_ids, client=client).order_by('date')
    # prevent double custom-billing
    already_custom_ids = BillTransaction.objects.filter(bill__client=client, bill__bill_type='custom').values_list('transaction_id', flat=True)
    already_billed = [t for t in selected_transactions if t.id in already_custom_ids]
    if already_billed:
        messages.error(request, f"Some transactions are already custom billed: {', '.join(str(t.id) for t in already_billed)}")
        return redirect('custom_billing', client_id=client_id)

    # Build itemized rows and compute subtotal correctly from per-row rates
    transaction_rows, subtotal = build_transaction_rows(selected_transactions, admin_client)


    # Compute totals from subtotal (accurately reflects row sums)
    totals = compute_totals_from_subtotal(subtotal=subtotal, discount_pct=discount_percentage, gst_pct=gst_percentage)

    # Create bill (legacy total_amount set to subtotal)
    with db_transaction.atomic():
        bill = Bill.objects.create(
            client=client,
            delivered_bottles=sum(r['qty'] for r in transaction_rows),
            returned_bottles=0,
            pending_bottles=sum(r['qty'] for r in transaction_rows),
            price_per_bottle=BottlePricing.get_solo().price,

            total_amount=totals['subtotal'],

            subtotal_amount=totals['subtotal'],
            discount_percentage=totals['discount_pct'],
            discount_amount=totals['discount_amount'],
            taxable_amount=totals['taxable'],
            gst_percentage=totals['gst_pct'],
            gst_amount=totals['gst_amount'],
            final_amount=totals['final'],

            generated_by=request.user,
            bill_type='custom',
            description=request.POST.get('description', 'Custom bill for selected transactions')
        )

        # Assign challan numbers and persist BillTransaction rows
        next_challan = get_next_challan_number()
        bt_objs = []
        for txn in selected_transactions:
            bt_objs.append(BillTransaction(bill=bill, transaction=txn, challan_number=next_challan))
            for row in transaction_rows:
                if row['txn'] == txn:
                    row['challan_no'] = next_challan
            next_challan += 1
        BillTransaction.objects.bulk_create(bt_objs)

        # mark selected txns billed
        selected_transaction_ids_list = [t.id for t in selected_transactions]
        Transaction.objects.filter(id__in=selected_transaction_ids_list).update(billed=True)

    # Sort rows by date ascending for display
    transaction_rows.sort(key=lambda x: x['date'])

    # prepare context fields for template
    cgst_amount = (totals['gst_amount'] / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_amount'] else Decimal('0.00')
    sgst_amount = cgst_amount
    cgst_percentage = (totals['gst_pct'] / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_pct'] else Decimal('0.00')
    sgst_percentage = cgst_percentage

    context = {
        'client': client,
        'bill': bill,
        'transaction_rows': transaction_rows,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'cgst_percentage': cgst_percentage,
        'sgst_percentage': sgst_percentage,
        'admin_client': Client.objects.filter(role='admin').first(),
    }
    if request.GET.get('format') == 'pdf':
        return generate_pdf_bill(request, context)
    return render(request, 'generate_bill.html', context)


@staff_member_required
def generate_bill(request, client_id, bill_id=None):
    admin_client = Client.objects.filter(role='admin').first()
    client = get_object_or_404(Client, id=client_id)

    if bill_id:
        # Render existing bill
        bill = get_object_or_404(Bill, id=bill_id, client=client)
        bts = BillTransaction.objects.filter(bill=bill).select_related('transaction').order_by('transaction__date', 'created_at')
        txns = [bt.transaction for bt in bts]

        transaction_rows, subtotal = build_transaction_rows(txns, admin_client)
        for row in transaction_rows:
            bt = next(bt for bt in bts if bt.transaction_id == row['txn'].id)
            row['challan_no'] = bt.challan_number
        # Compute totals based on actual subtotal (if bill already has fields, keep them consistent)
        # Use bill fields if present; otherwise compute from subtotal
        if getattr(bill, 'subtotal_amount', None):
            totals = {
                'subtotal': bill.subtotal_amount,
                'discount_pct': bill.discount_percentage,
                'discount_amount': bill.discount_amount,
                'taxable': bill.taxable_amount,
                'gst_pct': bill.gst_percentage,
                'gst_amount': bill.gst_amount,
                'final': bill.final_amount
            }
        else:
            totals = compute_totals_from_subtotal(subtotal=subtotal, discount_pct=bill.discount_percentage or Decimal('0'), gst_pct=bill.gst_percentage or Decimal('0'))

        cgst_amount = (Decimal(totals['gst_amount']) / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_amount'] else Decimal('0.00')
        sgst_amount = cgst_amount
        cgst_percentage = (Decimal(totals['gst_pct']) / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_pct'] else Decimal('0.00')
        sgst_percentage = cgst_percentage

        # ensure rows sorted ascending
        transaction_rows.sort(key=lambda x: x['date'])

        context = {
            'client': client,
            'bill': bill,
            'transaction_rows': transaction_rows,
            'cgst_amount': cgst_amount,
            'sgst_amount': sgst_amount,
            'cgst_percentage': cgst_percentage,
            'sgst_percentage': sgst_percentage,
            'admin_client': admin_client,
        }
        if request.GET.get('format') == 'pdf':
            return generate_pdf_bill(request, context)
        return render(request, 'generate_bill.html', context)

    # ----- Auto bill creation flow -----
    if request.method != 'POST':
        prefill_discount = request.GET.get('discount', '')
        prefill_gst = request.GET.get('gst', '18')
        return render(request, "ask_discount.html", {
            'client': client,
            'prefill_discount': prefill_discount,
            'prefill_gst': prefill_gst
        })

    try:
        discount_percentage = Decimal(request.POST.get("discount", "0").strip() or "0")
        gst_percentage = Decimal(request.POST.get("gst", "18").strip() or "18")
    except Exception:
        messages.error(request, "Invalid discount or GST value.")
        return redirect('generate_bill', client_id=client.id)

    delivered_txns = Transaction.objects.filter(client=client, transaction_type='delivered', billed=False).order_by('date')
    if not delivered_txns.exists():
        messages.warning(request, 'No new delivered transactions to bill for this client.')
        return redirect('client_list')

    # Build rows and compute subtotal using per-transaction rates
    transaction_rows, subtotal = build_transaction_rows(delivered_txns, admin_client)


    totals = compute_totals_from_subtotal(subtotal=subtotal, discount_pct=discount_percentage, gst_pct=gst_percentage)

    with db_transaction.atomic():
        bill = Bill.objects.create(
            client=client,
            delivered_bottles=sum(r['qty'] for r in transaction_rows),
            returned_bottles=0,
            pending_bottles=sum(r['qty'] for r in transaction_rows),
            price_per_bottle=BottlePricing.get_solo().price,

            total_amount=totals['subtotal'],

            subtotal_amount=totals['subtotal'],
            discount_percentage=totals['discount_pct'],
            discount_amount=totals['discount_amount'],
            taxable_amount=totals['taxable'],
            gst_percentage=totals['gst_pct'],
            gst_amount=totals['gst_amount'],
            final_amount=totals['final'],

            generated_by=request.user,
            bill_type='auto'
        )

        # assign challan numbers and create BillTransaction rows
        next_challan = get_next_challan_number()
        bt_objs = []
        for txn in delivered_txns:
            bt_objs.append(BillTransaction(bill=bill, transaction=txn, challan_number=next_challan))
            for row in transaction_rows:
                if row['txn'] == txn:
                    row['challan_no'] = next_challan
            next_challan += 1
        BillTransaction.objects.bulk_create(bt_objs)

        # mark as billed (exclude custom-linked)
        Transaction.objects.filter(client=client, billed=False).exclude(
            id__in=BillTransaction.objects.filter(bill__client=client, bill__bill_type='custom').values_list('transaction_id', flat=True)
        ).update(billed=True)

    # sort rows
    transaction_rows.sort(key=lambda x: x['date'])

    cgst_amount = (totals['gst_amount'] / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_amount'] else Decimal('0.00')
    sgst_amount = cgst_amount
    cgst_percentage = (totals['gst_pct'] / Decimal('2')).quantize(Decimal('0.01')) if totals['gst_pct'] else Decimal('0.00')
    sgst_percentage = cgst_percentage

    context = {
        'client': client,
        'bill': bill,
        'transaction_rows': transaction_rows,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'cgst_percentage': cgst_percentage,
        'sgst_percentage': sgst_percentage,
        'admin_client': admin_client,
    }

    if request.GET.get('format') == 'pdf':
        return generate_pdf_bill(request, context)
    return render(request, 'generate_bill.html', context)
# Helper used above
def default_rate_for_transaction(txn: Transaction):
    """
    Determine per-transaction rate:
    - If the bottle category has price set -> use it
    - Else fallback to BottlePricing.get_solo().price
    """
    if txn.bottles.exists():
        first_bottle = txn.bottles.first()
        if hasattr(first_bottle, 'category') and first_bottle.category:
            cat = first_bottle.category
            if getattr(cat, 'price', None) is not None:
                return cat.price
    return BottlePricing.get_solo().price

def _fmt_money(val):
    try:
        return f"₹{Decimal(val):.2f}"
    except Exception:
        return f"₹{val}"

def generate_pdf_bill(request, context):
    """
    Generate styled PDF bill. Fixes:
    - uses defaults for missing admin_client/address
    - avoids unsupported rupee glyph by using 'Rs.' prefix
    - right-aligns numeric columns and formats decimals to 2 places
    """

    def safe_decimal(val):
        try:
            return Decimal(val or 0).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError):
            return Decimal("0.00")

    def money_str(val):
        d = safe_decimal(val)
        # Use 'Rs.' to avoid unsupported rupee glyph problems in standard fonts
        return f"Rs. {d:.2f}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    styles.add(ParagraphStyle(name="CompanyHeader", alignment=1, fontSize=16, leading=18))
    styles.add(ParagraphStyle(name="InvoiceTitle", alignment=1, fontSize=14, leading=16))
    styles.add(ParagraphStyle(name="LeftSmall", alignment=0, fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="RightSmall", alignment=2, fontSize=9, leading=11))

    admin = context.get("admin_client") or {}
    bill = context.get("bill")
    client = context.get("client")
    tx_rows = context.get("transaction_rows", []) or []
    # ensure rows are sorted ascending by date for the summary sentence
    try:
        tx_rows.sort(key=lambda r: getattr(r["date"], "timestamp", lambda: r["date"])())
    except Exception:
        # fallback: if date is date/datetime, sort directly
        try:
            tx_rows.sort(key=lambda r: r["date"])
        except Exception:
            pass

    # Defaults (same as HTML template defaults)
    default_company = "Rainbow Gases"
    default_address = "NH-48 Sr.No 58, Ansar Market Opp.Amar Trupti Hotel, Ankleshwar, Dist-Bharuch"
    company_name = getattr(admin, "company_name", None) or (admin.get("company_name") if isinstance(admin, dict) else None) or default_company
    company_address = getattr(admin, "address", None) or (admin.get("address") if isinstance(admin, dict) else None) or default_address
    company_email = getattr(admin, "email", None) or (admin.get("email") if isinstance(admin, dict) else None) or "rainbowgases@gmail.com"
    company_contact = getattr(admin, "contact", None) or (admin.get("contact") if isinstance(admin, dict) else None) or ""
    company_gst = getattr(admin, "owner_gst", None) or (admin.get("owner_gst") if isinstance(admin, dict) else None) or ""

    # --- Company header (centered) ---
    elements.append(Paragraph(f"<b>{company_name}</b>", styles["CompanyHeader"]))
    elements.append(Paragraph(company_address, styles["CompanyHeader"]))
    contact_line = company_email
    if company_contact:
        contact_line += " | Contact: " + company_contact
    elements.append(Paragraph(contact_line, styles["CompanyHeader"]))
    if company_gst:
        elements.append(Paragraph(f"GST No: {company_gst}", styles["CompanyHeader"]))
    elements.append(Spacer(1, 12))

    # --- Invoice title & bill metadata (center/right) ---
    elements.append(Paragraph("<b>TAX INVOICE</b>", styles["InvoiceTitle"]))
    elements.append(Spacer(1, 6))
    # Bill metadata (right aligned)
    if bill:
        bill_no = getattr(bill, "id", "")
        bill_date = getattr(bill, "bill_date", None)
        bill_date_str = bill_date.strftime("%d/%m/%Y") if bill_date else ""
        elements.append(Paragraph(f"Bill No: {bill_no}", styles["RightSmall"]))
        elements.append(Paragraph(f"Date: {bill_date_str}", styles["RightSmall"]))
    elements.append(Spacer(1, 10))

    # --- Bill To ---
    if client:
        elements.append(Paragraph("<b>Bill To:</b>", styles["LeftSmall"]))
        elements.append(Paragraph(f"<b>{getattr(client, 'name', '')}</b>", styles["LeftSmall"]))
        client_addr = getattr(client, "address", None) or (client.get("address") if isinstance(client, dict) else "") or ""
        if client_addr:
            elements.append(Paragraph(client_addr, styles["LeftSmall"]))
        client_contact = getattr(client, "contact", None) or (client.get("contact") if isinstance(client, dict) else None) or ""
        if client_contact:
            elements.append(Paragraph(f"Contact: {client_contact}", styles["LeftSmall"]))
        client_gst = getattr(client, "gst_number", None) or (client.get("gst_number") if isinstance(client, dict) else None) or ""
        if client_gst:
            elements.append(Paragraph(f"GST: {client_gst}", styles["LeftSmall"]))
    elements.append(Spacer(1, 8))

    # --- Summary sentence ---
    delivered_bottles = getattr(bill, "delivered_bottles", None) or (bill.subtotal_amount and "")  # fallback
    if tx_rows:
        first_date = tx_rows[0].get("date")
        last_date = tx_rows[-1].get("date")
        try:
            first_date_str = first_date.strftime("%d %B %Y")
            last_date_str = last_date.strftime("%d %B %Y")
        except Exception:
            first_date_str = str(first_date)
            last_date_str = str(last_date)
        elements.append(Paragraph(
            f"This bill covers the supply of a total of {delivered_bottles or ''} gas bottles for the period {first_date_str} to {last_date_str}.",
            styles["LeftSmall"]
        ))
    else:
        bill_date_str = getattr(bill, "bill_date", None)
        bill_date_str = bill_date_str.strftime("%d %B %Y") if bill_date_str else ""
        elements.append(Paragraph(
            f"This bill covers the supply of a total of {delivered_bottles or ''} gas bottles on {bill_date_str}.",
            styles["LeftSmall"]
        ))
    elements.append(Spacer(1, 12))

    # --- Transaction Table ---
    # Header row
    table_data = [
        ["Date", "Gas", "Challan No", "HSN", "QTY", "C.U.M", "Total QTY", "Rate", "Amount"]
    ]

    # Add rows
    for r in tx_rows:
        # safe values
        date_val = r.get("date")
        try:
            date_str = date_val.strftime("%d-%m-%Y") if hasattr(date_val, "strftime") else str(date_val)
        except Exception:
            date_str = str(date_val)
        gas = r.get("gas", "")
        challan_no = r.get("challan_no") if r.get("challan_no") is not None else ""
        hsn = r.get("hsn", "")
        qty = r.get("qty", "")
        cum = r.get("cum", "")
        total_qty = r.get("total_qty", qty)
        rate = safe_decimal(r.get("rate", 0))
        amount = safe_decimal(r.get("amount", 0))

        table_data.append([
            date_str,
            gas,
            str(challan_no),
            str(hsn),
            str(qty),
            f"{safe_decimal(cum):.2f}",
            str(total_qty),
            f"Rs. {rate:.2f}",
            f"Rs. {amount:.2f}",
        ])

    # Totals rows exactly as in template
    subtotal_val = safe_decimal(getattr(bill, "subtotal_amount", context.get("subtotal") or 0))
    table_data.append(["", "", "", "", "", "", "", "Subtotal", f"Rs. {subtotal_val:.2f}"])

    # Discount row
    if getattr(bill, "discount_amount", None) and safe_decimal(bill.discount_amount) != Decimal("0.00"):
        disc_amt = safe_decimal(bill.discount_amount)
        disc_pct = getattr(bill, "discount_percentage", "")
        table_data.append(["", "", "", "", "", "", "", f"Discount ({disc_pct}%)", f"-Rs. {disc_amt:.2f}"])

    # Taxable and GST rows (if applicable)
    taxable_val = safe_decimal(getattr(bill, "taxable_amount", None) or context.get("taxable") or 0)
    gst_amt = safe_decimal(getattr(bill, "gst_amount", None) or context.get("gst_amount") or 0)
    cgst_amt = safe_decimal(context.get("cgst_amount") or (gst_amt / 2))
    sgst_amt = safe_decimal(context.get("sgst_amount") or (gst_amt / 2))
    cgst_pct = context.get("cgst_percentage") or (safe_decimal(getattr(bill, "gst_percentage", 0)) / 2)
    sgst_pct = context.get("sgst_percentage") or (safe_decimal(getattr(bill, "gst_percentage", 0)) / 2)

    if taxable_val and gst_amt:
        table_data.append(["", "", "", "", "", "", "", "Taxable Value", f"Rs. {taxable_val:.2f}"])
        table_data.append(["", "", "", "", "", "", "", f"CGST ({cgst_pct:.2f}%)", f"Rs. {cgst_amt:.2f}"])
        table_data.append(["", "", "", "", "", "", "", f"SGST ({sgst_pct:.2f}%)", f"Rs. {sgst_amt:.2f}"])

    grand_total = safe_decimal(getattr(bill, "final_amount", None) or context.get("final") or (taxable_val + gst_amt))
    table_data.append(["", "", "", "", "", "", "", "Grand Total", f"Rs. {grand_total:.2f}"])

    # Build table — align numeric columns to right (Rate & Amount columns)
    col_count = len(table_data[0])
    col_widths = None  # let ReportLab compute column widths
    table = Table(table_data, hAlign="LEFT", colWidths=col_widths)
    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (7, 0), (7, -1), "RIGHT"),   # Rate column
        ("ALIGN", (8, 0), (8, -1), "RIGHT"),   # Amount column
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ])
    # Emphasize grand total row
    last_row_idx = len(table_data) - 1
    table_style.add("FONTNAME", (7, last_row_idx), (8, last_row_idx), "Helvetica-Bold")
    table_style.add("BACKGROUND", (0, last_row_idx), (-1, last_row_idx), colors.HexColor("#e6f3ea"))
    table.setStyle(table_style)
    elements.append(table)
    elements.append(Spacer(1, 12))

    # --- Payment Details (Bank left, UPI right) ---
    bank_lines = []
    if getattr(admin, "account_holder", None) or (isinstance(admin, dict) and admin.get("account_holder")):
        bank_lines.append(f"Account Holder: {getattr(admin, 'account_holder', None) or admin.get('account_holder')}")
    if getattr(admin, "account_number", None) or (isinstance(admin, dict) and admin.get("account_number")):
        bank_lines.append(f"Account No: {getattr(admin, 'account_number', None) or admin.get('account_number')}")
    if getattr(admin, "ifsc", None) or (isinstance(admin, dict) and admin.get("ifsc")):
        bank_lines.append(f"IFSC: {getattr(admin, 'ifsc', None) or admin.get('ifsc')}")
    if getattr(admin, "branch", None) or (isinstance(admin, dict) and admin.get("branch")):
        bank_lines.append(f"Branch: {getattr(admin, 'branch', None) or admin.get('branch')}")

    for line in bank_lines:
        elements.append(Paragraph(line, styles["LeftSmall"]))

    # UPI details
    upi_vpa = getattr(admin, "vpa", None) or (admin.get("vpa") if isinstance(admin, dict) else None)
    upi_no = getattr(admin, "upi_number", None) or (admin.get("upi_number") if isinstance(admin, dict) else None)
    if upi_vpa or upi_no:
        elements.append(Spacer(1, 6))
        elements.append(Paragraph("<b>UPI Payment</b>", styles["LeftSmall"]))
        if upi_vpa:
            elements.append(Paragraph(f"VPA: {upi_vpa}", styles["LeftSmall"]))
        if upi_no:
            elements.append(Paragraph(f"UPI No: {upi_no}", styles["LeftSmall"]))

    elements.append(Spacer(1, 24))
    elements.append(Paragraph(f"For, {company_name}", styles["LeftSmall"]))
    elements.append(Spacer(1, 36))
    elements.append(Paragraph("Authorized Signatory", styles["LeftSmall"]))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="bill_{getattr(client, "name", "client")}_{getattr(bill, "bill_date", "").strftime("%Y%m%d") if getattr(bill, "bill_date", None) else ""}.pdf"'
    return response

@staff_member_required
def bill_history(request, client_id):
    """View bill history for a specific client"""
    client = get_object_or_404(Client, id=client_id)
    bills = Bill.objects.filter(client=client)
    return render(request, 'bill_history.html', {
        'client': client,
        'bills': bills,
    })

@staff_member_required
def mark_bill_paid(request, bill_id):
    """Mark a bill as paid"""
    from django.utils import timezone
    bill = get_object_or_404(Bill, id=bill_id)
    
    if request.method == 'POST':
        bill.paid = True
        bill.paid_date = timezone.now()
        bill.paid_by = request.user
        bill.save()
        messages.success(request, f'Bill #{bill.id} marked as paid successfully.')
        return redirect('bill_history', client_id=bill.client.id)
    
    return render(request, 'mark_bill_paid.html', {'bill': bill})

@staff_member_required
def delete_bill(request, bill_id):
    """Delete an unpaid bill and restore transactions to unbilled status"""
    bill = get_object_or_404(Bill, id=bill_id)
    
    # Check if bill is paid
    if bill.paid:
        messages.error(request, 'Cannot delete a paid bill.')
        return redirect('bill_history', client_id=bill.client.id)
    
    if request.method == 'POST':
        # Restore transactions to unbilled status
        Transaction.objects.filter(
            client=bill.client,
            billed=True
        ).update(billed=False)
        
        # Delete the bill
        bill.delete()
        messages.success(request, f'Bill #{bill_id} deleted successfully. Transactions restored to unbilled status.')
        return redirect('bill_history', client_id=bill.client.id)
    
    return render(request, 'delete_bill.html', {'bill': bill})

@staff_member_required
def sales_analytics(request):
    """Comprehensive sales analytics dashboard"""
    from django.db.models import Sum, Count, Q
    from django.utils import timezone
    from datetime import datetime, timedelta
    import calendar
    
    # Get date filters
    selected_year = request.GET.get('year', timezone.now().year)
    selected_month = request.GET.get('month', timezone.now().month)
    selected_date = request.GET.get('date', timezone.now().date())
    
    # Convert to integers
    selected_year = int(selected_year)
    selected_month = int(selected_month)
    
    # Current date info
    now = timezone.now()
    current_year = now.year
    current_month = now.month
    current_date = now.date()
    
    # Date ranges
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    year_start = today.replace(month=1, day=1)
    year_end = today.replace(month=12, day=31)
    
    # Get all bills
    all_bills = Bill.objects.all()
    
    # Sales Analytics
    def get_sales_data(bills_qs):
        total_bills = bills_qs.count()
        total_amount = bills_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        
        # Fetch transactions linked to these bills
        transactions = Transaction.objects.filter(
            bill_transactions__bill__in=bills_qs
        ).prefetch_related('bottles')

        # Bottle counts
        delivered_bottles = sum(
            t.bottles.count() for t in transactions if t.transaction_type == 'delivered'
        )
        returned_bottles = sum(
            t.bottles.count() for t in transactions if t.transaction_type == 'returned'
        )
        pending_bottles = delivered_bottles - returned_bottles

        # Payment info (still from Bill)
        paid_bills = bills_qs.filter(paid=True)
        paid_amount = paid_bills.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        unpaid_amount = total_amount - paid_amount

        return {
            'total_bills': total_bills,
            'total_amount': total_amount,
            'total_bottles_delivered': delivered_bottles,
            'total_bottles_returned': returned_bottles,
            'total_pending_bottles': pending_bottles,
            'paid_amount': paid_amount,
            'unpaid_amount': unpaid_amount,
            'payment_rate': (paid_amount / total_amount * 100) if total_amount > 0 else 0
        }

    
    # Daily, Weekly, Monthly, Yearly sales
    daily_sales = get_sales_data(all_bills.filter(bill_date__date=today))
    weekly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[week_start, week_end]))
    monthly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[month_start, month_end]))
    yearly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[year_start, year_end]))
    
    # Selected period sales
    selected_start = datetime(selected_year, selected_month, 1).date()
    selected_end = (selected_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    selected_sales = get_sales_data(all_bills.filter(bill_date__date__range=[selected_start, selected_end]))
    
    # Current stock status
    total_stock = Bottle.objects.count()
    in_stock = Bottle.objects.filter(status='in_stock').count()
    delivered_stock = Bottle.objects.filter(status='delivered').count()
    
    # Calculate percentages
    in_stock_percent = round((in_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    delivered_percent = round((delivered_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    
    # Client-wise analytics
    client_analytics = []
    clients = Client.objects.all()
    for client in clients:
        client_bills = all_bills.filter(client=client)
        transactions = Transaction.objects.filter(
            bill_transactions__bill__in=client_bills
        ).prefetch_related('bottles')

        total_delivered = sum(
            t.bottles.count() for t in transactions if t.transaction_type == 'delivered'
        )
        total_returned = sum(
            t.bottles.count() for t in transactions if t.transaction_type == 'returned'
        )
        total_pending = total_delivered - total_returned
        
        total_amount = client_bills.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        paid_amount = client_bills.filter(paid=True).aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        unpaid_amount = total_amount - paid_amount
        
        client_analytics.append({
            'client': client,
            'total_delivered': total_delivered,
            'total_returned': total_returned,
            'total_pending': total_pending,
            'total_amount': total_amount,
            'paid_amount': paid_amount,
            'unpaid_amount': unpaid_amount,
            'payment_rate': (paid_amount / total_amount * 100) if total_amount > 0 else 0
        })
    
    # Sort clients by total amount (highest first)
    client_analytics.sort(key=lambda x: x['total_amount'], reverse=True)
    
    # Monthly trend data for charts
    monthly_trend = []
    for month in range(1, 13):
        month_start = datetime(selected_year, month, 1).date()
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        
        month_bills = all_bills.filter(bill_date__date__range=[month_start, month_end])
        transactions = Transaction.objects.filter(
            bill_transactions__bill__in=month_bills
        ).prefetch_related('bottles')

        month_amount = month_bills.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        month_bottles = sum(t.bottles.count() for t in transactions if t.transaction_type == 'delivered')

        monthly_trend.append({
            'month': calendar.month_name[month],
            'amount': month_amount,
            'bottles': month_bottles
        })
    
    # Recent transactions
    recent_bills = all_bills.order_by('-bill_date')[:10]
    
    # Top performing clients
    top_clients = sorted(client_analytics, key=lambda x: x['total_amount'], reverse=True)[:5]
    
    # Create year range for dropdown (current year - 2 to current year + 2)
    year_range = list(range(current_year - 2, current_year + 3))
    
    context = {
        'daily_sales': daily_sales,
        'weekly_sales': weekly_sales,
        'monthly_sales': monthly_sales,
        'yearly_sales': yearly_sales,
        'selected_sales': selected_sales,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'current_year': current_year,
        'current_month': current_month,
        'total_stock': total_stock,
        'in_stock': in_stock,
        'delivered_stock': delivered_stock,
        'in_stock_percent': in_stock_percent,
        'delivered_percent': delivered_percent,
        'client_analytics': client_analytics,
        'monthly_trend': monthly_trend,
        'recent_bills': recent_bills,
        'top_clients': top_clients,
        'today': today,
        'week_start': week_start,
        'week_end': week_end,
        'month_start': month_start,
        'month_end': month_end,
        'year_range': year_range,
    }
    
    return render(request, 'sales_analytics.html', context)

def logout_view(request):
    logout(request)
    return redirect('login')

def debug_photos(request):
    """Debug view to test photo URLs"""
    transactions = Transaction.objects.all()[:5]
    photo_info = []
    for t in transactions:
        photo_info.append({
            'id': t.id,
            'photo_path': str(t.photo),
            'photo_url': t.photo.url if t.photo else 'No photo',
            'photo_exists': t.photo and t.photo.storage.exists(t.photo.name) if t.photo else False,
        })
    return render(request, 'debug_photos.html', {'photo_info': photo_info})

@staff_member_required
def admin_profile(request):
    """Admin profile management view"""
    from .models import Client
    from .forms import AdminProfileForm
    
    # Get or create admin profile
    admin_client, created = Client.objects.get_or_create(
        role='admin',
        defaults={
            'name': 'Admin Profile',
            'contact': '0000000000',
            'email': 'admin@example.com',
            'address': 'Admin Address'
        }
    )
    
    if request.method == 'POST':
        form = AdminProfileForm(request.POST, request.FILES, instance=admin_client)
        if form.is_valid():
            form.save()
            messages.success(request, 'Admin profile updated successfully.')
            return redirect('admin_profile')
    else:
        form = AdminProfileForm(instance=admin_client)
    
    return render(request, 'admin_profile.html', {'form': form, 'admin_client': admin_client})

@staff_member_required
def category_list(request):
    """List all bottle categories"""
    from .models import BottleCategory
    categories = BottleCategory.objects.all()
    return render(request, 'category_list.html', {'categories': categories})

@staff_member_required
def category_create(request):
    """Create a new bottle category"""
    from .models import BottleCategory
    from .forms import BottleCategoryForm
    
    if request.method == 'POST':
        form = BottleCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category created successfully.')
            return redirect('category_list')
    else:
        form = BottleCategoryForm()
    
    return render(request, 'category_form.html', {'form': form, 'edit': False})

@staff_member_required
def category_edit(request, category_id):
    """Edit a bottle category"""
    from .models import BottleCategory
    from .forms import BottleCategoryForm
    
    category = get_object_or_404(BottleCategory, id=category_id)
    
    if request.method == 'POST':
        form = BottleCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated successfully.')
            return redirect('category_list')
    else:
        form = BottleCategoryForm(instance=category)
    
    return render(request, 'category_form.html', {'form': form, 'category': category, 'edit': True})
