
import firebase_admin
from firebase_admin import credentials, messaging
import os
import logging

logging.basicConfig(level=logging.INFO)

# Path from logs
JSON_PATH = os.path.join(os.path.dirname(__file__), "custom-addons", "smart_delivery", "data", "firebase_service_account.json")

print(f"Testing Firebase with key: {JSON_PATH}")

try:
    cred = credentials.Certificate(JSON_PATH)
    app = firebase_admin.initialize_app(cred)
    print("App initialized successfully.")
    
    # Try to send a dry-run message (doesn't actually send to device, just validates auth)
    # We need a token, but 'send_each_for_multicast' doesn't support dry_run easy check without tokens.
    # Let's try 'send' with a fake token and dry_run=True
    
    message = messaging.Message(
        token="fake_token_for_testing_auth",
        notification=messaging.Notification(
            title="Test",
            body="Test"
        )
    )
    
    # dry_run=True verifies permissions without sending
    response = messaging.send(message, dry_run=True)
    print("Dry run success! ID:", response)
    
except Exception as e:
    print("Error occurred:")
    print(e)
