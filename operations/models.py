from django.contrib.auth.models import AbstractUser
from django.contrib.gis.db import models
from django.contrib.gis.geos import Point
from django.utils import timezone
import uuid

class User(AbstractUser):
    USER_ROLES = (
        ('manager', 'Manager'),
        ('agent', 'Field Agent'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=USER_ROLES, default='agent')
    phone = models.CharField(max_length=15, blank=True, null=True)
    current_location = models.PointField(null=True, blank=True, help_text="Current GPS location")
    is_active_agent = models.BooleanField(default=True, help_text="Is agent currently working")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auth_user'

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    @property
    def current_assignment(self):
        return self.assignments.filter(status='assigned').first()

class Client(models.Model):
    PRIORITY_CHOICES = (
        (1, 'Low'),
        (2, 'Medium'), 
        (3, 'High'),
        (4, 'Urgent'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField()
    location = models.PointField(help_text="Client GPS coordinates")
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=2)
    notes = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'name']
        indexes = [
            models.Index(fields=['priority']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.name} (Priority: {self.get_priority_display()})"

    @property
    def latitude(self):
        return self.location.y if self.location else None

    @property
    def longitude(self):
        return self.location.x if self.location else None

    @property
    def current_assignment(self):
        return self.assignments.filter(status__in=['assigned', 'in_progress']).first()

class Assignment(models.Model):
    STATUS_CHOICES = (
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='assignments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='assigned')
    assigned_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    estimated_duration = models.DurationField(null=True, blank=True)
    actual_duration = models.DurationField(null=True, blank=True)
    distance_to_client = models.FloatField(null=True, blank=True, help_text="Distance in kilometers")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_assignments')

    class Meta:
        ordering = ['-assigned_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['assigned_at']),
            models.Index(fields=['agent', 'status']),
        ]

    def __str__(self):
        return f"{self.agent.username} -> {self.client.name} ({self.get_status_display()})"

    def start_assignment(self):
        self.status = 'in_progress'
        self.started_at = timezone.now()
        self.save()

    def complete_assignment(self, notes=None):
        self.status = 'completed'
        self.completed_at = timezone.now()
        if self.started_at:
            self.actual_duration = self.completed_at - self.started_at
        if notes:
            self.notes = notes
        self.save()

class LocationHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(User, on_delete=models.CASCADE, related_name='location_history')
    location = models.PointField()
    timestamp = models.DateTimeField(auto_now_add=True)
    accuracy = models.FloatField(null=True, blank=True, help_text="GPS accuracy in meters")
    assignment = models.ForeignKey(Assignment, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['agent', 'timestamp']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.agent.username} at {self.timestamp}"

class NotificationLog(models.Model):
    NOTIFICATION_TYPES = (
        ('assignment', 'New Assignment'),
        ('update', 'Assignment Update'),
        ('completion', 'Assignment Completed'),
        ('system', 'System Notification'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient.username}"

    def mark_as_read(self):
        self.is_read = True
        self.read_at = timezone.now()
        self.save()

class SystemSettings(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return f"{self.key}: {self.value[:50]}"
