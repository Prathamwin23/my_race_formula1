from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.gis.admin import OSMGeoAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from import_export.admin import ImportExportModelAdmin
from import_export import resources
from .models import User, Client, Assignment, LocationHistory, NotificationLog, SystemSettings

# Custom User Admin
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'is_active_agent', 'current_location_display', 'last_login')
    list_filter = ('role', 'is_active', 'is_active_agent', 'is_staff', 'is_superuser')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'phone')
    ordering = ('username',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Field Operations', {
            'fields': ('role', 'phone', 'current_location', 'is_active_agent')
        }),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Field Operations', {
            'fields': ('role', 'phone', 'is_active_agent')
        }),
    )

    def current_location_display(self, obj):
        if obj.current_location:
            return f"({obj.current_location.y:.4f}, {obj.current_location.x:.4f})"
        return "Not available"
    current_location_display.short_description = "Current Location"

# Client Resource for Import/Export
class ClientResource(resources.ModelResource):
    class Meta:
        model = Client
        fields = ('name', 'phone', 'email', 'address', 'priority', 'notes', 'is_active')
        export_order = fields

# Client Admin with Map
class ClientAdmin(ImportExportModelAdmin, OSMGeoAdmin):
    resource_class = ClientResource
    list_display = ('name', 'phone', 'priority', 'address_short', 'current_assignment_status', 'is_active', 'created_at')
    list_filter = ('priority', 'is_active', 'created_at')
    search_fields = ('name', 'phone', 'email', 'address')
    readonly_fields = ('created_at', 'updated_at', 'current_assignment_link')
    ordering = ('-priority', 'name')

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'phone', 'email', 'priority')
        }),
        ('Location', {
            'fields': ('address', 'location')
        }),
        ('Additional', {
            'fields': ('notes', 'is_active')
        }),
        ('Assignment', {
            'fields': ('current_assignment_link',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def address_short(self, obj):
        return obj.address[:50] + "..." if len(obj.address) > 50 else obj.address
    address_short.short_description = "Address"

    def current_assignment_status(self, obj):
        assignment = obj.current_assignment
        if assignment:
            color = {
                'assigned': 'orange',
                'in_progress': 'blue', 
                'completed': 'green',
                'cancelled': 'red'
            }.get(assignment.status, 'gray')
            return format_html(
                '<span style="color: {};">{}</span>',
                color,
                assignment.get_status_display()
            )
        return "Not assigned"
    current_assignment_status.short_description = "Assignment Status"

    def current_assignment_link(self, obj):
        assignment = obj.current_assignment
        if assignment:
            url = reverse('admin:operations_assignment_change', args=[assignment.id])
            return format_html('<a href="{}">{}</a>', url, assignment)
        return "No active assignment"
    current_assignment_link.short_description = "Current Assignment"

# Assignment Admin
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ('agent', 'client', 'status', 'priority_display', 'distance_display', 'assigned_at', 'completed_at')
    list_filter = ('status', 'assigned_at', 'agent', 'client__priority')
    search_fields = ('agent__username', 'client__name', 'client__phone')
    readonly_fields = ('assigned_at', 'distance_to_client', 'actual_duration', 'created_by')
    ordering = ('-assigned_at',)
    date_hierarchy = 'assigned_at'

    fieldsets = (
        ('Assignment Details', {
            'fields': ('agent', 'client', 'status', 'created_by')
        }),
        ('Timing', {
            'fields': ('assigned_at', 'started_at', 'completed_at', 'estimated_duration', 'actual_duration')
        }),
        ('Additional Information', {
            'fields': ('notes', 'distance_to_client'),
            'classes': ('collapse',)
        }),
    )

    def priority_display(self, obj):
        return obj.client.get_priority_display()
    priority_display.short_description = "Client Priority"

    def distance_display(self, obj):
        if obj.distance_to_client:
            return f"{obj.distance_to_client:.2f} km"
        return "Not calculated"
    distance_display.short_description = "Distance"

    def save_model(self, request, obj, form, change):
        if not change:  # New assignment
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

# Location History Admin
class LocationHistoryAdmin(admin.ModelAdmin):
    list_display = ('agent', 'location_display', 'timestamp', 'accuracy', 'assignment')
    list_filter = ('agent', 'timestamp')
    search_fields = ('agent__username',)
    readonly_fields = ('timestamp',)
    ordering = ('-timestamp',)
    date_hierarchy = 'timestamp'

    def location_display(self, obj):
        return f"({obj.location.y:.4f}, {obj.location.x:.4f})"
    location_display.short_description = "Location"

    def has_add_permission(self, request):
        return False  # Don't allow manual creation

    def has_change_permission(self, request, obj=None):
        return False  # Don't allow editing

# Notification Log Admin
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'notification_type', 'title', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('recipient__username', 'title', 'message')
    readonly_fields = ('created_at', 'read_at')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Notification Details', {
            'fields': ('recipient', 'notification_type', 'title', 'message')
        }),
        ('Status', {
            'fields': ('is_read', 'created_at', 'read_at')
        }),
        ('Related', {
            'fields': ('assignment',),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        return False  # Don't allow manual creation

# System Settings Admin
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ('key', 'value_short', 'description_short', 'updated_at')
    search_fields = ('key', 'value', 'description')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('key',)

    def value_short(self, obj):
        return obj.value[:50] + "..." if len(obj.value) > 50 else obj.value
    value_short.short_description = "Value"

    def description_short(self, obj):
        return obj.description[:100] + "..." if len(obj.description) > 100 else obj.description
    description_short.short_description = "Description"

# Register models
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Client, ClientAdmin)
admin.site.register(Assignment, AssignmentAdmin)
admin.site.register(LocationHistory, LocationHistoryAdmin)
admin.site.register(NotificationLog, NotificationLogAdmin)
admin.site.register(SystemSettings, SystemSettingsAdmin)

# Customize admin site
admin.site.site_header = "Field Operations Management"
admin.site.site_title = "Field Operations"
admin.site.index_title = "Welcome to Field Operations Management System"

# Custom admin actions
def mark_clients_inactive(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, f"{updated} clients marked as inactive.")
mark_clients_inactive.short_description = "Mark selected clients as inactive"

def mark_clients_active(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, f"{updated} clients marked as active.")
mark_clients_active.short_description = "Mark selected clients as active"

def cancel_assignments(modeladmin, request, queryset):
    updated = queryset.filter(status__in=['assigned', 'in_progress']).update(status='cancelled')
    modeladmin.message_user(request, f"{updated} assignments cancelled.")
cancel_assignments.short_description = "Cancel selected assignments"

# Add actions to admin classes
ClientAdmin.actions = [mark_clients_inactive, mark_clients_active]
AssignmentAdmin.actions = [cancel_assignments]
