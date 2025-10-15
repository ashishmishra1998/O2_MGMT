from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.db.models.signals import post_migrate
from django.contrib.auth.models import User
from django.http import HttpResponse
from .forms import ClientForm, AddBottlesForm
from .models import Client, ManualBillRow
from .forms import TransactionForm, AdminProfileForm, BottlePricingForm, ClientForm, AddBottlesForm, BottleCategoryForm, ManualBillForm
from .models import Transaction, Bottle, Bill, BillTransaction, TransactionPhoto, BottleCategory
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponseForbidden
from .models import BottlePricing
from .forms import BottlePricingForm, TransactionEditForm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
from django.db.models import Q, F
from django.db.models.functions import Coalesce
from datetime import datetime  
from decimal import Decimal, InvalidOperation
from .utils import compute_totals, get_next_challan_number, compute_totals_from_subtotal, build_transaction_rows, number_to_words
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from django.db import transaction as db_transaction
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from datetime import datetime


@staff_member_required
def bottle_status(request):
    """Simple page to show current bottle ownership and allow bottle lookup.

    - Search by bottle code (`q`) shows which client currently holds it (only if delivered).
    - Summary table of clients -> number of bottles currently with them.
    """
    query_code = request.GET.get('q', '').strip()

    # Lookup result for a specific bottle code
    lookup = None
    if query_code:
        bottle = Bottle.objects.filter(code__iexact=query_code).first()
        if bottle:
            current_status = bottle.status
            current_client = None
            if current_status == 'delivered':
                # Find latest delivered transaction for this bottle to identify current client
                last_delivered_txn = (
                    Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
                    .order_by('-date')
                    .first()
                )
                current_client = last_delivered_txn.client if last_delivered_txn else None
            lookup = {
                'exists': True,
                'bottle': bottle,
                'status': current_status,
                'client': current_client,
            }
        else:
            lookup = {
                'exists': False,
                'code': query_code,
            }

    # Build summary: which clients currently hold how many bottles
    # Strategy: iterate bottles with status='delivered' and map to latest delivered txn's client
    delivered_bottles = Bottle.objects.filter(status='delivered').order_by('code')
    client_to_bottles = {}
    for b in delivered_bottles:
        last_delivered_txn = (
            Transaction.objects.filter(bottles=b, transaction_type='delivered')
            .order_by('-date')
            .first()
        )
        if last_delivered_txn:
            c = last_delivered_txn.client
            if c not in client_to_bottles:
                client_to_bottles[c] = []
            client_to_bottles[c].append(b)

    # Convert to a list of rows for the template (sorted by client name)
    client_rows = []
    for client_obj, bottles_list in client_to_bottles.items():
        client_rows.append({
            'client': client_obj,
            'count': len(bottles_list),
            'bottles': bottles_list,
        })
    client_rows.sort(key=lambda r: r['client'].name.lower())

    return render(request, 'bottle_status.html', {
        'query_code': query_code,
        'lookup': lookup,
        'client_rows': client_rows,
        'total_delivered_bottles': delivered_bottles.count(),
    })


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

@login_required
def admin_dashboard(request):
    # Ensure only staff users can access this dashboard
    if not request.user.is_staff:
        return redirect('delivery_dashboard')
    
    total_bottles = Bottle.objects.count()
    in_stock = Bottle.objects.filter(status='in_stock').count()
    delivered = Bottle.objects.filter(status='delivered').count()

    # Returned count = number of bottles ever returned via transactions
    returned = (
        Transaction.objects.filter(transaction_type='returned')
        .values_list('bottles', flat=True)
        .distinct()
        .count()
    )

    # Pending = bottles currently delivered (status='delivered')
    # This should match the bottle status page
    pending = delivered
    
    # Debug information to help identify data inconsistencies
    delivered_bottles = Bottle.objects.filter(status='delivered')
    delivered_bottles_list = list(delivered_bottles.values_list('code', flat=True))
    
    # Check for orphaned delivered bottles (delivered but no current client)
    orphaned_bottles = []
    for bottle in delivered_bottles:
        # Find the latest delivered transaction for this bottle
        latest_delivered_txn = (
            Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
            .order_by('-date')
            .first()
        )
        if not latest_delivered_txn:
            orphaned_bottles.append({
                'bottle_code': bottle.code,
                'issue': 'No delivered transaction found'
            })
        else:
            # Check if this bottle was later returned
            latest_returned_txn = (
                Transaction.objects.filter(bottles=bottle, transaction_type='returned')
                .order_by('-date')
                .first()
            )
            if latest_returned_txn and latest_returned_txn.date > latest_delivered_txn.date:
                orphaned_bottles.append({
                    'bottle_code': bottle.code,
                    'issue': f'Returned after delivery (Return: {latest_returned_txn.date}, Delivery: {latest_delivered_txn.date})'
                })
    
    debug_info = {
        'total_bottles': total_bottles,
        'in_stock': in_stock,
        'delivered': delivered,
        'returned_transactions': returned,
        'sum_check': in_stock + delivered,  # Should equal total_bottles
        'delivered_bottles_list': delivered_bottles_list,
        'in_stock_bottles_list': list(Bottle.objects.filter(status='in_stock').values_list('code', flat=True)),
        'orphaned_bottles': orphaned_bottles,
    }

    recent_transactions = Transaction.objects.prefetch_related('bottles').order_by('-date')[:5]

    return render(request, 'admin_dashboard.html', {
        'total_bottles': total_bottles,
        'delivered': delivered,
        'returned': returned,
        'in_stock': in_stock,
        'pending': pending,
        'recent_transactions': recent_transactions,
        'debug_info': debug_info,
    })



