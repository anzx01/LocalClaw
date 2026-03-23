"""Start the web server."""

import uvicorn
from localclaw.channels.web import create_app

# Create the app (initialization happens in lifespan)
app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
