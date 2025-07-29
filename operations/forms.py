from django import forms
from django.contrib.gis.forms import PointField
from django.contrib.gis.geos import Point
from .models import Client, Assignment, User

class ClientUploadForm(forms.Form):
    """Form for uploading Excel file with client data"""
    file = forms.FileField(
        label="Excel File",
        help_text="Upload Excel file with columns: name, phone, email, address, latitude, longitude, priority",
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls'
        })
    )

    def clean_file(self):
        file = self.cleaned_data.get('file')
        if file:
            if not file.name.endswith(('.xlsx', '.xls')):
                raise forms.ValidationError("Please upload a valid Excel file (.xlsx or .xls)")

            # Check file size (5MB limit)
            if file.size > 5 * 1024 * 1024:
                raise forms.ValidationError("File size must be less than 5MB")

        return file

class ClientForm(forms.ModelForm):
    """Form for creating/editing clients"""
    latitude = forms.FloatField(
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': 'any',
            'placeholder': '12.9716'
        }),
        help_text="Enter latitude coordinate"
    )
    longitude = forms.FloatField(
        widget=forms.NumberInput(attrs={
            'class': 'form-control', 
            'step': 'any',
            'placeholder': '77.5946'
        }),
        help_text="Enter longitude coordinate"
    )

    class Meta:
        model = Client
        fields = ['name', 'phone', 'email', 'address', 'priority', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Pre-populate coordinates for existing clients
            if self.instance.location:
                self.fields['latitude'].initial = self.instance.location.y
                self.fields['longitude'].initial = self.instance.location.x

    def clean(self):
        cleaned_data = super().clean()
        latitude = cleaned_data.get('latitude')
        longitude = cleaned_data.get('longitude')

        if latitude is not None and longitude is not None:
            # Validate coordinate ranges
            if not (-90 <= latitude <= 90):
                raise forms.ValidationError("Latitude must be between -90 and 90")
            if not (-180 <= longitude <= 180):
                raise forms.ValidationError("Longitude must be between -180 and 180")

            # Create Point object
            cleaned_data['location'] = Point(longitude, latitude)

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Set location from coordinates
        latitude = self.cleaned_data.get('latitude')
        longitude = self.cleaned_data.get('longitude')

        if latitude is not None and longitude is not None:
            instance.location = Point(longitude, latitude)

        if commit:
            instance.save()

        return instance

class AssignmentForm(forms.ModelForm):
    """Form for creating/editing assignments"""

    class Meta:
        model = Assignment
        fields = ['agent', 'client', 'status', 'notes', 'estimated_duration']
        widgets = {
            'agent': forms.Select(attrs={'class': 'form-select'}),
            'client': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'estimated_duration': forms.TimeInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Filter agents to only show field agents
        self.fields['agent'].queryset = User.objects.filter(role='agent', is_active=True)

        # Filter clients to only show active clients
        self.fields['client'].queryset = Client.objects.filter(is_active=True)

class UserProfileForm(forms.ModelForm):
    """Form for updating user profile"""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }

class LocationUpdateForm(forms.Form):
    """Form for updating agent location"""
    latitude = forms.FloatField(
        widget=forms.HiddenInput()
    )
    longitude = forms.FloatField(
        widget=forms.HiddenInput()
    )
    accuracy = forms.FloatField(
        required=False,
        widget=forms.HiddenInput()
    )

    def clean(self):
        cleaned_data = super().clean()
        latitude = cleaned_data.get('latitude')
        longitude = cleaned_data.get('longitude')

        if latitude is not None and longitude is not None:
            # Validate coordinate ranges
            if not (-90 <= latitude <= 90):
                raise forms.ValidationError("Invalid latitude")
            if not (-180 <= longitude <= 180):
                raise forms.ValidationError("Invalid longitude")

        return cleaned_data

class ManualAssignmentForm(forms.Form):
    """Form for manually assigning a client to an agent"""
    agent = forms.ModelChoiceField(
        queryset=User.objects.filter(role='agent', is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Select an available agent"
    )
    client = forms.ModelChoiceField(
        queryset=Client.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Select a client to assign"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        help_text="Optional notes for the assignment"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Filter out agents who already have active assignments
        active_assignment_agents = Assignment.objects.filter(
            status__in=['assigned', 'in_progress']
        ).values_list('agent_id', flat=True)

        self.fields['agent'].queryset = User.objects.filter(
            role='agent', 
            is_active=True
        ).exclude(id__in=active_assignment_agents)

        # Filter out clients who are already assigned
        assigned_clients = Assignment.objects.filter(
            status__in=['assigned', 'in_progress']
        ).values_list('client_id', flat=True)

        self.fields['client'].queryset = Client.objects.filter(
            is_active=True
        ).exclude(id__in=assigned_clients)

class BulkAssignmentForm(forms.Form):
    """Form for bulk assignment of clients"""
    ASSIGNMENT_TYPES = (
        ('closest', 'Assign by proximity (closest first)'),
        ('priority', 'Assign by priority (highest priority first)'),
        ('balanced', 'Balanced assignment (distribute evenly)'),
    )

    assignment_type = forms.ChoiceField(
        choices=ASSIGNMENT_TYPES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='closest',
        help_text="Choose how to assign clients to available agents"
    )

    max_assignments_per_agent = forms.IntegerField(
        min_value=1,
        max_value=10,
        initial=3,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text="Maximum number of assignments per agent"
    )

    only_available_agents = forms.BooleanField(
        initial=True,
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Only assign to agents without current assignments"
    )

class ReportFilterForm(forms.Form):
    """Form for filtering reports"""
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        help_text="Start date for report"
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        help_text="End date for report"
    )
    agent = forms.ModelChoiceField(
        queryset=User.objects.filter(role='agent'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Filter by specific agent"
    )
    status = forms.ChoiceField(
        choices=[('', 'All Status')] + Assignment.STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Filter by assignment status"
    )
    client_priority = forms.ChoiceField(
        choices=[('', 'All Priorities')] + Client.PRIORITY_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Filter by client priority"
    )
