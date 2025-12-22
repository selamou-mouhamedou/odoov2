# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from datetime import timedelta
from odoo import fields
from unittest.mock import patch, MagicMock

class TestDeliveryDispatch(TransactionCase):

    def setUp(self):
        super(TestDeliveryDispatch, self).setUp()
        self.DeliveryOrder = self.env['delivery.order']
        self.Livreur = self.env['delivery.livreur']
        self.Partner = self.env['res.partner']
        
        # Create a sender
        self.sender = self.Partner.create({'name': 'Test Sender', 'is_delivery_enterprise': True})
        
        # Create a standard sector
        self.sector = self.env['sector.rule'].create({
            'sector_type': 'standard',
            'name': 'Standard Sector'
        })
        
        # Create 15 livreurs at various locations
        self.livreurs = []
        for i in range(15):
            self.livreurs.append(self.Livreur.create({
                'name': f'Livreur {i}',
                'phone': f'12345678{i}',
                'email': f'livreur{i}@test.com',
                'nni': f'REF{i}',
                'vehicle_type': 'motorcycle',
                'current_lat': 18.0 + (i * 0.01), # varying locations
                'current_long': -15.0,
                'availability': True,
                'verified': True,
                'registration_status': 'approved',
                'sector_ids': [(4, self.sector.id)],
                'fcm_token': f'token_{i}'
            }))

        # Create an order
        self.order = self.DeliveryOrder.create({
            'sender_id': self.sender.id,
            'receiver_phone': '99999999',
            'pickup_lat': 18.0,
            'pickup_long': -15.0, # Near Livreur 0
            'drop_lat': 18.1,
            'drop_long': -15.1,
            'sector_type': 'standard',
            'dispatch_batch_size': 5 # Smaller batch for testing
        })

    @patch('smart_delivery.models.delivery_order.send_push_notification')
    def test_dispatch_flow(self, mock_send_push):
        """Test the full dispatch flow"""
        
        # 1. Start Dispatch
        self.order.assign_livreur()
        
        self.assertEqual(self.order.status, 'dispatching')
        self.assertEqual(len(self.order.current_batch_livreur_ids), 5)
        self.assertEqual(len(self.order.dispatched_livreur_ids), 5)
        
        # Check closest livreurs are picked first
        # Livreur 0 should be closest (same lat/long as pickup)
        self.assertIn(self.livreurs[0], self.order.current_batch_livreur_ids)
        
        # 2. Simulate Timeout and Next Batch
        # Manually trigger next batch
        self.order._dispatch_next_batch()
        
        self.assertEqual(len(self.order.current_batch_livreur_ids), 5) # New batch of 5
        self.assertEqual(len(self.order.dispatched_livreur_ids), 10) # Total 10 notified
        
        # Check that previous batch members are NOT in current batch
        self.assertNotIn(self.livreurs[0], self.order.current_batch_livreur_ids)
        
        # 3. Simulate Acceptance
        # Try to accept with a user from the second batch (e.g., 6th livreur)
        # Note: We sorted by distance, so index 5 should be roughly the 6th closest
        accepting_livreur = self.order.current_batch_livreur_ids[0]
        
        result = self.order.action_accept_delivery(accepting_livreur.id)
        
        self.assertTrue(result.get('success'))
        self.assertEqual(self.order.status, 'assigned')
        self.assertEqual(self.order.assigned_livreur_id, accepting_livreur)
        self.assertFalse(self.order.current_batch_livreur_ids) # Batch cleared

    @patch('smart_delivery.models.delivery_order.send_push_notification')
    def test_acceptance_restrictions(self, mock_send_push):
        """Test restrictions on acceptance"""
        self.order.assign_livreur()
        
        # Try to accept with a livreur who was NOT notified (e.g. Livreur 14, who is far away and not in first batch of 5)
        # Note: We created 15 livreurs, batch size 5. Livreur 14 should be last.
        far_livreur = self.livreurs[14]
        
        result = self.order.action_accept_delivery(far_livreur.id)
        self.assertIn('error', result)
        self.assertEqual(result.get('code'), 'NOT_AUTHORIZED')

