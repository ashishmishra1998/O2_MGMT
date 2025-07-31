from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class Client(models.Model):
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=10)
    alt_contact = models.CharField(max_length=10, blank=True, null=True) 
    email = models.EmailField()
    address = models.TextField()
    company_name = models.CharField(max_length=150, blank=True, null=True)
    gst_number = models.CharField(max_length=20, blank=True, null=True)


    def __str__(self):
        return self.name

class Bottle(models.Model):
    STATUS_CHOICES = [
        ('in_stock', 'In Stock'),
        ('delivered', 'Delivered'),
        ('returned', 'Returned'),
    ]
    code = models.CharField(max_length=10, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='in_stock')

    def __str__(self):
        return f"Bottle {self.code}"

    @staticmethod
    def bulk_create_bottles(start=101, end=250):
        bottles = []
        for i in range(start, end + 1):
            code = f"SV-{i}"
            if not Bottle.objects.filter(code=code).exists():
                bottles.append(Bottle(code=code, status='in_stock'))
        Bottle.objects.bulk_create(bottles)

class BottlePricing(models.Model):
    price = models.DecimalField(max_digits=10, decimal_places=2, default=100)

    def __str__(self):
        return f"Bottle Price: {self.price}"

    @staticmethod
    def get_solo():
        obj, created = BottlePricing.objects.get_or_create(id=1)
        return obj

class Transaction(models.Model):
    TRANSACTION_TYPE = [
        ('delivered', 'Delivered'),
        ('returned', 'Returned'),
    ]
    bottle = models.ForeignKey(Bottle, on_delete=models.CASCADE)
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now_add=True)
    photo = models.ImageField(upload_to='bottle_photos/')
    delivered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE)
    billed = models.BooleanField(default=False)  # Track if this transaction has been billed

    def __str__(self):
        return f"{self.bottle} - {self.transaction_type} - {self.client}"

class Bill(models.Model):
    BILL_TYPE_CHOICES = [
        ('auto', 'Automated'),
        ('custom', 'Custom'),
    ]
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    bill_date = models.DateTimeField(auto_now_add=True)
    delivered_bottles = models.IntegerField()
    returned_bottles = models.IntegerField()
    pending_bottles = models.IntegerField()
    price_per_bottle = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    paid = models.BooleanField(default=False)
    paid_date = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='bills_paid')
    bill_type = models.CharField(max_length=10, choices=BILL_TYPE_CHOICES, default='auto')
    description = models.TextField(blank=True, null=True)  # For custom bill descriptions

    def __str__(self):
        return f"Bill for {self.client.name} - {self.bill_date.strftime('%Y-%m-%d')}"

    class Meta:
        ordering = ['-bill_date']

class BillTransaction(models.Model):
    """Model to track which transactions are included in custom bills"""
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='bill_transactions')
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='bill_transactions')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.bill} - {self.transaction}"

    class Meta:
        unique_together = ['bill', 'transaction']
