"""Start the web server."""

import argparse
import uvicorn
from localclaw.channels.web import create_app

# Create the app (initialization happens in lifespan)
app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the LocalClaw web server")
    parser.add_argument("--port", type=int, default=8080, help="Port to run the server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the server on")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
