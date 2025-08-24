from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import HttpResponse
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
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from io import BytesIO
from django.db.models import Q
from datetime import datetime
from decimal import Decimal
from .utils import compute_totals

# Ensure admin and delivery boy users exist
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'Admin@123'
DELIVERY_USERNAME = 'delivery'
DELIVERY_PASSWORD = 'boy@123'

def create_default_users():
    if not User.objects.filter(username=ADMIN_USERNAME).exists():
        User.objects.create_superuser(ADMIN_USERNAME, 'admin@example.com', ADMIN_PASSWORD)
    if not User.objects.filter(username=DELIVERY_USERNAME).exists():
        User.objects.create_user(DELIVERY_USERNAME, 'delivery@example.com', DELIVERY_PASSWORD)

create_default_users()

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
    delivered = Bottle.objects.filter(status='delivered').count()
    returned = Bottle.objects.filter(status='returned').count()
    in_stock = Bottle.objects.filter(status='in_stock').count()
    pending = delivered  # Bottles delivered but not yet returned
    recent_transactions = Transaction.objects.order_by('-date')[:5]
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
    delivered = Transaction.objects.filter(delivered_by=request.user, transaction_type='delivered').count()
    returned = Transaction.objects.filter(delivered_by=request.user, transaction_type='returned').count()
    pending = delivered - returned
    recent_transactions = Transaction.objects.filter(delivered_by=request.user).order_by('-date')[:5]
    return render(request, 'delivery_dashboard.html', {
        'delivered': delivered,
        'returned': returned,
        'pending': pending,
        'recent_transactions': recent_transactions,
    })

