"""
Webhook Server for UPIGateway Integration
Listens for successful payment callbacks and upgrades the user automatically.
"""

import logging
from flask import Flask, request, jsonify
from bot.monetization import monetization
from bot.config import UPIGATEWAY_API_KEY

app = Flask(__name__)
# Logs को डेटा फोल्डर में रखें ताकि main log के साथ देख सको
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@app.route('/', methods=['GET'])
def index():
    return "SaaS YouTube Uploader Payment Webhook is Active!", 200

@app.route('/webhook/upigateway', methods=['POST'])
def upigateway_webhook():
    """Endpoint to receive UPIGateway payment status."""
    try:
        # UPIGateway डेटा को प्रोसेस करें
        data = request.form.to_dict() or request.json
        
        if not data:
            return jsonify({"status": "failed", "msg": "No data received"}), 400

        client_txn_id = data.get("client_txn_id")
        amount = data.get("amount")
        status = data.get("status")
        user_id = data.get("udf1")  # हमने main.py में user_id यहाँ भेजा था
        
        logging.info(f"Received Webhook: TXN={client_txn_id}, Status={status}, User={user_id}, Amount={amount}")

        # अगर स्टेटस 'success' है तो टाइम ऐड करें
        if status == "success" and user_id:
            result = monetization.recharge(
                user_id=int(user_id),
                rupees=float(amount),
                payment_method="UPIGateway Auto",
                transaction_id=client_txn_id
            )
            
            if result["success"]:
                logging.info(f"✅ Successfully added time for User ID {user_id}")
            else:
                logging.error(f"❌ Failed to add time to database for User ID {user_id}")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"Webhook processing error: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

if __name__ == '__main__':
    # सर्वर 5000 पोर्ट पर चलेगा
    app.run(host='0.0.0.0', port=5000)
