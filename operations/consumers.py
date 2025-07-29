import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from .models import Assignment, NotificationLog

User = get_user_model()

class AgentConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for field agents"""

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close()
            return

        if self.user.role != 'agent':
            await self.close()
            return

        # Join agent-specific group
        self.group_name = f'agent_{self.user.id}'
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

        # Send connection confirmation
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'message': 'Connected to Field Operations System',
            'agent_id': str(self.user.id),
            'agent_name': self.user.username
        }))

    async def disconnect(self, close_code):
        # Leave agent group
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        """Handle messages from WebSocket"""
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type')

            if message_type == 'location_update':
                await self.handle_location_update(text_data_json)
            elif message_type == 'assignment_status_update':
                await self.handle_assignment_status_update(text_data_json)
            elif message_type == 'ping':
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                    'timestamp': text_data_json.get('timestamp')
                }))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def handle_location_update(self, data):
        """Handle location update from agent"""
        try:
            latitude = float(data.get('latitude'))
            longitude = float(data.get('longitude'))
            accuracy = data.get('accuracy')

            # Update agent location in database
            await self.update_agent_location(latitude, longitude, accuracy)

            # Broadcast location to managers
            await self.channel_layer.group_send(
                'managers',
                {
                    'type': 'send_notification',
                    'data': {
                        'type': 'location_update',
                        'agent_id': str(self.user.id),
                        'agent_name': self.user.username,
                        'latitude': latitude,
                        'longitude': longitude,
                        'accuracy': accuracy
                    }
                }
            )

            # Confirm location update
            await self.send(text_data=json.dumps({
                'type': 'location_updated',
                'message': 'Location updated successfully'
            }))

        except (ValueError, TypeError) as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Invalid location data: {str(e)}'
            }))

    async def handle_assignment_status_update(self, data):
        """Handle assignment status update from agent"""
        try:
            assignment_id = data.get('assignment_id')
            new_status = data.get('status')
            notes = data.get('notes', '')

            if new_status not in ['in_progress', 'completed']:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Invalid status'
                }))
                return

            # Update assignment status
            success = await self.update_assignment_status(assignment_id, new_status, notes)

            if success:
                await self.send(text_data=json.dumps({
                    'type': 'assignment_status_updated',
                    'assignment_id': assignment_id,
                    'status': new_status,
                    'message': f'Assignment status updated to {new_status}'
                }))
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Failed to update assignment status'
                }))

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def send_notification(self, event):
        """Send notification to agent"""
        await self.send(text_data=json.dumps(event['data']))

    @database_sync_to_async
    def update_agent_location(self, latitude, longitude, accuracy):
        """Update agent location in database"""
        from django.contrib.gis.geos import Point
        from .models import LocationHistory

        location = Point(longitude, latitude)
        self.user.current_location = location
        self.user.save()

        # Save to location history
        LocationHistory.objects.create(
            agent=self.user,
            location=location,
            accuracy=accuracy,
            assignment=self.user.current_assignment
        )

    @database_sync_to_async
    def update_assignment_status(self, assignment_id, new_status, notes):
        """Update assignment status in database"""
        try:
            assignment = Assignment.objects.get(
                id=assignment_id,
                agent=self.user
            )

            if new_status == 'in_progress':
                assignment.start_assignment()
            elif new_status == 'completed':
                assignment.complete_assignment(notes)

            return True
        except Assignment.DoesNotExist:
            return False
        except Exception:
            return False

class ManagerConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for managers"""

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close()
            return

        if self.user.role != 'manager':
            await self.close()
            return

        # Join manager-specific group
        self.group_name = f'manager_{self.user.id}'
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        # Join general managers group
        await self.channel_layer.group_add(
            'managers',
            self.channel_name
        )

        await self.accept()

        # Send connection confirmation
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'message': 'Connected to Field Operations Management',
            'manager_id': str(self.user.id),
            'manager_name': self.user.username
        }))

    async def disconnect(self, close_code):
        # Leave groups
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

        await self.channel_layer.group_discard(
            'managers',
            self.channel_name
        )

    async def receive(self, text_data):
        """Handle messages from WebSocket"""
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type')

            if message_type == 'create_assignment':
                await self.handle_create_assignment(text_data_json)
            elif message_type == 'cancel_assignment':
                await self.handle_cancel_assignment(text_data_json)
            elif message_type == 'ping':
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                    'timestamp': text_data_json.get('timestamp')
                }))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def handle_create_assignment(self, data):
        """Handle assignment creation from manager"""
        try:
            agent_id = data.get('agent_id')
            client_id = data.get('client_id')
            notes = data.get('notes', '')

            # Create assignment
            assignment = await self.create_assignment(agent_id, client_id, notes)

            if assignment:
                # Notify agent
                await self.channel_layer.group_send(
                    f'agent_{agent_id}',
                    {
                        'type': 'send_notification',
                        'data': {
                            'type': 'new_assignment',
                            'assignment_id': str(assignment.id),
                            'client_name': assignment.client.name,
                            'client_address': assignment.client.address,
                            'client_phone': assignment.client.phone,
                            'priority': assignment.client.get_priority_display(),
                            'latitude': assignment.client.latitude,
                            'longitude': assignment.client.longitude,
                            'message': f'New assignment: {assignment.client.name}'
                        }
                    }
                )

                # Confirm to manager
                await self.send(text_data=json.dumps({
                    'type': 'assignment_created',
                    'assignment_id': str(assignment.id),
                    'message': 'Assignment created successfully'
                }))
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Failed to create assignment'
                }))

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def handle_cancel_assignment(self, data):
        """Handle assignment cancellation from manager"""
        try:
            assignment_id = data.get('assignment_id')
            reason = data.get('reason', 'Cancelled by manager')

            # Cancel assignment
            success = await self.cancel_assignment(assignment_id, reason)

            if success:
                assignment = await self.get_assignment(assignment_id)
                if assignment:
                    # Notify agent
                    await self.channel_layer.group_send(
                        f'agent_{assignment.agent.id}',
                        {
                            'type': 'send_notification',
                            'data': {
                                'type': 'assignment_cancelled',
                                'assignment_id': assignment_id,
                                'message': f'Assignment cancelled: {reason}'
                            }
                        }
                    )

                await self.send(text_data=json.dumps({
                    'type': 'assignment_cancelled',
                    'assignment_id': assignment_id,
                    'message': 'Assignment cancelled successfully'
                }))
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Failed to cancel assignment'
                }))

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def send_notification(self, event):
        """Send notification to manager"""
        await self.send(text_data=json.dumps(event['data']))

    @database_sync_to_async
    def create_assignment(self, agent_id, client_id, notes):
        """Create assignment in database"""
        from .models import Client

        try:
            agent = User.objects.get(id=agent_id, role='agent')
            client = Client.objects.get(id=client_id)

            # Check if agent already has active assignment
            if agent.current_assignment:
                return None

            # Check if client is already assigned
            if client.current_assignment:
                return None

            assignment = Assignment.objects.create(
                agent=agent,
                client=client,
                notes=notes,
                created_by=self.user
            )

            return assignment
        except Exception:
            return None

    @database_sync_to_async
    def cancel_assignment(self, assignment_id, reason):
        """Cancel assignment in database"""
        try:
            assignment = Assignment.objects.get(id=assignment_id)
            assignment.status = 'cancelled'
            assignment.notes = f"{assignment.notes}\n\nCancelled: {reason}" if assignment.notes else f"Cancelled: {reason}"
            assignment.save()
            return True
        except Assignment.DoesNotExist:
            return False
        except Exception:
            return False

    @database_sync_to_async
    def get_assignment(self, assignment_id):
        """Get assignment from database"""
        try:
            return Assignment.objects.select_related('agent', 'client').get(id=assignment_id)
        except Assignment.DoesNotExist:
            return None
