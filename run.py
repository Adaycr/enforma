#!/usr/bin/env python3
"""
EnForma - Entry point
Run this file to start the local server.
"""
import sys
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent

def check_and_install_deps():
    """Check and install required Python packages."""
    required = [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn[standard]"),
        ("pydantic", "pydantic"),
        ("cryptography", "cryptography"),
        ("renpho", "renpho-api"),
    ]
    
    missing = []
    for module, package in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"📦 Installing missing packages: {', '.join(missing)}")
        # --break-system-packages only applies outside a virtualenv
        pip_cmd = [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            # Not in a venv — add the flag for system pip
            pip_cmd.insert(4, "--break-system-packages")
        subprocess.check_call(pip_cmd)
        print("✅ Packages installed.\n")


def main():
    print("═" * 52)
    print("  🏃 EnForma")
    print("═" * 52)
    
    check_and_install_deps()
    
    import uvicorn
    
    # Create assets dir if needed
    (BASE_DIR / "frontend" / "assets").mkdir(exist_ok=True)
    (BASE_DIR / "data").mkdir(exist_ok=True)
    
    print("\n🚀 Starting server at http://localhost:8000")
    print("   Press Ctrl+C to stop.\n")
    
    # Open browser after a short delay
    import threading, webbrowser, time
    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://localhost:8000")
    threading.Thread(target=open_browser, daemon=True).start()
    
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(BASE_DIR / "backend")],
        log_level="warning",
        app_dir=str(BASE_DIR)
    )


if __name__ == "__main__":
    main()
