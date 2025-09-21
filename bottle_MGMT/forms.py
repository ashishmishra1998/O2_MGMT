from django import forms
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
        fields = ['client', 'bottles', 'transaction_type', 'custom_date']
        widgets = {
            'custom_date': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'form-control'
            })
        }

    def __init__(self, *args, **kwargs):
        transaction_type = kwargs.pop('transaction_type', None)
        super().__init__(*args, **kwargs)
        
        self.fields['custom_date'].required = False
        self.fields['custom_date'].help_text = "Optional: Leave blank to use current date/time"
        
        if transaction_type == 'delivered':
            self.fields['bottles'].queryset = Bottle.objects.filter(status='in_stock')
        elif transaction_type == 'returned':
            # Initially empty; filtered by client via AJAX
            self.fields['bottles'].queryset = Bottle.objects.none()
        else:
            self.fields['bottles'].queryset = Bottle.objects.all()


class BottlePricingForm(forms.ModelForm):
    class Meta:
        model = BottlePricing
        fields = ['price'] 
        

class BottleCategoryForm(forms.ModelForm):
    class Meta:
        model = BottleCategory
        fields = ['name', 'price']

class ManualBillForm(forms.Form):
    """Form for creating manual bills with all required fields"""
    
    # Client Information
    client = forms.ModelChoiceField(
        queryset=Client.objects.filter(role='customer', is_deleted=False),
        label='Client',
        empty_label="Select a Client"
    )
    
    # Bill Details
    bill_date = forms.DateTimeField(
        label='Bill Date',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        initial=lambda: timezone.now()
    )
    
    # Transaction Details (multiple rows)
    transaction_date = forms.DateField(
        label='Transaction Date',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    
    gas_type = forms.ModelChoiceField(
        queryset=BottleCategory.objects.all(),
        label='Gas Type',
        empty_label="Select Gas Type",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    challan_number = forms.IntegerField(
        label='Challan Number',
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    hsn_code = forms.CharField(
        label='HSN Code',
        max_length=20,
        initial='28044090',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    
    quantity = forms.IntegerField(
        label='Quantity',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    cum_value = forms.DecimalField(
        label='C.U.M',
        max_digits=6,
        decimal_places=2,
        initial=Decimal('7.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    
    total_quantity = forms.IntegerField(
        label='Total Quantity',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    rate_per_bottle = forms.DecimalField(
        label='Rate per Bottle',
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    
    # Financial Details
    subtotal_amount = forms.DecimalField(
        label='Subtotal Amount',
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'})
    )
    
    discount_percentage = forms.DecimalField(
        label='Discount Percentage',
        max_digits=5,
        decimal_places=2,
        initial=Decimal('0.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    
    discount_amount = forms.DecimalField(
        label='Discount Amount',
        max_digits=12,
        decimal_places=2,
        initial=Decimal('0.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'})
    )
    
    taxable_amount = forms.DecimalField(
        label='Taxable Amount',
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'})
    )
    
    gst_percentage = forms.DecimalField(
        label='GST Percentage',
        max_digits=5,
        decimal_places=2,
        initial=Decimal('18.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    
    gst_amount = forms.DecimalField(
        label='GST Amount',
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'})
    )
    
    final_amount = forms.DecimalField(
        label='Final Amount',
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'})
    )
    
    # Additional Information
    description = forms.CharField(
        label='Description',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional description for the bill'})
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set initial values
        self.fields['bill_date'].initial = timezone.now()
        
        # Customize gas type field to show prices
        self.fields['gas_type'].queryset = BottleCategory.objects.all()
        self.fields['gas_type'].label_from_instance = lambda obj: f"{obj.name} (Rs. {obj.price})" if obj.price else obj.name
        
    def clean(self):
        cleaned_data = super().clean()
        
        # Validate that total_quantity >= quantity
        quantity = cleaned_data.get('quantity')
        total_quantity = cleaned_data.get('total_quantity')
        if quantity and total_quantity and total_quantity < quantity:
            raise forms.ValidationError('Total quantity must be greater than or equal to quantity.')
        
        # Auto-calculate amounts
        quantity = cleaned_data.get('quantity', 0)
        rate = cleaned_data.get('rate_per_bottle', 0)
        discount_pct = cleaned_data.get('discount_percentage', 0)
        gst_pct = cleaned_data.get('gst_percentage', 18)
        
        if quantity and rate:
            subtotal = quantity * rate
            discount_amount = (subtotal * discount_pct / 100).quantize(Decimal('0.01'))
            taxable_amount = subtotal - discount_amount
            gst_amount = (taxable_amount * gst_pct / 100).quantize(Decimal('0.01'))
            final_amount = taxable_amount + gst_amount
            
            cleaned_data['subtotal_amount'] = subtotal
            cleaned_data['discount_amount'] = discount_amount
            cleaned_data['taxable_amount'] = taxable_amount
            cleaned_data['gst_amount'] = gst_amount
            cleaned_data['final_amount'] = final_amount
        
        return cleaned_data 