from django import forms
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import Client, Transaction, Bottle, BottlePricing, BottleCategory
from decimal import Decimal

class AddBottlesForm(forms.Form):
    series = forms.CharField(label='Series Prefix', max_length=5, help_text='e.g. SV or AV')
    start = forms.IntegerField(label='Start Number', min_value=1)
    end = forms.IntegerField(label='End Number', min_value=1)
    category = forms.ModelChoiceField(
        queryset=BottleCategory.objects.all(),
        label='Category',
        empty_label="Select a Category"
    )
    
    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('start')
        end = cleaned_data.get('end')
        if start and end and start > end:
            raise forms.ValidationError('Start number must be less than or equal to end number.')
        return cleaned_data

class ClientForm(forms.ModelForm):
    class Meta:   
        model = Client
        fields = ['name', 'contact', 'email', 'address', 'company_name', 'gst_number', 'alt_contact']
 
class AdminProfileForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            'owner_gst', 'account_holder','license_number', 'account_number', 'ifsc', 'branch',
            'account_type', 'mmid', 'vpa', 'upi_number', 'upi_qr',
            'contact', 'hsn_code', 'cum_value',
        ]

    def clean_contact(self):
        contact = self.cleaned_data['contact']
        if not contact.isdigit() or len(contact) != 10:
            raise forms.ValidationError('Contact number must be exactly 10 digits.')
        return contact

class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ['client', 'bottles', 'transaction_type', 'custom_date', 'challan_number']
        widgets = {
            'custom_date': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'form-control'
            }),
            'challan_number': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter challan number'
            })
        }

    def __init__(self, *args, **kwargs):
        transaction_type = kwargs.pop('transaction_type', None)
        transaction = kwargs.get('instance')  # Will be set when editing
        super().__init__(*args, **kwargs)

        self.fields['custom_date'].required = False
        self.fields['custom_date'].help_text = "Optional: Leave blank to use current date/time"

        # --- Create behavior ---
        if not transaction:  # Creating new transaction
            if transaction_type == 'delivered':
                self.fields['bottles'].queryset = Bottle.objects.filter(status='in_stock')
            elif transaction_type == 'returned':
                # Initially empty; filtered by client via AJAX
                self.fields['bottles'].queryset = Bottle.objects.none()
            else:
                self.fields['bottles'].queryset = Bottle.objects.all()

        # --- Edit behavior ---
        if transaction:
            if transaction.billed:
                # Lock certain fields if billed
                locked_fields = ['client', 'bottles', 'transaction_type']
                for field in locked_fields:
                    self.fields[field].disabled = True
            else:
                # Not billed - allow editing with proper bottle filtering
                # Get bottles already in this transaction (should always be available)
                current_bottle_ids = set(transaction.bottles.values_list('id', flat=True))
                
                # Determine transaction type:
                # 1. From kwargs (transaction_type parameter passed to form)
                # 2. From form data if form has been submitted
                # 3. From existing transaction instance
                tx_type = transaction_type
                if not tx_type and self.data.get('transaction_type'):
                    tx_type = self.data.get('transaction_type')
                if not tx_type:
                    tx_type = transaction.transaction_type
                
                if tx_type == 'delivered':
                    # For delivery transactions: show in_stock bottles + bottles already in this transaction
                    # This allows keeping current bottles and adding new in_stock bottles
                    self.fields['bottles'].queryset = Bottle.objects.filter(
                        Q(status='in_stock') | Q(id__in=current_bottle_ids)
                    ).distinct()
                elif tx_type == 'returned':
                    # For return transactions: show delivered bottles for this client + bottles already in this transaction
                    # Get client from form data if available (in case client is being changed), otherwise use transaction's client
                    client_id = None
                    if self.data.get('client'):
                        try:
                            client_id = int(self.data.get('client'))
                        except (ValueError, TypeError):
                            pass
                    if not client_id:
                        client_id = transaction.client_id
                    
                    if client_id:
                        # Get all bottles that are currently delivered
                        delivered_bottles = Bottle.objects.filter(status='delivered')
                        
                        # Filter to only include bottles where the latest delivered transaction is for this client
                        valid_bottle_ids = []
                        for bottle in delivered_bottles:
                            latest_delivered_txn = (
                                Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
                                .order_by(Coalesce('custom_date', 'date').desc())
                                .first()
                            )
                            if latest_delivered_txn and latest_delivered_txn.client_id == client_id:
                                valid_bottle_ids.append(bottle.id)
                        
                        # Combine with bottles already in this transaction
                        valid_bottle_ids.extend(current_bottle_ids)
                        self.fields['bottles'].queryset = Bottle.objects.filter(id__in=valid_bottle_ids).distinct()
                    else:
                        # No client selected yet, only show bottles already in transaction
                        self.fields['bottles'].queryset = Bottle.objects.filter(id__in=current_bottle_ids)

    def clean_bottles(self):
        """Validate that selected bottles are appropriate for the transaction type"""
        bottles = self.cleaned_data.get('bottles')
        transaction_type = self.cleaned_data.get('transaction_type')
        transaction = self.instance
        
        if not bottles:
            return bottles
        
        if transaction_type == 'delivered':
            # For delivery transactions, only in_stock bottles are allowed
            # OR bottles that are already in this transaction (to allow keeping them)
            current_bottle_ids = set()
            if transaction and transaction.pk:
                current_bottle_ids = set(transaction.bottles.values_list('id', flat=True))
            
            invalid_bottles = []
            for bottle in bottles:
                # Allow if in_stock or already in this transaction
                if bottle.status != 'in_stock' and bottle.id not in current_bottle_ids:
                    invalid_bottles.append(bottle.code)
            
            if invalid_bottles:
                raise forms.ValidationError(
                    f"The following bottles are already delivered and cannot be selected: {', '.join(invalid_bottles)}. "
                    "Only bottles with 'in_stock' status can be delivered. "
                    "Please return the bottles first before delivering them to another client."
                )
        
        elif transaction_type == 'returned':
            # For return transactions, only bottles delivered to this client are allowed
            # OR bottles that are already in this transaction
            client = self.cleaned_data.get('client')
            if not client:
                return bottles
            
            current_bottle_ids = set()
            if transaction and transaction.pk:
                current_bottle_ids = set(transaction.bottles.values_list('id', flat=True))
            
            invalid_bottles = []
            for bottle in bottles:
                # Skip validation for bottles already in this transaction
                if bottle.id in current_bottle_ids:
                    continue
                
                # Check if bottle is delivered to this client
                if bottle.status != 'delivered':
                    invalid_bottles.append(bottle.code)
                else:
                    # Verify bottle's LATEST delivered transaction is for this client
                    latest_delivered_txn = (
                        Transaction.objects.filter(bottles=bottle, transaction_type='delivered')
                        .order_by(Coalesce('custom_date', 'date').desc())
                        .first()
                    )
                    
                    if not latest_delivered_txn or latest_delivered_txn.client_id != client.id:
                        invalid_bottles.append(bottle.code)
            
            if invalid_bottles:
                raise forms.ValidationError(
                    f"The following bottles are not delivered to this client: {', '.join(invalid_bottles)}. "
                    "You can only return bottles that have been delivered to this client."
                )
        
        return bottles
    
class TransactionEditForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ['challan_number']


class BottlePricingForm(forms.ModelForm):
    class Meta:
        model = BottlePricing
        fields = ['price'] 
        

class BottleCategoryForm(forms.ModelForm):
    class Meta:
        model = BottleCategory
        fields = ['name', 'price']
        
        
# forms.py — replace ManualBillForm with this updated version

class ManualBillForm(forms.Form):
    """Form for creating manual bills (multi-row). Row inputs are submitted as arrays from template."""
    # Client + Bill metadata
    client = forms.ModelChoiceField(
        queryset=Client.objects.filter(role='customer', is_deleted=False),
        label='Client',
        empty_label="Select a Client"
    )

    bill_date = forms.DateTimeField(
        label='Bill Date',
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': 'form-control'},
            format='%Y-%m-%dT%H:%M:%S'   # important!
        ),
        initial=lambda: timezone.now()
    )

    # Keep gas_type so template can render a select for rows (multiple selects with same name => getlist in view)
    gas_type = forms.ModelChoiceField(
        queryset=BottleCategory.objects.all(),
        label='Gas Type (used to render a select in each row)',
        empty_label="Select Gas Type",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    discount_percentage = forms.DecimalField(
        label='Discount Percentage',
        max_digits=5, decimal_places=2,
        initial=Decimal('0.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )

    discount_amount = forms.DecimalField(
        label='Discount Amount',
        max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )

    taxable_amount = forms.DecimalField(
        label='Taxable Amount',
        max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )

    gst_percentage = forms.DecimalField(
        label='GST Percentage',
        max_digits=5, decimal_places=2,
        initial=Decimal('18.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )

    gst_amount = forms.DecimalField(
        label='GST Amount',
        max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )

    final_amount = forms.DecimalField(
        label='Final Amount',
        max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )

    description = forms.CharField(
        label='Description',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional description for the bill'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['bill_date'].initial = timezone.now()
        self.fields['gas_type'].queryset = BottleCategory.objects.all()
        self.fields['gas_type'].label_from_instance = lambda obj: f"{obj.name} (Rs. {obj.price})" if obj.price else obj.name

    def clean(self):
        cleaned = super().clean()
        # Basic validation only — row-level validations happen in the view (since rows arrive as arrays)
        discount_pct = cleaned.get('discount_percentage')
        gst_pct = cleaned.get('gst_percentage')
        if discount_pct is None:
            cleaned['discount_percentage'] = Decimal('0.00')
        if gst_pct is None:
            cleaned['gst_percentage'] = Decimal('18.00')
        return cleaned