@user_passes_test(lambda u: u.username == 'admin')
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
        
    # show lateset first
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
    if request.user.username != 'admin':
        return HttpResponseForbidden('You do not have permission to view this page.')
    from django.db.models import Count
    import json
    user = request.user
    is_admin = user.username == 'admin'
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

    # Parse discount/gst (safe defaults)
    try:
        discount_percentage = Decimal(request.POST.get('discount', '0').strip() or '0')
        gst_percentage = Decimal(request.POST.get('gst', '18').strip() or '18')
    except Exception:
        messages.error(request, 'Invalid discount or GST value.')
        return redirect('custom_billing', client_id=client_id)

    # Selected transactions
    selected_transactions = Transaction.objects.filter(
        id__in=selected_transaction_ids, client=client
    )

    # Prevent double custom-billing
    custom_billed_transactions = set()
    custom_bills = Bill.objects.filter(client=client, bill_type='custom')
    for bill in custom_bills:
        custom_billed_transactions.update(
            bill.bill_transactions.values_list('transaction_id', flat=True)
        )
    already_billed = [t for t in selected_transactions if t.id in custom_billed_transactions]
    if already_billed:
        messages.error(
            request,
            f'Some transactions are already custom billed: {", ".join([str(t.id) for t in already_billed])}'
        )
        return redirect('custom_billing', client_id=client_id)

    # Quantities & price
    total_bottles_delivered = sum(t.bottles.count() for t in selected_transactions)
    price = BottlePricing.get_solo().price  # Decimal

    # Totals (GST on taxable)
    totals = compute_totals(
        quantity=total_bottles_delivered,
        price_per_bottle=price,
        discount_pct=discount_percentage,
        gst_pct=gst_percentage
    )

    # Create custom bill
    bill = Bill.objects.create(
        client=client,
        delivered_bottles=total_bottles_delivered,
        returned_bottles=0,
        pending_bottles=total_bottles_delivered,
        price_per_bottle=price,

        # legacy subtotal
        total_amount=totals['subtotal'],

        # normalized
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

    # Link transactions
    bill_transactions = [BillTransaction(bill=bill, transaction=txn) for txn in selected_transactions]
    BillTransaction.objects.bulk_create(bill_transactions)

    # Mark as billed
    selected_transactions.update(billed=True)

    messages.success(request, f'Custom bill created successfully for {total_bottles_delivered} bottles.')
    return redirect('generate_bill', client_id=client_id, bill_id=bill.id)

@staff_member_required
def generate_bill(request, client_id, bill_id=None):
    """Generate bill - modified to handle both auto and custom bills"""
    client = get_object_or_404(Client, id=client_id)
    admin_client = Client.objects.filter(role='admin').first()

    if bill_id:
        bill = get_object_or_404(Bill, id=bill_id, client=client)
        context = {
            'client': client,
            'delivered': bill.delivered_bottles,
            'returned': bill.returned_bottles,
            'pending': bill.pending_bottles,
            'price': bill.price_per_bottle,
            'total': bill.total_amount,  # legacy subtotal
            'bill': bill,
            'bill_date': bill.bill_date,
            'is_custom': bill.bill_type == 'custom',
            'discount': bill.discount_percentage,
            'final_amount': bill.final_amount,
            'admin_client': admin_client,  # pass admin details
        }
        if request.GET.get('format') == 'pdf':
            return generate_pdf_bill(request, context)
        return render(request, 'generate_bill.html', context)

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
    except Exception:
        messages.error(request, "Invalid discount value.")
        return redirect('generate_bill', client_id=client.id)

    try:
        gst_percentage = Decimal(request.POST.get("gst", "18").strip() or "18")
    except Exception:
        messages.error(request, "Invalid GST value.")
        return redirect('generate_bill', client_id=client.id)
    
    # Original automated billing logic
    delivered_txns = Transaction.objects.filter(
        client=client, transaction_type='delivered', billed=False
    )
    delivered_txn_count = delivered_txns.count()
    delivered_bottles = sum(t.bottles.count() for t in delivered_txns)  # ✅ correct

    if delivered_txn_count == 0 or delivered_bottles == 0:
        messages.warning(request, 'No new delivered transactions to bill for this client.')
        return redirect('client_list')

    returned_txns = Transaction.objects.filter(
        client=client, transaction_type='returned', billed=False
    )
    returned_bottles = sum(t.bottles.count() for t in returned_txns)
    pending_bottles = delivered_bottles - returned_bottles

    price = BottlePricing.get_solo().price  # Decimal

    # Totals (GST on taxable = subtotal - discount)
    totals = compute_totals(
        quantity=delivered_bottles,
        price_per_bottle=price,
        discount_pct=discount_percentage,
        gst_pct=gst_percentage
    )

    bill = Bill.objects.create(
        client=client,
        delivered_bottles=delivered_bottles,            # ✅ store bottle count, not txn count
        returned_bottles=returned_bottles,
        pending_bottles=pending_bottles,
        price_per_bottle=price,

        # legacy subtotal (keep name for old template bits)
        total_amount=totals['subtotal'],

        # normalized new fields
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

    # Mark all unbilled as billed (except ones already in custom bills)
    Transaction.objects.filter(
        client=client,
        billed=False
    ).exclude(
        id__in=BillTransaction.objects.filter(
            bill__client=client, bill__bill_type='custom'
        ).values_list('transaction_id', flat=True)
    ).update(billed=True)

    context = {
        'client': client,
        'delivered': delivered_bottles,
        'returned': returned_bottles,
        'pending': pending_bottles,
        'price': price,
        'total': bill.total_amount,
        'bill': bill,
        'bill_date': bill.bill_date,
        'is_custom': False,
        'discount': bill.discount_percentage,
        'final_amount': bill.final_amount,
        'admin_client': admin_client,  # pass admin details
    }
    if request.GET.get('format') == 'pdf':
        return generate_pdf_bill(request, context)
    return render(request, 'generate_bill.html', context)

def _fmt_money(val):
    try:
        return f"₹{Decimal(val):.2f}"
    except Exception:
        return f"₹{val}"


def generate_pdf_bill(request, context):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)

    p.setTitle("Bill")
    p.setAuthor("O2 Bottle Management System")
    p.setSubject(f"Bill for {context['client'].name}")
    p.setKeywords("O2 Bottle, Bill, Management")
    p.setFont("Helvetica", 12)

    # Header
    p.drawString(1 * inch, 10 * inch, "O2 Bottle Management System")
    p.drawString(1 * inch, 9.5 * inch, "Bill")

    # Client
    client = context["client"]
    p.drawString(1 * inch, 9 * inch, f"Client: {client.name}")
    p.drawString(1 * inch, 8.5 * inch, f"Address: {client.address}")
    p.drawString(1 * inch, 8 * inch, f"Contact: {client.contact}")
    if getattr(client, "gst_number", None):
        p.drawString(1 * inch, 7.75 * inch, f"GSTIN: {client.gst_number}")

    bill = context["bill"]

    # Bill details
    y = 7.2 * inch
    p.drawString(1 * inch, y, f"Bill Date: {context['bill_date'].strftime('%Y-%m-%d')}")
    y -= 0.3 * inch
    p.drawString(1 * inch, y, f"Bottles Delivered: {bill.delivered_bottles}")
    y -= 0.3 * inch
    p.drawString(1 * inch, y, f"Price per Bottle: {_fmt_money(bill.price_per_bottle)}")

    # Amounts
    subtotal = getattr(bill, "subtotal_amount", context.get("total"))
    discount_pct = getattr(bill, "discount_percentage", Decimal("0"))
    discount_amount = getattr(bill, "discount_amount", Decimal("0"))
    taxable = getattr(bill, "taxable_amount", subtotal)
    gst_pct = getattr(bill, "gst_percentage", Decimal("18"))
    gst_amount = getattr(bill, "gst_amount", Decimal("0"))
    final_amount = getattr(bill, "final_amount", subtotal)

    y -= 0.45 * inch
    p.drawString(1 * inch, y, f"Subtotal: {_fmt_money(subtotal)}")
    y -= 0.3 * inch
    if discount_pct and discount_pct != 0:
        p.drawString(1 * inch, y, f"Discount ({discount_pct}%): -{_fmt_money(discount_amount)}")
        y -= 0.3 * inch
    p.drawString(1 * inch, y, f"Taxable Value: {_fmt_money(taxable)}")
    y -= 0.3 * inch
    p.drawString(1 * inch, y, f"GST ({gst_pct}%): {_fmt_money(gst_amount)}")
    y -= 0.3 * inch
    p.drawString(1 * inch, y, f"Final Payable: {_fmt_money(final_amount)}")
    y -= 0.45 * inch

    p.drawString(1 * inch, y, f"Generated by: {bill.generated_by.username}")
    y -= 0.6 * inch

    # ---------------- Payment Details Section ----------------
    admin_client = context.get("admin_client")
    if admin_client:
        p.setFont("Helvetica-Bold", 12)
        p.setFillColor(colors.white)
        p.setStrokeColor(colors.black)

        # Card header
        p.setFillColor(colors.grey)
        p.rect(1 * inch, y - 0.3 * inch, 6 * inch, 0.35 * inch, fill=1)
        p.setFillColor(colors.white)
        p.drawCentredString(4 * inch, y - 0.1 * inch, "Payment Details")

        # Reset font for details
        y -= 0.6 * inch
        p.setFont("Helvetica", 10)
        p.setFillColor(colors.black)

        # Left column: Bank details
        left_x = 1.1 * inch
        right_x = 3.8 * inch
        col_y = y

        p.setFillColor(colors.blue)
        p.drawString(left_x, col_y, "Bank Transfer")
        p.setFillColor(colors.black)
        col_y -= 0.2 * inch
        p.drawString(left_x, col_y, f"Account Holder: {admin_client.account_holder}")
        col_y -= 0.2 * inch
        p.drawString(left_x, col_y, f"Account No: {admin_client.account_number}")
        col_y -= 0.2 * inch
        p.drawString(left_x, col_y, f"IFSC: {admin_client.ifsc}")
        col_y -= 0.2 * inch
        p.drawString(left_x, col_y, f"Branch: {admin_client.branch}")
        col_y -= 0.2 * inch
        p.drawString(left_x, col_y, f"Type: {admin_client.account_type}")

        # Right column: UPI
        upi_y = y
        p.setFillColor(colors.green)
        p.drawString(right_x, upi_y, "UPI Payment")
        p.setFillColor(colors.black)
        upi_y -= 0.2 * inch
        if getattr(admin_client, "vpa", None):
            p.drawString(right_x, upi_y, f"VPA: {admin_client.vpa}")
            upi_y -= 0.2 * inch
        if getattr(admin_client, "upi_number", None):
            p.drawString(right_x, upi_y, f"UPI No: {admin_client.upi_number}")
            upi_y -= 0.3 * inch

        # QR code (resize ~120px)
        if getattr(admin_client, "upi_qr", None):
            try:
                qr_path = admin_client.upi_qr.path
                qr_img = ImageReader(qr_path)
                p.drawImage(qr_img, right_x, upi_y - 1.2 * inch, width=1.2 * inch, height=1.2 * inch, preserveAspectRatio=True)
                p.setFont("Helvetica-Oblique", 8)
                p.drawCentredString(right_x + 0.6 * inch, upi_y - 1.3 * inch, "Scan & Pay")
                p.setFont("Helvetica", 10)
            except Exception as e:
                p.drawString(right_x, upi_y, "[QR Code Missing]")

    # ----------------------------------------------------------

    p.save()
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = (
        f"attachment; filename=\"bill_{client.name}_{context['bill_date'].strftime('%Y%m%d')}.pdf\""
    )
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
    returned_stock = Bottle.objects.filter(status='returned').count()
    
    # Calculate percentages
    in_stock_percent = round((in_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    delivered_percent = round((delivered_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    returned_percent = round((returned_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    
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
        'returned_stock': returned_stock,
        'in_stock_percent': in_stock_percent,
        'delivered_percent': delivered_percent,
        'returned_percent': returned_percent,
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


@login_required
def admin_profile(request):
    admin_client, created = Client.objects.get_or_create(
        role='admin',
        defaults={'name': 'Admin'}
    )
    if request.method == 'POST':
        form = AdminProfileForm(request.POST, request.FILES, instance=admin_client)  # 👈 added request.FILES
        if form.is_valid():
            form.save()
            return redirect('admin_profile')
    else:
        form = AdminProfileForm(instance=admin_client)

    return render(request, 'admin_profile.html', {'form': form})


@staff_member_required
def category_list(request):
    categories = BottleCategory.objects.all()
    return render(request, 'category_list.html', {'categories': categories})

@staff_member_required
def category_create(request):
    if request.method == 'POST':
        form = BottleCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('category_list')
    else:
        form = BottleCategoryForm()
    return render(request, 'category_form.html', {'form': form})

@staff_member_required
def category_edit(request, category_id):
    category = BottleCategory.objects.get(id=category_id)
    if request.method == 'POST':
        form = BottleCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            return redirect('category_list')
    else:
        form = BottleCategoryForm(instance=category)
    return render(request, 'category_form.html', {'form': form, 'edit': True, 'category': category})