@login_required
def delivery_dashboard(request):
    # Ensure only delivery users can access this dashboard
    if request.user.is_staff:
        return redirect('admin_dashboard')
    
    # Get all transactions for this delivery boy (not just recent 5)
    recent_transactions = Transaction.objects.filter(delivered_by=request.user).order_by('-date')

    return render(request, 'delivery_dashboard.html', {
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

@staff_member_required
def client_edit(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    if request.method == 'POST':
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            return redirect('client_list')
    else:
        form = ClientForm(instance=client)
    return render(request, 'client_create.html', {'form': form, 'client': client, 'is_edit': True})

@staff_member_required
def client_delete(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    if request.method == 'POST':
        client.soft_delete()
        return redirect('client_list')
    return render(request, 'client_delete.html', {'client': client})

@staff_member_required
def client_restore(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=True)
    if request.method == 'POST':
        client.restore()
        return redirect('client_list')
    return render(request, 'client_restore.html', {'client': client})

def client_list(request):
    query = request.GET.get('q', '')
    show_deleted = request.GET.get('show_deleted', 'false').lower() == 'true'
    
    if show_deleted:
        # Show deleted clients
        if query:
            clients = Client.objects.filter(name__icontains=query, is_deleted=True).order_by('name')
        else:
            clients = Client.objects.filter(is_deleted=True).order_by('name')
    else:
        # Show active clients (default)
        if query:
            clients = Client.objects.filter(name__icontains=query, is_deleted=False).order_by('name')
        else:
            clients = Client.objects.filter(is_deleted=False).order_by('name')

    # Add pagination
    paginator = Paginator(clients, 20)  # 20 clients per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    client_stats = []
    for client in page_obj:  # Use paginated clients
        if show_deleted:
            # For deleted clients, don't calculate stats (they're not active)
            client_stats.append({
                'client': client,
                'delivered': 0,
                'returned': 0,
                'pending': 0,
                'pending_bottles_list': [],
                'pending_bill_bottles': 0,
            })
        else:
            # Delivered transactions
            delivered_txns = Transaction.objects.filter(client=client, transaction_type='delivered')
            delivered_bottles = sum(t.bottles.count() for t in delivered_txns)

            # Returned transactions
            returned_txns = Transaction.objects.filter(client=client, transaction_type='returned')
            returned_bottles = sum(t.bottles.count() for t in returned_txns)

            pending_bottles = delivered_bottles - returned_bottles

            # Get actual list of pending bottles
            pending_bottles_list = Bottle.objects.filter(
                status="delivered",
                transaction__client=client
            ).distinct().select_related("category")

            # Unbilled bottles
            pending_bill_bottles = sum(
                t.bottles.count() for t in delivered_txns.filter(billed=False)
            )

            client_stats.append({
                'client': client,
                'delivered': delivered_bottles,
                'returned': returned_bottles,
                'pending': pending_bottles,
                'pending_bottles_list': pending_bottles_list,
                'pending_bill_bottles': pending_bill_bottles,
            })

    return render(request, 'client_list.html', {
        'clients': page_obj,  # Use paginated clients
        'page_obj': page_obj,  # Pass for pagination controls
        'query': query,
        'client_stats': client_stats,
        'show_deleted': show_deleted
    })
    
@login_required
def get_client_bottles(request):
    client_id = request.GET.get('client_id')
    transaction_type = request.GET.get('transaction_type')
    bottles_data = []

    if client_id and transaction_type == 'returned':
        # Bottles delivered to this client, still marked delivered
        bottles = Bottle.objects.filter(
            transaction__client_id=client_id,
            transaction__transaction_type='delivered',
            status='delivered'
        ).distinct()

        for bottle in bottles:
            bottles_data.append({'id': bottle.id, 'name': bottle.code})  # adjust 'code' if needed

    return JsonResponse({'bottles': bottles_data})
  
@login_required
def transaction_create(request):
    transaction_type = request.GET.get('transaction_type')
    if not transaction_type:
        return render(request, 'transaction_type_select.html')

    message = None
    if request.method == 'POST':
        form = TransactionForm(request.POST, request.FILES, transaction_type=transaction_type)

        # Fix: update bottles queryset based on client for returned transactions
        if transaction_type == 'returned' and 'client' in request.POST:
            client_id = request.POST['client']
            form.fields['bottles'].queryset = Bottle.objects.filter(
                transaction__client_id=client_id,
                transaction__transaction_type='delivered',
                status='delivered'
            ).distinct()

        if form.is_valid():
            transaction = form.save(commit=False)
            transaction.delivered_by = request.user
            transaction.save()
            form.save_m2m()

            photos = request.FILES.getlist('photos')
            for photo in photos:
                TransactionPhoto.objects.create(transaction=transaction, image=photo)

            bottles = transaction.bottles.all()
            if transaction.transaction_type == 'delivered':
                bottles.update(status='delivered')
            elif transaction.transaction_type == 'returned':
                bottles.update(status='in_stock')

            return redirect('transaction_list')
    else:
        form = TransactionForm(transaction_type=transaction_type)
        if transaction_type == 'delivered' and not form.fields['bottles'].queryset.exists():
            message = 'No bottles available in stock for delivery.'

    return render(request, 'transaction_create.html', {
        'form': form,
        'transaction_type': transaction_type,
        'message': message
    })

@login_required
def transaction_list(request):
    if request.user.username == 'delivery':
        transactions = Transaction.objects.filter(delivered_by=request.user)
    else:
        transactions = Transaction.objects.all()
    
    # Default ordering: by effective date (custom_date if set, else date) descending
    transactions = transactions.order_by(Coalesce('custom_date', 'date').desc())

    # Filtering
    client_id = request.GET.get('client')
    if client_id:
        print("Filtering by client:", client_id)  # Debug print
        transactions = transactions.filter(client_id=client_id)

    # Date range filtering
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            transactions = transactions.filter(
                Q(date__date__gte=start_date_obj) | Q(custom_date__date__gte=start_date_obj)
            )
        except ValueError:
            pass
    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            transactions = transactions.filter(
                Q(date__date__lte=end_date_obj) | Q(custom_date__date__lte=end_date_obj)
            )
        except ValueError:
            pass

    transaction_type = request.GET.get('type')
    if transaction_type:
        if transaction_type == 'challan_asc':
            # Order by challan number ascending; place NULLs last consistently
            transactions = transactions.order_by(F('challan_number').asc(nulls_last=True))
        elif transaction_type == 'date_asc':
            transactions = transactions.order_by(Coalesce('custom_date', 'date').asc())
        elif transaction_type == 'date_desc':
            transactions = transactions.order_by(Coalesce('custom_date', 'date').desc())
        else:
            transactions = transactions.filter(transaction_type=transaction_type)

    transactions = transactions.prefetch_related('bottles')

    # 🔹 Add pagination
    paginator = Paginator(transactions, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'transaction_list.html', {
        'transactions': page_obj,  # pass paginated data
        'clients': Client.objects.all(),
        'selected_client': client_id,
        'selected_type': transaction_type,
        'selected_start_date': start_date,
        'selected_end_date': end_date,
        'page_obj': page_obj,  # useful for pagination controls
    })

# views.py
@login_required
def transaction_edit(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)

    if request.method == 'POST':
        form = TransactionForm(request.POST, request.FILES, instance=transaction)
        if form.is_valid():
            updated_transaction = form.save(commit=False)

            # Photos handling
            photos = request.FILES.getlist('photos')
            for photo in photos:
                TransactionPhoto.objects.create(transaction=transaction, image=photo)

            updated_transaction.save()
            form.save_m2m()

            # Bottle status updates (only if not billed)
            if not transaction.billed:
                bottles = transaction.bottles.all()
                if transaction.transaction_type == 'delivered':
                    bottles.update(status='delivered')
                elif transaction.transaction_type == 'returned':
                    bottles.update(status='in_stock')

            return redirect('transaction_list')
    else:
        form = TransactionForm(instance=transaction)

    return render(request, 'transaction_edit.html', {
        'form': form,
        'transaction': transaction
    })

@login_required
def transaction_delete(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    if request.method == 'POST':
        # Revert bottle statuses based on transaction type
        bottles_qs = transaction.bottles.all()
        if transaction.transaction_type == 'delivered':
            bottles_qs.update(status='in_stock')
        elif transaction.transaction_type == 'returned':
            # A return indicates previously delivered bottles came back; removing this should mark them delivered again
            bottles_qs.update(status='delivered')

        # Remove any BillTransaction links and possibly adjust billed flag
        BillTransaction.objects.filter(transaction=transaction).delete()
        # If it was marked billed, clear it (for consistency; record is being deleted anyway)
        transaction.delete()
        messages.success(request, 'Transaction deleted successfully.')
        return redirect('transaction_list')
    return render(request, 'delete_bill.html', { 'bill': None })

@login_required
def transaction_print(request, pk):
    """Print view for individual transaction - uses same PDF format as admin bills"""
    transaction = get_object_or_404(Transaction, pk=pk)
    
    # Only allow delivery boys to print their own transactions
    if request.user.username == 'delivery' and transaction.delivered_by != request.user:
        return HttpResponseForbidden('You can only print your own transactions.')
    
    # Get admin client info
    admin_client = Client.objects.filter(role='admin').first()
    
    # Create a mock bill object for the transaction
    class MockBill:
        def __init__(self, transaction, calculated_amount=Decimal('0.00')):
            self.id = transaction.id  # Use same format as admin bills
            self.bill_date = transaction.custom_date or transaction.date
            self.subtotal_amount = calculated_amount
            self.discount_amount = Decimal('0.00')
            self.discount_percentage = 0
            self.taxable_amount = calculated_amount
            self.gst_amount = Decimal('0.00')
            self.gst_percentage = 0
            self.final_amount = calculated_amount
            self.delivered_bottles = transaction.bottles.count()
    
    # Create transaction rows in the same format as admin bills
    transaction_rows = []
    bottles = transaction.bottles.all()
    
    # Group bottles by category for better display
    from collections import defaultdict
    bottles_by_category = defaultdict(list)
    for bottle in bottles:
        bottles_by_category[bottle.category.name].append(bottle)
    
    for category_name, category_bottles in bottles_by_category.items():
        # Get pricing for this category from BottleCategory
        try:
            category = category_bottles[0].category
            rate = category.price or Decimal('0.00')
        except (AttributeError, IndexError):
            rate = Decimal('0.00')
        
        # Create a row for this category
        row = {
            'date': transaction.custom_date or transaction.date,
            'gas': category_name,
            'challan_no': transaction.challan_number or '',
            'hsn': '28044000',  # Standard HSN for oxygen
            'qty': len(category_bottles),
            'cum': len(category_bottles),
            'total_qty': len(category_bottles),
            'rate': rate,
            'amount': rate * len(category_bottles)
        }
        transaction_rows.append(row)
    
    # Calculate total amount from transaction rows
    calculated_amount = sum(row['amount'] for row in transaction_rows)
    
    # Create mock bill with calculated amount
    bill = MockBill(transaction, calculated_amount)
    
    # Calculate totals using the same logic as admin bills
    # Use default GST percentage of 18% (same as admin bills)
    gst_percentage = Decimal('18.00')
    discount_percentage = Decimal('0.00')  # No discount for individual transactions
    
    # Import the utility function for GST calculation
    from bottle_MGMT.utils import compute_totals_from_subtotal
    totals = compute_totals_from_subtotal(
        subtotal=calculated_amount,
        discount_pct=discount_percentage,
        gst_pct=gst_percentage
    )
    
    # Calculate CGST and SGST (split GST equally)
    cgst_amount = (totals['gst_amount'] / Decimal('2')).quantize(Decimal('0.01'))
    sgst_amount = cgst_amount
    cgst_percentage = (gst_percentage / Decimal('2')).quantize(Decimal('0.01'))
    sgst_percentage = cgst_percentage
    
    # Update bill with calculated amounts
    bill.subtotal_amount = totals['subtotal']
    bill.discount_amount = totals['discount_amount']
    bill.discount_percentage = totals['discount_pct']
    bill.taxable_amount = totals['taxable']
    bill.gst_amount = totals['gst_amount']
    bill.gst_percentage = totals['gst_pct']
    bill.final_amount = totals['final']
    
    context = {
        'client': transaction.client,
        'bill': bill,
        'transaction_rows': transaction_rows,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'cgst_percentage': cgst_percentage,
        'sgst_percentage': sgst_percentage,
        'admin_client': admin_client,
        'amount_in_words': number_to_words(bill.final_amount),
        'transaction': transaction,  # Keep original transaction for reference
        'bottles': bottles,  # Keep bottles for reference
        'qty_sum': sum(row['qty'] for row in transaction_rows),
        'total_qty_sum': sum(row['total_qty'] for row in transaction_rows),
    }
    
    # Check if PDF format is requested
    if request.GET.get('format') == 'pdf':
        return generate_pdf_bill(request, context)
    
    # Use the same template as admin bills for consistency
    return render(request, 'bill_pdf.html', context)

    
@staff_member_required
def reports_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('You do not have permission to view this page.')
    import json
    from django.utils import timezone
    from datetime import timedelta

    user = request.user
    client_id = request.GET.get('client')

    transactions = Transaction.objects.all()
    if client_id:
        transactions = transactions.filter(client_id=client_id)

    clients = Client.objects.all()

    # Date ranges
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)

    # Stats function (counts bottles, not transactions)
    def count_stats(qs):
        delivered = sum(t.bottles.count() for t in qs.filter(transaction_type='delivered'))
        # Returned = bottles that have moved back to in_stock
        returned = Bottle.objects.filter(
            transaction__in=qs,
            status="in_stock"
        ).count()
        return {
            'delivered': delivered,
            'returned': returned,
        }

    stats = {
        'week': count_stats(transactions.filter(date__gte=week_ago)),
        'month': count_stats(transactions.filter(date__gte=month_ago)),
        'year': count_stats(transactions.filter(date__gte=year_ago)),
        'overall': count_stats(transactions),
    }

    # Chart data
    chart_labels = ['Week', 'Month', 'Year', 'Overall']
    delivered_data = [stats['week']['delivered'], stats['month']['delivered'],
                      stats['year']['delivered'], stats['overall']['delivered']]
    returned_data = [stats['week']['returned'], stats['month']['returned'],
                     stats['year']['returned'], stats['overall']['returned']]

    return render(request, 'reports.html', {
        'is_admin': user.is_staff,
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
    category_id = request.GET.get('category', '')

    bottles = Bottle.objects.all().order_by('code')

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

    # 🔹 Add pagination (20 bottles per page, tweak if needed)
    paginator = Paginator(bottles, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventory.html', {
        'bottles': page_obj,          # use paginated queryset
        'page_obj': page_obj,         # pass for template controls
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

    # Get all transactions for this client (delivered only by default)
    transactions = Transaction.objects.filter(
        client=client, transaction_type='delivered'
    ).order_by('-date')

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
        transactions_by_date.setdefault(date_key, []).append(transaction)

    # Get category-wise pricing
    from .models import BottleCategory
    categories = BottleCategory.objects.all().values("name", "price")

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
        'categories': categories,
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
    admin_client = Client.objects.filter(role='admin').first()

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
            challan_no = txn.challan_number if txn.challan_number else next_challan

            # if challan not already set, persist it to the transaction
            if not txn.challan_number:
                txn.challan_number = challan_no
                txn.save(update_fields=['challan_number'])
                next_challan += 1

            bt_objs.append(BillTransaction(bill=bill, transaction=txn, challan_number=challan_no))

            # also push challan number into transaction_rows for display
            for row in transaction_rows:
                if row['txn'] == txn:
                    row['challan_no'] = challan_no
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
        'amount_in_words': number_to_words(bill.final_amount),
    }
    # Compute QTY totals for display in the invoice table
    try:
        context['qty_sum'] = sum((r.get('qty') or 0) for r in transaction_rows)
        context['total_qty_sum'] = sum((r.get('total_qty') or 0) for r in transaction_rows)
    except Exception:
        context['qty_sum'] = 0
        context['total_qty_sum'] = 0
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
        
        # Handle manual bills differently
        if bill.manual_bill:
            rows = bill.manual_rows.all().order_by('date')
            transaction_rows = []
            subtotal = Decimal('0.00')
            for r in rows:
                transaction_rows.append({
                    'date': r.date,
                    'gas': r.gas_type,
                    'challan_no': r.challan_no,
                    'hsn': r.hsn or (admin_client.hsn_code if admin_client else '28044090'),
                    'qty': r.qty,
                    'cum': r.cum,
                    'total_qty': r.total_qty or r.qty,
                    'rate': r.rate,
                    'amount': r.amount,
                    'txn': None
                })
                subtotal += (r.amount or Decimal('0.00'))
        else:
            # Regular bills with transactions
            bts = BillTransaction.objects.filter(bill=bill).select_related('transaction').order_by('transaction__date', 'created_at')
            txns = [bt.transaction for bt in bts]

            transaction_rows, subtotal = build_transaction_rows(txns, admin_client)
            for row in transaction_rows:
                bt = next(bt for bt in bts if bt.transaction_id == row['txn'].id)
                # Use transaction's challan_number if available, otherwise use BillTransaction's challan_number
                row['challan_no'] = row['txn'].challan_number if row['txn'].challan_number else bt.challan_number
            # Compute qty sums for existing bill path too
            try:
                qty_sum = sum((r.get('qty') or 0) for r in transaction_rows)
                total_qty_sum = sum((r.get('total_qty') or 0) for r in transaction_rows)
            except Exception:
                qty_sum = 0
                total_qty_sum = 0
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
            'amount_in_words': number_to_words(bill.final_amount),
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
            challan_no = txn.challan_number if txn.challan_number else next_challan

            if not txn.challan_number:
                txn.challan_number = challan_no
                txn.save(update_fields=['challan_number'])
                next_challan += 1

            bt_objs.append(BillTransaction(bill=bill, transaction=txn, challan_number=challan_no))

            for row in transaction_rows:
                if row['txn'] == txn:
                    row['challan_no'] = challan_no
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
        'amount_in_words': number_to_words(bill.final_amount),
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
    # License number intentionally omitted from PDF as requested
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
    # Compute qty totals
    try:
        qty_sum_pdf = sum((r.get("qty") or 0) for r in tx_rows)
        total_qty_sum_pdf = sum((r.get("total_qty") or 0) for r in tx_rows)
    except Exception:
        qty_sum_pdf = 0
        total_qty_sum_pdf = 0
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
    # Combined totals + grand total row (to mirror HTML)
    table_data.append(["", "", "", "", str(qty_sum_pdf), "", str(total_qty_sum_pdf), "Grand Total", f"Rs. {grand_total:.2f}"])

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
    
    # --- Amount in Words ---
    amount_in_words = context.get("amount_in_words", "")
    # Debug: Always add amount in words for testing
    elements.append(Paragraph(f"<b>Amount in Words:</b> {amount_in_words.title() if amount_in_words else 'NOT_SET'}", styles["LeftSmall"]))
    elements.append(Spacer(1, 12))
    
    # # --- QR Code (if available) ---
    # upi_qr = getattr(admin, "upi_qr", None) or (admin.get("upi_qr") if isinstance(admin, dict) else None)
    # if upi_qr:
    #     try:
    #         # Get the full path to the QR code image
    #         if hasattr(upi_qr, 'path'):
    #             qr_path = upi_qr.path
    #         else:
    #             # If it's a URL, we need to handle it differently
    #             qr_path = str(upi_qr)
            
    #         # Create image element with proper spacing
    #         qr_image = Image(qr_path, width=120, height=120)
    #         elements.append(Spacer(1, 12))  # White space on top
    #         elements.append(qr_image)
    #         elements.append(Paragraph("Scan & Pay", styles["LeftSmall"]))
    #         elements.append(Spacer(1, 12))  # White space on bottom
    #     except Exception as e:
    #         # If QR code fails to load, just skip it
    #         print(f"QR code loading failed: {e}")
    #         pass

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

    # --- Terms and Conditions ---
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("<b>Terms and Conditions</b>", styles["LeftSmall"]))
    elements.append(Spacer(1, 6))
    
    terms_conditions = [
        "• Received the above filled cylinders complete with valves and caps.",
        "• We agree with the terms and conditions of the agreement, including the rental charges.",
        "• Our responsibility ceases as soon as goods have left our warehouse.",
        "• Payment is requested by account payee cheque/DD only.",
        "• Interest at @24% will be charged if payment is not made within 30 days."
    ]
    
    for term in terms_conditions:
        elements.append(Paragraph(term, styles["LeftSmall"]))
    
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
    """Mark a bill as paid (simple confirm flow)."""
    bill = get_object_or_404(Bill, id=bill_id)

    # Prevent duplicate marking
    if bill.paid:
        paid_on = bill.paid_date.strftime("%d %b %Y, %H:%M") if bill.paid_date else "earlier"
        messages.info(request, f"Bill #{bill.id} is already marked paid (on {paid_on}).")
        return redirect('bill_history', client_id=bill.client.id)

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
    """Comprehensive sales analytics dashboard (with fixed recent transactions)"""
    from django.db.models import Sum
    from django.utils import timezone
    from datetime import datetime, timedelta
    import calendar
    from decimal import Decimal
    # local imports used in calculations
    from .models import Transaction, BottleCategory, BottlePricing, BillTransaction, Bottle, Bill, Client

    # Get date filters
    selected_year = int(request.GET.get('year', timezone.now().year))
    selected_month = int(request.GET.get('month', timezone.now().month))

    now = timezone.now()
    current_year = now.year
    current_month = now.month
    today = now.date()

    # Date ranges
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    year_start = today.replace(month=1, day=1)
    year_end = today.replace(month=12, day=31)

    all_bills = Bill.objects.all()

    # price map for quick lookup (category_id -> Decimal price)
    price_map = {}
    for c in BottleCategory.objects.all():
        try:
            price_map[c.id] = Decimal(c.price)
        except Exception:
            price_map[c.id] = Decimal("0.00")
    default_price = Decimal(BottlePricing.get_solo().price)

    def get_sales_data(bills_qs, include_unbilled=False):
        total_bills = bills_qs.count()
        billed_amount = bills_qs.aggregate(Sum("final_amount"))["final_amount__sum"] or Decimal("0.00")

        paid_amount = bills_qs.filter(paid=True).aggregate(Sum("final_amount"))["final_amount__sum"] or Decimal("0.00")
        unpaid_amount = billed_amount - paid_amount

        delivered_bottles = bills_qs.aggregate(Sum("delivered_bottles"))["delivered_bottles__sum"] or 0

        if include_unbilled:
            # include unbilled delivered transactions in current period
            unbilled_txns = Transaction.objects.filter(billed=False, transaction_type="delivered")
            unbilled_bottles = 0
            unbilled_amount = Decimal("0.00")
            # iterate efficiently with prefetch
            for t in unbilled_txns.prefetch_related('bottles__category'):
                for b in t.bottles.all():
                    unbilled_bottles += 1
                    unbilled_amount += price_map.get(b.category_id, default_price)
            delivered_bottles += unbilled_bottles
            unpaid_amount += unbilled_amount
            billed_amount += unbilled_amount

        return {
            "total_bills": total_bills,
            "total_amount": billed_amount,
            "total_bottles_delivered": delivered_bottles,
            "paid_amount": paid_amount,
            "unpaid_amount": unpaid_amount,
            "payment_rate": (paid_amount / billed_amount * 100) if billed_amount > 0 else 0,
        }

    # Daily/weekly/monthly/yearly (include unbilled so pending shows immediately)
    daily_sales = get_sales_data(all_bills.filter(bill_date__date=today), include_unbilled=True)
    weekly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[week_start, week_end]), include_unbilled=True)
    monthly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[month_start, month_end]), include_unbilled=True)
    yearly_sales = get_sales_data(all_bills.filter(bill_date__date__range=[year_start, year_end]), include_unbilled=True)

    # selected period
    selected_start = datetime(selected_year, selected_month, 1).date()
    selected_end = (selected_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    selected_sales = get_sales_data(all_bills.filter(bill_date__date__range=[selected_start, selected_end]), include_unbilled=True)

    # stock status
    total_stock = Bottle.objects.count()
    in_stock = Bottle.objects.filter(status="in_stock").count()
    delivered_stock = Bottle.objects.filter(status="delivered").count()
    in_stock_percent = round((in_stock / total_stock * 100) if total_stock > 0 else 0, 1)
    delivered_percent = round((delivered_stock / total_stock * 100) if total_stock > 0 else 0, 1)

    # client-wise analytics (include unbilled)
    client_analytics = []
    for client in Client.objects.filter(role="customer"):
        client_bills = all_bills.filter(client=client)
        billed_amount = client_bills.aggregate(Sum("final_amount"))["final_amount__sum"] or Decimal("0.00")
        paid_amount = client_bills.filter(paid=True).aggregate(Sum("final_amount"))["final_amount__sum"] or Decimal("0.00")
        unpaid_amount = billed_amount - paid_amount

        # unbilled deliveries for this client
        unbilled_txns = Transaction.objects.filter(client=client, billed=False, transaction_type="delivered").prefetch_related('bottles__category')
        unbilled_amount = Decimal("0.00")
        unbilled_bottles = 0
        for t in unbilled_txns:
            for b in t.bottles.all():
                unbilled_bottles += 1
                unbilled_amount += price_map.get(b.category_id, default_price)

        total_delivered = client_bills.aggregate(Sum("delivered_bottles"))["delivered_bottles__sum"] or 0
        total_delivered += unbilled_bottles

        unpaid_amount += unbilled_amount
        billed_amount += unbilled_amount

        client_analytics.append({
            "client": client,
            "total_delivered": total_delivered,
            "total_pending": total_delivered,  # simplified (no returned state)
            "total_amount": billed_amount,
            "paid_amount": paid_amount,
            "unpaid_amount": unpaid_amount,
            "payment_rate": (paid_amount / billed_amount * 100) if billed_amount > 0 else 0,
        })

    client_analytics.sort(key=lambda x: x["total_amount"], reverse=True)

    # monthly trend (billed only)
    monthly_trend = []
    for month in range(1, 13):
        m_start = datetime(selected_year, month, 1).date()
        m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        m_bills = all_bills.filter(bill_date__date__range=[m_start, m_end])
        m_amount = m_bills.aggregate(Sum("final_amount"))["final_amount__sum"] or Decimal("0.00")
        m_bottles = m_bills.aggregate(Sum("delivered_bottles"))["delivered_bottles__sum"] or 0
        monthly_trend.append({"month": calendar.month_name[month], "amount": m_amount, "bottles": m_bottles})

    # --- FIX: recent transactions (show transactions, not bills) ---
    recent_txns_qs = Transaction.objects.select_related("client", "delivered_by").prefetch_related("bottles__category").order_by("-date")[:10]
    recent_transactions = []
    for txn in recent_txns_qs:
        # bottle count
        bottles = list(txn.bottles.all())
        bottle_count = len(bottles)
        # calculate transaction amount by summing per-bottle category price
        txn_amount = Decimal("0.00")
        for b in bottles:
            txn_amount += price_map.get(b.category_id, default_price)

        # find bill if any
        bt = BillTransaction.objects.filter(transaction=txn).select_related("bill").first()
        bill_id = bt.bill.id if bt and bt.bill else None
        billed_flag = bool(bill_id)

        recent_transactions.append({
            "id": txn.id,
            "date": txn.custom_date or txn.date,
            "client": txn.client,
            "client_name": txn.client.name,
            "type": txn.transaction_type,
            "bottles": bottle_count,
            "amount": txn_amount,
            "billed": billed_flag,
            "bill_id": bill_id,
            "delivered_by": getattr(txn.delivered_by, "username", None),
        })

    # recent bills kept for other parts of the template if needed
    recent_bills = all_bills.order_by("-bill_date")[:10]

    year_range = list(range(current_year - 2, current_year + 3))

    context = {
        "daily_sales": daily_sales,
        "weekly_sales": weekly_sales,
        "monthly_sales": monthly_sales,
        "yearly_sales": yearly_sales,
        "selected_sales": selected_sales,
        "selected_year": selected_year,
        "selected_month": selected_month,
        "current_year": current_year,
        "current_month": current_month,
        "total_stock": total_stock,
        "in_stock": in_stock,
        "delivered_stock": delivered_stock,
        "in_stock_percent": in_stock_percent,
        "delivered_percent": delivered_percent,
        "client_analytics": client_analytics,
        "monthly_trend": monthly_trend,
        "recent_bills": recent_bills,
        "recent_transactions": recent_transactions,   # NEW: recent transactions list of dicts
        "top_clients": client_analytics[:5],
        "today": today,
        "week_start": week_start,
        "week_end": week_end,
        "month_start": month_start,
        "month_end": month_end,
        "year_range": year_range,
    }
    return render(request, "sales_analytics.html", context)

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

@staff_member_required
def manual_bill_create(request):
    """Create a manual bill with multiple rows (uses ManualBillRow)."""
    if request.method == 'POST':
        form = ManualBillForm(request.POST)
        if form.is_valid():
            try:
                client = form.cleaned_data['client']
                bill_date = form.cleaned_data['bill_date']
                default_hsn = form.cleaned_data.get('hsn_code') or (Client.objects.filter(role='admin').first().hsn_code if Client.objects.filter(role='admin').exists() else '28044090')
                default_cum = form.cleaned_data.get('cum_value') or Decimal('7.00')
                discount_pct = Decimal(form.cleaned_data.get('discount_percentage') or 0)
                gst_pct = Decimal(form.cleaned_data.get('gst_percentage') or 18)
                description = form.cleaned_data.get('description', '')

                # helper to read lists posted as name[] or name
                def post_list(key):
                    vals = request.POST.getlist(key)
                    if vals:
                        return vals
                    return request.POST.getlist(f"{key}[]")

                dates = post_list('transaction_date')
                gas_types = post_list('gas_type')            # may be category ids or names
                challans = post_list('challan_number')
                hsns = post_list('hsn_code')
                cums = post_list('cum_value')
                qtys = post_list('quantity')
                rates = post_list('rate_per_bottle')

                # Basic validation: need at least one row
                if not dates or len(dates) == 0:
                    messages.error(request, "Add at least one transaction row.")
                    return render(request, 'manual_bill.html', {'form': form})

                # normalize length to number of date entries
                n_rows = len(dates)

                rows_to_create = []
                subtotal_sum = Decimal('0.00')
                total_qty = 0

                for i in range(n_rows):
                    # date
                    d_str = dates[i].strip() if i < len(dates) and dates[i] else ''
                    try:
                        # expecting YYYY-MM-DD from <input type="date">
                        row_date = datetime.strptime(d_str, '%Y-%m-%d').date() if d_str else bill_date.date()
                    except Exception:
                        row_date = bill_date.date()

                    # gas type (try to resolve BottleCategory name from id)
                    gas_val = gas_types[i] if i < len(gas_types) else ''
                    gas_name = ''
                    if gas_val:
                        try:
                            # gas_val may be id or name
                            bc = BottleCategory.objects.filter(id=gas_val).first()
                            if bc:
                                gas_name = bc.name
                                # If rate not provided, fall back to bottle category price
                                default_rate_for_row = bc.price
                            else:
                                gas_name = gas_val
                                default_rate_for_row = None
                        except Exception:
                            gas_name = gas_val
                            default_rate_for_row = None
                    else:
                        gas_name = 'Manual Entry'
                        default_rate_for_row = None

                    # challan
                    challan_raw = challans[i] if i < len(challans) and challans[i] else None
                    challan_no = int(challan_raw) if challan_raw else None

                    # hsn & cum (use row if provided, otherwise fallback to default)
                    hsn_row = hsns[i] if i < len(hsns) and hsns[i] else default_hsn
                    cum_row = Decimal(cums[i]) if (i < len(cums) and cums[i]) else Decimal(default_cum)

                    # qty & rate
                    qty = int(qtys[i]) if i < len(qtys) and qtys[i] else 0
                    rate = None
                    if i < len(rates) and rates[i]:
                        try:
                            rate = Decimal(rates[i])
                        except Exception:
                            rate = Decimal('0.00')
                    if (rate is None or rate == Decimal('0.00')) and default_rate_for_row:
                        rate = Decimal(default_rate_for_row)

                    amount = (Decimal(qty) * (rate or Decimal('0.00'))).quantize(Decimal('0.01'))

                    subtotal_sum += amount
                    total_qty += qty

                    rows_to_create.append(ManualBillRow(
                        date=row_date,
                        gas_type=gas_name,
                        challan_no=challan_no,
                        hsn=hsn_row,
                        qty=qty,
                        cum=cum_row,
                        total_qty=qty,
                        rate=(rate or Decimal('0.00')),
                        amount=amount
                    ))

                # Compute totals (discount applies on subtotal sum, then GST applied to taxable after discount)
                discount_amount = (subtotal_sum * discount_pct / Decimal('100')).quantize(Decimal('0.01'))
                taxable = (subtotal_sum - discount_amount).quantize(Decimal('0.01'))
                gst_amount = (taxable * gst_pct / Decimal('100')).quantize(Decimal('0.01'))
                final_amount = (taxable + gst_amount).quantize(Decimal('0.01'))

                # price_per_bottle: weighted average (subtotal_sum / total_qty) if qty > 0
                if total_qty > 0:
                    price_per_bottle = (subtotal_sum / Decimal(total_qty)).quantize(Decimal('0.01'))
                else:
                    price_per_bottle = BottlePricing.get_solo().price

                # Create Bill and ManualBillRow(s)
                with db_transaction.atomic():
                    bill = Bill.objects.create(
                        client_id=client.id,  # Pass ID instead of object
                        bill_date=bill_date,
                        delivered_bottles=total_qty,
                        returned_bottles=0,
                        pending_bottles=total_qty,
                        price_per_bottle=price_per_bottle,
                        total_amount=subtotal_sum,
                        subtotal_amount=subtotal_sum,
                        discount_percentage=discount_pct,
                        discount_amount=discount_amount,
                        taxable_amount=taxable,
                        gst_percentage=gst_pct,
                        gst_amount=gst_amount,
                        final_amount=final_amount,
                        generated_by_id=request.user.id,  # Pass ID instead of object
                        bill_type='manual',
                        description=description or f'Manual bill',
                        manual_bill=True,
                        manual_gas_type=None,
                        manual_challan_number=None
                    )

                    # attach rows (set bill FK)
                    for r in rows_to_create:
                        r.bill = bill

                    ManualBillRow.objects.bulk_create(rows_to_create)

                # redirect to preview/generate
                return redirect('generate_bill', client_id=client.id, bill_id=bill.id)

            except Exception as e:
                messages.error(request, f'Error creating manual bill: {str(e)}')
                import traceback
                traceback.print_exc()
                return render(request, 'manual_bill.html', {'form': form})
        else:
            # form invalid
            return render(request, 'manual_bill.html', {'form': form})
    else:
        admin_client = Client.objects.filter(role='admin').first()
        default_cum = admin_client.cum_value if admin_client and admin_client.cum_value else Decimal('7.00')
        default_hsn = admin_client.hsn_code if admin_client and admin_client.hsn_code else '28044090'
        form = ManualBillForm(initial={
            'cum_value': default_cum,
            'hsn_code': default_hsn,
        })
    return render(request, 'manual_bill.html', {'form': form, 'default_hsn': default_hsn,'default_cum': default_cum})

@staff_member_required
def manual_bills_list(request):
    """List all manually created bills"""
    # Get all manual bills
    manual_bills = Bill.objects.filter(manual_bill=True).select_related('client', 'generated_by').order_by('-bill_date')
    
    # Add pagination
    paginator = Paginator(manual_bills, 20)  # 20 bills per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'manual_bills_list.html', {
        'manual_bills': page_obj,
        'page_obj': page_obj,
    })


