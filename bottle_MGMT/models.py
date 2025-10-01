from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal

# Create your models here.
 
class Client(models.Model):
    ROLE_CHOICES = [
        ('customer', 'Customer'),
        ('admin', 'Admin'),
    ]
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=10)
    alt_contact = models.CharField(max_length=10, blank=True, null=True) 
    email = models.EmailField(blank=True, null=True)
    address = models.TextField()
    company_name = models.CharField(max_length=150, blank=True, null=True)
    gst_number = models.CharField(max_length=20, blank=True, null=True)
    
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='customer')
    # Admin profile fields
    owner_gst = models.CharField(max_length=20, blank=True, null=True)
    license_number = models.CharField(max_length=50, blank=True, null=True)
    bank_account = models.CharField(max_length=50, blank=True, null=True)
    card = models.CharField(max_length=50, blank=True, null=True)
    account_holder = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.CharField(max_length=30, blank=True, null=True)
    ifsc = models.CharField(max_length=15, blank=True, null=True)
    branch = models.CharField(max_length=100, blank=True, null=True)
    account_type = models.CharField(max_length=20, blank=True, null=True)
    mmid = models.CharField(max_length=20, blank=True, null=True)
    vpa = models.CharField("Virtual Payment Address", max_length=100, blank=True, null=True)
    upi_number = models.CharField(max_length=15, blank=True, null=True)
    upi_qr = models.ImageField(upload_to="upi_qr/", blank=True, null=True)
    
    # NEW: Admin-configurable HSN & C.U.M placeholders
    hsn_code = models.CharField(max_length=20, blank=True, null=True, default="28044090")
    cum_value = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('7.00'))
    
    # Soft delete field
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.role})"
    
    def soft_delete(self):
        """Soft delete the client"""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()
    
    def restore(self):
        """Restore the client"""
        self.is_deleted = False
        self.deleted_at = None
        self.save()

class BottleCategory(models.Model):
    name = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    def __str__(self):
        return f"{self.name} (Rs. {self.price})"

class Bottle(models.Model):
    STATUS_CHOICES = [
        ('in_stock', 'In Stock'),
        ('delivered', 'Delivered'),
    ]
    code = models.CharField(max_length=10, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='in_stock')
    category = models.ForeignKey(BottleCategory, on_delete=models.SET_DEFAULT, default=1)

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
    bottles = models.ManyToManyField(Bottle)
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    date = models.DateTimeField(default=timezone.now, help_text="Date and time of the transaction")
    custom_date = models.DateTimeField(null=True, blank=True, help_text="Optional custom date for the transaction. If not provided, current date/time will be used.")
    delivered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE)
    challan_number = models.IntegerField(null=True, blank=True, help_text="Challan number for this transaction")
    billed = models.BooleanField(default=False)  # Track if this transaction has been billed
    challan_number = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.bottle} - {self.transaction_type} - {self.client}"

class TransactionPhoto(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='bottle_photos/')

    def __str__(self):
        return f"Photo for Transaction {self.transaction.id}"

class Bill(models.Model):
    BILL_TYPE_CHOICES = [
        ('auto', 'Automated'),
        ('custom', 'Custom'),
        ('manual', 'Manual'),
    ]
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    bill_date = models.DateTimeField(default=timezone.now)
    delivered_bottles = models.IntegerField()
    returned_bottles = models.IntegerField()
    pending_bottles = models.IntegerField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_bottle = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    gst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'))
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    final_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    paid = models.BooleanField(default=False)
    paid_date = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='bills_paid')
    bill_type = models.CharField(max_length=10, choices=BILL_TYPE_CHOICES, default='auto')
    description = models.TextField(blank=True, null=True)  # For custom bill descriptions
    manual_bill = models.BooleanField(default=False)  # Flag to identify manual bills
    manual_gas_type = models.CharField(max_length=100, blank=True, null=True)  # Gas type for manual bills
    manual_challan_number = models.IntegerField(blank=True, null=True)  # Challan number for manual bills

    def __str__(self):
        return f"Bill for {self.client.name} - {self.bill_date.strftime('%Y-%m-%d')}"

    class Meta:
        ordering = ['-bill_date']


class ManualBillRow(models.Model):
    """
    Store per-row details for manual bills. Kept separate from Transaction so
    manual bills do not touch bottle inventory.
    """
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='manual_rows')
    date = models.DateField()
    # store gas type as name (human readable) to avoid FK coupling for historical/manual entries
    gas_type = models.CharField(max_length=100)
    challan_no = models.IntegerField(null=True, blank=True)
    hsn = models.CharField(max_length=20, blank=True, null=True, default="28044090")
    qty = models.IntegerField()
    cum = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('7.00'))
    total_qty = models.IntegerField(default=0)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"ManualRow(bill={self.bill_id}, gas={self.gas_type}, qty={self.qty}, challan={self.challan_no})"


class BillTransaction(models.Model):
    """Model to track which transactions are included in custom bills"""
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='bill_transactions')
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='bill_transactions')
    challan_number = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.bill} - {self.transaction}"

    class Meta:
        unique_together = ['bill', 'transaction']
