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
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
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