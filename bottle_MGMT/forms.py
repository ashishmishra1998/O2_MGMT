from django import forms
from .models import Client, Transaction, Bottle, BottlePricing

class AddBottlesForm(forms.Form):
    series = forms.CharField(label='Series Prefix', max_length=5, help_text='e.g. SV or AV')
    start = forms.IntegerField(label='Start Number', min_value=1)
    end = forms.IntegerField(label='End Number', min_value=1)

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
        fields = ['name', 'contact', 'email', 'address']

    def clean_contact(self):
        contact = self.cleaned_data['contact']
        if not contact.isdigit() or len(contact) != 10:
            raise forms.ValidationError('Contact number must be exactly 10 digits.')
        return contact 

class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ['client', 'bottle', 'photo', 'transaction_type']

    def __init__(self, *args, **kwargs):
        transaction_type = kwargs.pop('transaction_type', None)
        super().__init__(*args, **kwargs)
        if transaction_type == 'delivered':
            self.fields['bottle'].queryset = Bottle.objects.filter(status='in_stock')
        elif transaction_type == 'returned':
            self.fields['bottle'].queryset = Bottle.objects.filter(status='delivered')
        else:
            self.fields['bottle'].queryset = Bottle.objects.all() 

class BottlePricingForm(forms.ModelForm):
    class Meta:
        model = BottlePricing
        fields = ['price'] 