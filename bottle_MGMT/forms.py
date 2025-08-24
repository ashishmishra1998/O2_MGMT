from django import forms
from .models import Client, Transaction, Bottle, BottlePricing, BottleCategory

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
            'owner_gst', 'account_holder', 'account_number', 'ifsc', 'branch',
            'account_type', 'mmid', 'vpa', 'upi_number', 'upi_qr',
            'contact'
        ]

    def clean_contact(self):
        contact = self.cleaned_data['contact']
        if not contact.isdigit() or len(contact) != 10:
            raise forms.ValidationError('Contact number must be exactly 10 digits.')
        return contact
class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ['client', 'bottles', 'transaction_type']

    def __init__(self, *args, **kwargs):
        transaction_type = kwargs.pop('transaction_type', None)
        super().__init__(*args, **kwargs)
        if transaction_type == 'delivered':
            self.fields['bottles'].queryset = Bottle.objects.filter(status='in_stock')
        elif transaction_type == 'returned':
            self.fields['bottles'].queryset = Bottle.objects.filter(status='delivered')
        else:
            self.fields['bottles'].queryset = Bottle.objects.all()
    
    def clean(self):
        cleaned_data = super().clean()
        bottles = cleaned_data.get('bottles')
        photos = self.files.getlist('photos') if hasattr(self, 'files') else []
        print(f"Number of photos uploaded: {len(photos)}")
        print(f"Bottles selected: {bottles}")
        if bottles and len(photos) < bottles.count():
            raise forms.ValidationError(
                f'You must upload at least {bottles.count()} photo(s) for the selected bottles.'
            )
        return cleaned_data

class BottlePricingForm(forms.ModelForm):
    class Meta:
        model = BottlePricing
        fields = ['price'] 
        

class BottleCategoryForm(forms.ModelForm):
    class Meta:
        model = BottleCategory
        fields = ['name']