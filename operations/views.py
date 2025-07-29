from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.contrib.gis.geos import Point
from django.contrib.gis.db.models.functions import Distance
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.core.paginator import Paginator
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
import pandas as pd
import requests
from .models import User, Client, Assignment, LocationHistory, NotificationLog
from .forms import ClientUploadForm, AssignmentForm
from django.conf import settings

def home(request):
    """Home page - redirects based on user role"""
    if not request.user.is_authenticated:
        return redirect('/admin/login/')

    if request.user.role == 'manager':
        return redirect('manager_dashboard')
    else:
        return redirect('agent_dashboard')

@login_required
def manager_dashboard(request):
    """Manager dashboard with overview and controls"""
    if request.user.role != 'manager':
        return redirect('agent_dashboard')

    # Get statistics
    total_agents = User.objects.filter(role='agent').count()
    active_agents = User.objects.filter(role='agent', is_active_agent=True).count()
    total_clients = Client.objects.filter(is_active=True).count()
    active_assignments = Assignment.objects.filter(status__in=['assigned', 'in_progress']).count()
    completed_today = Assignment.objects.filter(
        status='completed',
        completed_at__date=timezone.now().date()
    ).count()

    # Get recent assignments
    recent_assignments = Assignment.objects.select_related('agent', 'client').all()[:10]

    # Get agents with their current locations and assignments
    agents_data = []
    for agent in User.objects.filter(role='agent'):
        current_assignment = agent.current_assignment
        agents_data.append({
            'agent': agent,
            'current_assignment': current_assignment,
            'location': agent.current_location,
        })

    context = {
        'total_agents': total_agents,
        'active_agents': active_agents,
        'total_clients': total_clients,
        'active_assignments': active_assignments,
        'completed_today': completed_today,
        'recent_assignments': recent_assignments,
        'agents_data': agents_data,
    }

    return render(request, 'operations/manager_dashboard.html', context)

@login_required
def agent_dashboard(request):
    """Field agent dashboard with current assignment and map"""
    if request.user.role != 'agent':
        return redirect('manager_dashboard')

    current_assignment = request.user.current_assignment

    # Get agent's assignment history
    assignment_history = Assignment.objects.filter(
        agent=request.user
    ).select_related('client')[:10]

    # Get unread notifications
    unread_notifications = NotificationLog.objects.filter(
        recipient=request.user,
        is_read=False
    )[:5]

    context = {
        'current_assignment': current_assignment,
        'assignment_history': assignment_history,
        'unread_notifications': unread_notifications,
        'agent_location': request.user.current_location,
    }

    return render(request, 'operations/agent_dashboard.html', context)

