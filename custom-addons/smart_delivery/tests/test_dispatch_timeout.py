
from odoo.tests.common import TransactionCase
from odoo.tests import tagged
from odoo import fields
from datetime import timedelta
from unittest.mock import patch, MagicMock

@tagged('post_install', '-at_install')
class TestDispatchTimeout(TransactionCase):

    def setUp(self):
        super(TestDispatchTimeout, self).setUp()
        self.DeliveryOrder = self.env['delivery.order']
        self.Livreur = self.env['delivery.livreur']
        
        # Create a test livreur with FCM token
        self.livreur1 = self.Livreur.create({
            'name': 'Test Driver 1',
            'phone': '123456789',
            'vehicle_type': 'motorcycle',
            'registration_status': 'approved',
            'availability': True,
            'verified': True,
            'fcm_token': 'test_token_1',
            'current_lat': 18.0,
            'current_long': -15.0
        })
        
        # Create an order
        self.partner = self.env['res.partner'].create({'name': 'Test Client'})
        self.order = self.DeliveryOrder.create({
            'sector_type': 'standard',
            'sender_id': self.partner.id,
            'receiver_phone': '99999999',
            'pickup_lat': 18.01,
            'pickup_long': -15.01,
            'drop_lat': 18.02,
            'drop_long': -15.02,
        })

    @patch('odoo.addons.smart_delivery.models.delivery_order.fields.Datetime')
    @patch('odoo.addons.smart_delivery.models.delivery_order.DeliveryOrder._notify_livreurs')
    def test_global_timeout_cancellation(self, mock_notify, mock_datetime):
        """Test that order is cancelled after 3 minutes"""
        
        # 1. Start Dispatch at T=0
        start_time = fields.Datetime.now()
        # Mock now() to return start_time
        mock_datetime.now.return_value = start_time
        
        self.order.assign_livreur()
        
        self.assertEqual(self.order.status, 'dispatching')
        self.assertTrue(self.order.first_dispatch_time, "first_dispatch_time should be set")
        self.assertEqual(self.order.first_dispatch_time, start_time)
        
        # 2. Simulate 2 minutes later (Should NOT cancel)
        time_2_min = start_time + timedelta(minutes=2)
        mock_datetime.now.return_value = time_2_min
        
        self.order.process_dispatch_timeout()
        self.assertEqual(self.order.status, 'dispatching', "Order should still be dispatching after 2 mins")
        
        # 3. Simulate 4 minutes later (Should CANCEL)
        time_4_min = start_time + timedelta(minutes=4)
        mock_datetime.now.return_value = time_4_min
        
        self.order.process_dispatch_timeout()
        self.assertEqual(self.order.status, 'cancelled', "Order should be cancelled after 4 mins")

