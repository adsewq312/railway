import os
from website import create_app
from website.socket import socketio

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port) 