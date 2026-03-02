"""
=============================================================================
Trading Hydra Dashboard - Flask Web Interface
=============================================================================
Provides real-time monitoring and control of the trading system via web browser.

Features:
- Real-time equity, P&L, and position display
- Bot status monitoring with enable/disable controls
- Manual trading interface (buy/sell/liquidate)
- Trading halt toggle (emergency stop)
- Live log viewer with filtering
- Configuration editor for settings.yaml and bots.yaml

Access at: http://0.0.0.0:5000
=============================================================================
"""

from flask import Flask
import os
import sys

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def create_app():
    """
    Create and configure the Flask application.
    
    Returns:
        Flask application instance configured with all routes
    """
    # Create Flask app with template and static folders
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    
    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir
    )
    
    # Secret key for session management
    app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-prod')
    
    # Register blueprints/routes
    from .routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    
    return app
