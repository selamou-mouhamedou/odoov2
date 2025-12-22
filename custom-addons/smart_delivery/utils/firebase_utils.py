import logging
import json
import base64
import os
from odoo import tools, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Attempt to import firebase_admin
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    _logger.warning("firebase-admin library not found. Please install it with 'pip install firebase-admin'")

_firebase_app = None

def _get_firebase_app(env=None):
    """Initialize and return the Firebase app instance."""
    global _firebase_app
    
    if not FIREBASE_AVAILABLE:
        return None
        
    if _firebase_app:
        return _firebase_app
        
    try:
        # Check if already initialized by another module/thread
        # We use a unique name to avoid conflicts with Odoo's potential default app or other modules
        app_name = 'smart_delivery'
        
        try:
            _firebase_app = firebase_admin.get_app(name=app_name)
            return _firebase_app
        except ValueError:
            # App not initialized yet
            pass
            
        # 1. Try getting JSON content from System Parameter
        if not env:
            if request:
                env = request.env
        
        if not env:
            _logger.warning("No Odoo environment available to load Firebase config")
            return None
            
        json_content = env['ir.config_parameter'].sudo().get_param('smart_delivery.firebase_service_account_json')
        json_path = env['ir.config_parameter'].sudo().get_param('smart_delivery.firebase_json_path')
        
        _logger.info(f"Firebase Config - Content: {bool(json_content)}, Path: {json_path}")

        cred = None
        if json_content:
            try:
                # Handle case where JSON might be corrupted or have extra spaces
                cred_dict = json.loads(json_content)
                cred = credentials.Certificate(cred_dict)
            except Exception as e:
                _logger.error(f"Invalid Firebase JSON parameter: {e}")
        
        if not json_path:
            # Fallback: look for the file inside the module's data folder
            module_path = tools.config.get_module_path('smart_delivery')
            if module_path:
                potential_path = os.path.join(module_path, 'data', 'firebase_service_account.json')
                if os.path.exists(potential_path):
                    json_path = potential_path
                    _logger.info(f"Using default Firebase config at {json_path}")

        if json_path:
            # Set environment variable as fallback/primary for Google Auth libraries
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = json_path
            try:
                cred = credentials.Certificate(json_path)
            except Exception as e:
                _logger.error(f"Invalid Firebase JSON path '{json_path}': {e}")
        
        if cred:
            _firebase_app = firebase_admin.initialize_app(cred, name=app_name)
            _logger.info(f"Firebase app '{app_name}' initialized successfully.")
            return _firebase_app
        else:
            _logger.warning("Firebase credentials not configured in System Parameters (smart_delivery.firebase_service_account_json)")
            return None
            
    except Exception as e:
        _logger.error(f"Error initializing Firebase: {e}")
        return None

def send_push_notification(tokens, title, body, data=None, env=None):
    """
    Send FCM push notification to multiple tokens.
    """
    if not tokens:
        return None
    
    app = _get_firebase_app(env=env)
    if not app:
        _logger.warning("Firebase app not initialized. Notification skipped.")
        return {'success': False, 'error': 'Firebase not configured'}

    # Prepare messages
    # Note: MulticastMessage is for sending to multiple tokens
    # But firebase-admin recommended way for high throughput is send_each_for_multicast
    
    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=data or {},
    )
    
    try:
        response = messaging.send_each_for_multicast(message, app=app)
        _logger.info(f"FCM: Sent {response.success_count} messages, {response.failure_count} failed.")
        
        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    # Clean up invalid tokens if necessary
                    # invalid_token = tokens[idx]
                    _logger.warning(f"FCM Failure: {resp.exception}")
                    
        return {
            "success": True, 
            "success_count": response.success_count, 
            "failure_count": response.failure_count
        }
    except Exception as e:
        _logger.error(f"FCM Send Error: {e}")
        return {"success": False, "error": str(e)}
