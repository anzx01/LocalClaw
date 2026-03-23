import sys
print("Python starting...")
print(f"Python version: {sys.version}")

try:
    print("Importing localclaw...")
    from localclaw.channels.web import create_app
    print("Import successful!")
    
    import uvicorn
    print("Starting server...")
    uvicorn.run("localclaw.channels.web:create_app", host="127.0.0.1", port=8016, factory=True)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