@staff_member_required
def upload_clients(request):
    """Upload clients from Excel file"""
    if request.method == 'POST':
        form = ClientUploadForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['file']

            try:
                # Read Excel file
                df = pd.read_excel(excel_file)

                # Validate required columns
                required_columns = ['name', 'phone', 'address', 'latitude', 'longitude']
                missing_columns = [col for col in required_columns if col not in df.columns]

                if missing_columns:
                    messages.error(request, f"Missing columns: {', '.join(missing_columns)}")
                    return render(request, 'operations/upload_clients.html', {'form': form})

                # Process each row
                created_count = 0
                updated_count = 0

                for index, row in df.iterrows():
                    try:
                        # Create Point from coordinates
                        location = Point(float(row['longitude']), float(row['latitude']))

                        # Get or create client
                        client, created = Client.objects.get_or_create(
                            phone=str(row['phone']),
                            defaults={
                                'name': str(row['name']),
                                'address': str(row['address']),
                                'location': location,
                                'email': str(row.get('email', '')),
                                'priority': int(row.get('priority', 2)),
                                'notes': str(row.get('notes', '')),
                            }
                        )

                        if created:
                            created_count += 1
                        else:
                            # Update existing client
                            client.name = str(row['name'])
                            client.address = str(row['address'])
                            client.location = location
                            client.email = str(row.get('email', ''))
                            client.priority = int(row.get('priority', 2))
                            client.notes = str(row.get('notes', ''))
                            client.save()
                            updated_count += 1

                    except Exception as e:
                        messages.warning(request, f"Error processing row {index + 1}: {str(e)}")
                        continue

                messages.success(
                    request, 
                    f"Successfully processed {created_count} new clients and updated {updated_count} existing clients."
                )
                return redirect('manager_dashboard')

            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
    else:
        form = ClientUploadForm()

    return render(request, 'operations/upload_clients.html', {'form': form})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def auto_assign_client(request):
    """Automatically assign next client to agent based on proximity and priority"""
    agent_id = request.data.get('agent_id')
    assignment_type = request.data.get('type', 'closest')  # 'closest' or 'priority'

    try:
        agent = User.objects.get(id=agent_id, role='agent')

        if not agent.current_location:
            return Response(
                {'error': 'Agent location not available'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if agent already has an active assignment
        if agent.current_assignment:
            return Response(
                {'error': 'Agent already has an active assignment'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get available clients (not currently assigned)
        assigned_client_ids = Assignment.objects.filter(
            status__in=['assigned', 'in_progress']
        ).values_list('client_id', flat=True)

        available_clients = Client.objects.filter(
            is_active=True
        ).exclude(id__in=assigned_client_ids)

        if not available_clients.exists():
            return Response(
                {'error': 'No available clients for assignment'}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # Apply assignment logic
        if assignment_type == 'priority':
            # Priority-based assignment (highest priority first, then closest)
            clients_with_distance = available_clients.annotate(
                distance=Distance('location', agent.current_location)
            ).order_by('-priority', 'distance')
        else:
            # Distance-based assignment (closest first)
            clients_with_distance = available_clients.annotate(
                distance=Distance('location', agent.current_location)
            ).order_by('distance')

        selected_client = clients_with_distance.first()

        if not selected_client:
            return Response(
                {'error': 'No suitable client found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # Create assignment
        with transaction.atomic():
            assignment = Assignment.objects.create(
                agent=agent,
                client=selected_client,
                distance_to_client=selected_client.distance.km if hasattr(selected_client, 'distance') else None,
                created_by=request.user
            )

            # Send real-time notification
            send_assignment_notification(assignment)

        return Response({
            'message': 'Assignment created successfully',
            'assignment_id': str(assignment.id),
            'client_name': selected_client.name,
            'distance': selected_client.distance.km if hasattr(selected_client, 'distance') else None
        }, status=status.HTTP_201_CREATED)

    except User.DoesNotExist:
        return Response(
            {'error': 'Agent not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {'error': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_assignment_status(request, assignment_id):
    """Update assignment status"""
    try:
        assignment = get_object_or_404(Assignment, id=assignment_id)

        # Check permissions
        if request.user != assignment.agent and request.user.role != 'manager':
            return Response(
                {'error': 'Permission denied'}, 
                status=status.HTTP_403_FORBIDDEN
            )

        new_status = request.data.get('status')
        notes = request.data.get('notes', '')

        if new_status not in ['in_progress', 'completed', 'cancelled']:
            return Response(
                {'error': 'Invalid status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            if new_status == 'in_progress':
                assignment.start_assignment()
            elif new_status == 'completed':
                assignment.complete_assignment(notes)
            else:
                assignment.status = new_status
                assignment.notes = notes
                assignment.save()

            # Send real-time update
            send_assignment_update(assignment)

        return Response({
            'message': 'Assignment status updated successfully',
            'status': assignment.get_status_display()
        })

    except Exception as e:
        return Response(
            {'error': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_agent_location(request):
    """Update agent's current location"""
    try:
        latitude = float(request.data.get('latitude'))
        longitude = float(request.data.get('longitude'))
        accuracy = request.data.get('accuracy')

        location = Point(longitude, latitude)

        with transaction.atomic():
            # Update agent location
            request.user.current_location = location
            request.user.save()

            # Save location history
            LocationHistory.objects.create(
                agent=request.user,
                location=location,
                accuracy=accuracy,
                assignment=request.user.current_assignment
            )

            # Send real-time location update
            send_location_update(request.user, location)

        return Response({'message': 'Location updated successfully'})

    except (ValueError, TypeError) as e:
        return Response(
            {'error': 'Invalid coordinates'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return Response(
            {'error': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_route(request):
    """Get route between two points using OpenRouteService"""
    try:
        start_lat = float(request.GET.get('start_lat'))
        start_lng = float(request.GET.get('start_lng'))
        end_lat = float(request.GET.get('end_lat'))
        end_lng = float(request.GET.get('end_lng'))

        # OpenRouteService API call
        api_key = settings.OPENROUTESERVICE_API_KEY

        if api_key == 'your_openrouteservice_api_key_here':
            # Fallback: return straight line
            return Response({
                'coordinates': [[start_lng, start_lat], [end_lng, end_lat]],
                'distance': 0,
                'duration': 0,
                'instructions': ['Follow the route to destination']
            })

        url = "https://api.openrouteservice.org/v2/directions/driving-car"
        headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json'
        }

        data = {
            'coordinates': [[start_lng, start_lat], [end_lng, end_lat]],
            'format': 'geojson',
            'instructions': True
        }

        response = requests.post(url, json=data, headers=headers, timeout=10)

        if response.status_code == 200:
            route_data = response.json()
            if route_data['features']:
                feature = route_data['features'][0]
                return Response({
                    'coordinates': feature['geometry']['coordinates'],
                    'distance': feature['properties']['segments'][0]['distance'],
                    'duration': feature['properties']['segments'][0]['duration'],
                    'instructions': [step['instruction'] for step in feature['properties']['segments'][0]['steps']]
                })

        # Fallback: return straight line
        return Response({
            'coordinates': [[start_lng, start_lat], [end_lng, end_lat]],
            'distance': 0,
            'duration': 0,
            'instructions': ['Follow the route to destination']
        })

    except Exception as e:
        return Response(
            {'error': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

def send_assignment_notification(assignment):
    """Send real-time notification for new assignment"""
    channel_layer = get_channel_layer()

    notification_data = {
        'type': 'assignment_notification',
        'assignment_id': str(assignment.id),
        'client_name': assignment.client.name,
        'client_address': assignment.client.address,
        'client_phone': assignment.client.phone,
        'priority': assignment.client.get_priority_display(),
        'latitude': assignment.client.latitude,
        'longitude': assignment.client.longitude,
        'message': f'New assignment: {assignment.client.name}'
    }

    # Send to specific agent
    async_to_sync(channel_layer.group_send)(
        f'agent_{assignment.agent.id}',
        {
            'type': 'send_notification',
            'data': notification_data
        }
    )

    # Send to all managers
    managers = User.objects.filter(role='manager')
    for manager in managers:
        async_to_sync(channel_layer.group_send)(
            f'manager_{manager.id}',
            {
                'type': 'send_notification',
                'data': notification_data
            }
        )

def send_assignment_update(assignment):
    """Send real-time update for assignment status change"""
    channel_layer = get_channel_layer()

    update_data = {
        'type': 'assignment_update',
        'assignment_id': str(assignment.id),
        'status': assignment.status,
        'status_display': assignment.get_status_display(),
        'agent_name': assignment.agent.username,
        'client_name': assignment.client.name,
        'message': f'Assignment {assignment.get_status_display()}: {assignment.client.name}'
    }

    # Send to agent
    async_to_sync(channel_layer.group_send)(
        f'agent_{assignment.agent.id}',
        {
            'type': 'send_notification',
            'data': update_data
        }
    )

    # Send to all managers
    managers = User.objects.filter(role='manager')
    for manager in managers:
        async_to_sync(channel_layer.group_send)(
            f'manager_{manager.id}',
            {
                'type': 'send_notification',
                'data': update_data
            }
        )

def send_location_update(agent, location):
    """Send real-time location update"""
    channel_layer = get_channel_layer()

    location_data = {
        'type': 'location_update',
        'agent_id': str(agent.id),
        'agent_name': agent.username,
        'latitude': location.y,
        'longitude': location.x,
        'timestamp': timezone.now().isoformat()
    }

    # Send to all managers
    managers = User.objects.filter(role='manager')
    for manager in managers:
        async_to_sync(channel_layer.group_send)(
            f'manager_{manager.id}',
            {
                'type': 'send_notification',
                'data': location_data
            }
        )