@staff_member_required
def export_transactions(request):
    """Export transactions to Excel with filtering options"""
    if request.method == 'GET':
        # Get filter parameters
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        transaction_type = request.GET.get('transaction_type', '')
        client_id = request.GET.get('client_id', '')
        
        # If no parameters provided, show the form
        if not any([start_date, end_date, transaction_type, client_id]):
            clients = Client.objects.filter(is_deleted=False).order_by('name')
            return render(request, 'export_transactions.html', {
                'clients': clients
            })
        
        # Filter transactions based on parameters
        transactions = Transaction.objects.select_related('client', 'delivered_by').prefetch_related('bottles')
        
        # Apply date filters
        if start_date:
            transactions = transactions.filter(date__gte=start_date)
        if end_date:
            # Add one day to end_date to include the entire day
            from datetime import timedelta
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            transactions = transactions.filter(date__lt=end_date_obj + timedelta(days=1))
        
        # Apply transaction type filter
        if transaction_type:
            transactions = transactions.filter(transaction_type=transaction_type)
        
        # Apply client filter
        if client_id:
            transactions = transactions.filter(client_id=client_id)
        
        # Order by date
        transactions = transactions.order_by('date')
        
        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Transactions Export"
        
        # Define headers
        headers = [
            'Transaction ID', 'Date', 'Time', 'Client Name', 'Client Contact', 
            'Transaction Type', 'Challan Number', 'Delivered By', 'Bottle Code', 
            'Bottle Category', 'Bottle Status'
        ]
        
        # Style headers
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # Write headers
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
        
        # Write data
        row_num = 2
        for transaction in transactions:
            # Get all bottles for this transaction
            bottles = transaction.bottles.all()
            
            if bottles.exists():
                # Create a row for each bottle
                for bottle in bottles:
                    ws.cell(row=row_num, column=1, value=transaction.id)
                    ws.cell(row=row_num, column=2, value=transaction.date.strftime('%Y-%m-%d'))
                    ws.cell(row=row_num, column=3, value=transaction.date.strftime('%H:%M:%S'))
                    ws.cell(row=row_num, column=4, value=transaction.client.name)
                    ws.cell(row=row_num, column=5, value=transaction.client.contact)
                    ws.cell(row=row_num, column=6, value=transaction.get_transaction_type_display())
                    ws.cell(row=row_num, column=7, value=transaction.challan_number or '')
                    ws.cell(row=row_num, column=8, value=transaction.delivered_by.username if transaction.delivered_by else '')
                    ws.cell(row=row_num, column=9, value=bottle.code)
                    ws.cell(row=row_num, column=10, value=bottle.category.name)
                    ws.cell(row=row_num, column=11, value=bottle.get_status_display())
                    row_num += 1
            else:
                # If no bottles, still show transaction info
                ws.cell(row=row_num, column=1, value=transaction.id)
                ws.cell(row=row_num, column=2, value=transaction.date.strftime('%Y-%m-%d'))
                ws.cell(row=row_num, column=3, value=transaction.date.strftime('%H:%M:%S'))
                ws.cell(row=row_num, column=4, value=transaction.client.name)
                ws.cell(row=row_num, column=5, value=transaction.client.contact)
                ws.cell(row=row_num, column=6, value=transaction.get_transaction_type_display())
                ws.cell(row=row_num, column=7, value=transaction.challan_number or '')
                ws.cell(row=row_num, column=8, value=transaction.delivered_by.username if transaction.delivered_by else '')
                ws.cell(row=row_num, column=9, value='No bottles')
                ws.cell(row=row_num, column=10, value='')
                ws.cell(row=row_num, column=11, value='')
                row_num += 1
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Add current bottles summary section
        summary_start_row = row_num + 2
        
        # Add separator
        ws.cell(row=summary_start_row, column=1, value="=" * 100)
        for col in range(2, 12):
            ws.cell(row=summary_start_row, column=col, value="=" * 20)
        
        # Add summary title
        summary_title_row = summary_start_row + 1
        ws.cell(row=summary_title_row, column=1, value="CURRENT BOTTLES WITH CLIENTS SUMMARY")
        ws.merge_cells(f'A{summary_title_row}:K{summary_title_row}')
        title_cell = ws.cell(row=summary_title_row, column=1)
        title_cell.font = Font(bold=True, size=14, color="FFFFFF")
        title_cell.fill = PatternFill(start_color="2E8B57", end_color="2E8B57", fill_type="solid")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # Get current bottles with clients
        current_bottles = Bottle.objects.filter(
            status='delivered'
        ).select_related('category').order_by('code')
        
        # Group bottles by client (find client through latest delivered transaction)
        from collections import defaultdict
        client_bottles = defaultdict(list)
        for bottle in current_bottles:
            # Find the latest delivered transaction for this bottle to get the current client
            latest_delivered_txn = (
                Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
                .select_related('client')
                .order_by('-date')
                .first()
            )
            if latest_delivered_txn:
                client_bottles[latest_delivered_txn.client].append(bottle)
        
        # Add summary headers
        summary_header_row = summary_title_row + 2
        summary_headers = [
            'Client Name', 'Client Contact', 'Total Bottles', 'Bottle Codes', 
            'Categories', 'Last Delivery Date', 'Days Since Delivery'
        ]
        
        for col, header in enumerate(summary_headers, 1):
            cell = ws.cell(row=summary_header_row, column=col, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # Add client summary data
        current_row = summary_header_row + 1
        for client, bottles in client_bottles.items():
            # Get last delivery date for this client
            last_delivery = Transaction.objects.filter(
                client=client,
                transaction_type='delivered',
                bottles__in=bottles
            ).order_by('-date').first()
            
            last_delivery_date = last_delivery.date if last_delivery else None
            days_since_delivery = ""
            if last_delivery_date:
                from datetime import date
                days_since = (date.today() - last_delivery_date.date()).days
                days_since_delivery = f"{days_since} days"
            
            # Get unique categories
            categories = list(set(bottle.category.name for bottle in bottles))
            categories_str = ", ".join(categories)
            
            # Get bottle codes (limit to first 10 for readability)
            bottle_codes = [bottle.code for bottle in bottles[:10]]
            bottle_codes_str = ", ".join(bottle_codes)
            if len(bottles) > 10:
                bottle_codes_str += f" ... (+{len(bottles) - 10} more)"
            
            # Write client summary row
            ws.cell(row=current_row, column=1, value=client.name)
            ws.cell(row=current_row, column=2, value=client.contact)
            ws.cell(row=current_row, column=3, value=len(bottles))
            ws.cell(row=current_row, column=4, value=bottle_codes_str)
            ws.cell(row=current_row, column=5, value=categories_str)
            ws.cell(row=current_row, column=6, value=last_delivery_date.strftime('%Y-%m-%d') if last_delivery_date else 'N/A')
            ws.cell(row=current_row, column=7, value=days_since_delivery)
            
            current_row += 1
        
        # Add summary footer
        footer_row = current_row + 1
        ws.cell(row=footer_row, column=1, value=f"Total Clients with Bottles: {len(client_bottles)}")
        ws.cell(row=footer_row, column=3, value=f"Total Bottles Out: {sum(len(bottles) for bottles in client_bottles.values())}")
        
        # Style summary footer
        footer_cell = ws.cell(row=footer_row, column=1)
        footer_cell.font = Font(bold=True)
        footer_cell.fill = PatternFill(start_color="E6E6FA", end_color="E6E6FA", fill_type="solid")
        
        # Auto-adjust summary column widths
        for col in range(1, 8):
            max_length = 0
            column_letter = ws.cell(row=summary_header_row, column=col).column_letter
            for row in range(summary_header_row, current_row):
                try:
                    cell_value = ws.cell(row=row, column=col).value
                    if cell_value and len(str(cell_value)) > max_length:
                        max_length = len(str(cell_value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Create HTTP response
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
        # Generate filename with date range and filters
        filename_parts = ['transactions_export']
        if start_date:
            filename_parts.append(f"from_{start_date}")
        if end_date:
            filename_parts.append(f"to_{end_date}")
        if transaction_type:
            filename_parts.append(transaction_type)
        if client_id:
            client = Client.objects.get(id=client_id)
            filename_parts.append(f"client_{client.name.replace(' ', '_')}")
        
        filename = '_'.join(filename_parts) + '.xlsx'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Save workbook to response
        wb.save(response)
        return response
    
    return HttpResponse("Invalid request method", status=405)


@staff_member_required
def fix_orphaned_bottles(request):
    """Fix orphaned delivered bottles that should be in stock"""
    if request.method == 'POST':
        # Get all delivered bottles
        delivered_bottles = Bottle.objects.filter(status='delivered')
        fixed_count = 0
        
        for bottle in delivered_bottles:
            # Find the latest delivered transaction for this bottle
            latest_delivered_txn = (
                Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
                .order_by('-date')
                .first()
            )
            
            if not latest_delivered_txn:
                # No delivered transaction found - fix status
                bottle.status = 'in_stock'
                bottle.save()
                fixed_count += 1
            else:
                # Check if this bottle was later returned
                latest_returned_txn = (
                    Transaction.objects.filter(bottles=bottle, transaction_type='returned')
                    .order_by('-date')
                    .first()
                )
                if latest_returned_txn and latest_returned_txn.date > latest_delivered_txn.date:
                    # Bottle was returned after delivery - fix status
                    bottle.status = 'in_stock'
                    bottle.save()
                    fixed_count += 1
        
        messages.success(request, f'Fixed {fixed_count} orphaned bottles. They have been moved to in_stock status.')
        return redirect('admin_dashboard')
    
    return render(request, 'fix_orphaned_bottles.html')
