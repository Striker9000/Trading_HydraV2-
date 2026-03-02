"""
=============================================================================
Dashboard Routes - Flask endpoints for web interface
=============================================================================
Provides both page routes (HTML) and API routes (JSON) for the dashboard.

Page Routes:
- / : Main dashboard with real-time monitoring
- /logs : Log viewer with filtering
- /config : Configuration editor

API Routes:
- /api/status : Current system status (equity, P&L, positions)
- /api/bots : Bot status and configuration
- /api/bot/<bot_id>/toggle : Enable/disable a bot
- /api/halt : Get/set trading halt status
- /api/trade : Execute manual trade
- /api/liquidate : Liquidate all positions
- /api/logs : Fetch recent logs
- /api/config : Get/update configuration files
=============================================================================
"""

from flask import Blueprint, render_template, jsonify, request
import os
import sys
import json
from datetime import datetime

# Import trading system components
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_hydra.core.config import load_settings, load_bots_config
from trading_hydra.core.state import get_state, set_state
from trading_hydra.core.halt import get_halt_manager
from trading_hydra.core.clock import get_market_clock
from trading_hydra.services.alpaca_client import get_alpaca_client
from trading_hydra.services.decision_tracker import get_decision_tracker


# =============================================================================
# BLUEPRINTS - Organize routes into logical groups
# =============================================================================

main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__)


# =============================================================================
# PAGE ROUTES - Render HTML templates
# =============================================================================

@main_bp.route('/')
def index():
    """
    Main dashboard page with real-time monitoring.
    
    Displays:
    - Account equity and daily P&L
    - Current positions with unrealized P&L
    - Bot status (enabled/disabled)
    - Recent activity log
    """
    return render_template('index.html')


@main_bp.route('/logs')
def logs_page():
    """
    Log viewer page with filtering capabilities.
    
    Allows viewing and filtering of:
    - Trading activity logs
    - System events
    - Error messages
    """
    return render_template('logs.html')


@main_bp.route('/config')
def config_page():
    """
    Configuration editor page.
    
    Allows editing of:
    - settings.yaml (system configuration)
    - bots.yaml (bot configuration)
    """
    return render_template('config.html')


# =============================================================================
# API ROUTES - JSON endpoints for AJAX calls
# =============================================================================

@api_bp.route('/status')
def get_status():
    """
    Get current system status including account info and positions.
    
    Returns JSON with:
    - equity: Current account equity
    - day_pnl: Today's profit/loss
    - day_pnl_pct: Today's P&L as percentage
    - positions: List of current positions
    - is_halted: Whether trading is halted
    - uptime: System uptime
    """
    try:
        alpaca = get_alpaca_client()
        halt_manager = get_halt_manager()
        
        # Get account info
        account = alpaca.get_account()
        # Account is an Alpaca object with attributes, not a dictionary
        equity = float(getattr(account, 'equity', 0) or 0)
        last_equity = float(getattr(account, 'last_equity', equity) or equity)
        day_pnl = equity - last_equity
        day_pnl_pct = (day_pnl / last_equity * 100) if last_equity > 0 else 0
        
        # Get positions
        positions = alpaca.get_positions()
        position_list = []
        for pos in positions:
            # Use getattr for safe attribute access on Alpaca Position objects
            unrealized_pl = float(getattr(pos, 'unrealized_pl', 0) or 0)
            # unrealized_plpc may be named differently in newer API versions
            unrealized_plpc = float(getattr(pos, 'unrealized_plpc', None) or 
                                   getattr(pos, 'unrealized_pl_pc', 0) or 0)
            position_list.append({
                'symbol': getattr(pos, 'symbol', 'Unknown'),
                'qty': float(getattr(pos, 'qty', 0) or 0),
                'side': getattr(pos, 'side', 'long'),
                'market_value': float(getattr(pos, 'market_value', 0) or 0),
                'unrealized_pl': unrealized_pl,
                'unrealized_plpc': unrealized_plpc * 100,
                'current_price': float(getattr(pos, 'current_price', 0) or 0),
                'avg_entry_price': float(getattr(pos, 'avg_entry_price', 0) or 0)
            })
        
        # Get halt status
        is_halted = halt_manager.is_halted()
        halt_reason = halt_manager.get_status().reason if is_halted else None
        
        # Get uptime from state
        start_time = get_state('system.start_time')
        uptime_str = "Unknown"
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                uptime = get_market_clock().now() - start_dt
                hours, remainder = divmod(int(uptime.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{hours}h {minutes}m {seconds}s"
            except:
                pass
        
        return jsonify({
            'success': True,
            'equity': round(equity, 2),
            'day_pnl': round(day_pnl, 2),
            'day_pnl_pct': round(day_pnl_pct, 2),
            'positions': position_list,
            'position_count': len(position_list),
            'is_halted': is_halted,
            'halt_reason': halt_reason,
            'uptime': uptime_str,
            'timestamp': get_market_clock().now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'equity': 0,
            'day_pnl': 0,
            'positions': [],
            'is_halted': False
        }), 500


@api_bp.route('/bots')
def get_bots():
    """
    Get status of all configured bots.
    
    Returns JSON with list of bots and their:
    - bot_id: Unique identifier
    - type: momentum, crypto, or options
    - enabled: Whether bot is active (checks runtime state first, then config)
    - ticker(s): What the bot trades
    
    Note: Runtime state takes precedence over config file for enabled status.
    This allows toggling bots without editing the config file.
    """
    try:
        bots_config = load_bots_config()
        bots = []
        
        # Momentum bots
        for bot in bots_config.get('momentum_bots', []):
            bot_id = bot.get('bot_id')
            # Check runtime state first, fall back to config
            config_enabled = bot.get('enabled', False)
            runtime_enabled = get_state(f'bot.{bot_id}.enabled')
            enabled = runtime_enabled if runtime_enabled is not None else config_enabled
            
            bots.append({
                'bot_id': bot_id,
                'type': 'momentum',
                'enabled': enabled,
                'ticker': bot.get('ticker'),
                'direction': bot.get('direction', 'long_only')
            })
        
        # Options bot
        optionsbot = bots_config.get('optionsbot', {})
        if optionsbot:
            bot_id = optionsbot.get('bot_id', 'optionsbot')
            config_enabled = optionsbot.get('enabled', False)
            runtime_enabled = get_state(f'bot.{bot_id}.enabled')
            enabled = runtime_enabled if runtime_enabled is not None else config_enabled
            
            bots.append({
                'bot_id': bot_id,
                'type': 'options',
                'enabled': enabled,
                'tickers': optionsbot.get('tickers', [])
            })
        
        # Crypto bot
        cryptobot = bots_config.get('cryptobot', {})
        if cryptobot:
            bot_id = cryptobot.get('bot_id', 'cryptobot')
            config_enabled = cryptobot.get('enabled', False)
            runtime_enabled = get_state(f'bot.{bot_id}.enabled')
            enabled = runtime_enabled if runtime_enabled is not None else config_enabled
            
            bots.append({
                'bot_id': bot_id,
                'type': 'crypto',
                'enabled': enabled,
                'pairs': cryptobot.get('pairs', [])
            })
        
        # ExitBot status
        exitbot = bots_config.get('exitbot', {})
        config_enabled = exitbot.get('enabled', True)
        runtime_enabled = get_state('bot.exitbot.enabled')
        enabled = runtime_enabled if runtime_enabled is not None else config_enabled
        
        bots.append({
            'bot_id': 'exitbot',
            'type': 'risk',
            'enabled': enabled,
            'description': 'Monitors all positions with trailing stops'
        })
        
        return jsonify({
            'success': True,
            'bots': bots
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'bots': []
        }), 500


@api_bp.route('/decisions')
def get_decisions():
    """
    Get current decision states from all trading bots.
    
    Returns JSON with:
    - timestamp: When this snapshot was taken
    - bots: Dict of bot_id -> decision state containing:
        - signals: Current buy/sell/hold signals
        - blockers: Why trades aren't happening
        - exit_proximity: How close positions are to exits
    """
    try:
        tracker = get_decision_tracker()
        decisions = tracker.get_all_decisions()
        
        return jsonify({
            'success': True,
            **decisions
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'bots': {}
        }), 500


@api_bp.route('/bot/<bot_id>/toggle', methods=['POST'])
def toggle_bot(bot_id):
    """
    Toggle a bot's enabled status.
    
    Note: This updates the runtime state, not the config file.
    For permanent changes, use the config editor.
    """
    try:
        current_state = get_state(f'bot.{bot_id}.enabled', True)
        new_state = not current_state
        set_state(f'bot.{bot_id}.enabled', new_state)
        
        return jsonify({
            'success': True,
            'bot_id': bot_id,
            'enabled': new_state
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/halt', methods=['GET', 'POST'])
def halt_control():
    """
    Get or set trading halt status.
    
    GET: Returns current halt status
    POST: Toggle or set halt status
        - action: "toggle", "halt", or "resume"
    """
    halt_manager = get_halt_manager()
    
    if request.method == 'GET':
        status = halt_manager.get_status()
        return jsonify({
            'success': True,
            'is_halted': status.active,
            'reason': status.reason
        })
    
    try:
        data = request.get_json() or {}
        action = data.get('action', 'toggle')
        
        if action == 'toggle':
            if halt_manager.is_halted():
                halt_manager.clear_halt()
                is_halted = False
            else:
                halt_manager.set_halt("Manual halt via dashboard")
                is_halted = True
        elif action == 'halt':
            halt_manager.set_halt(data.get('reason', 'Manual halt via dashboard'))
            is_halted = True
        elif action == 'resume':
            halt_manager.clear_halt()
            is_halted = False
        else:
            return jsonify({
                'success': False,
                'error': f'Unknown action: {action}'
            }), 400
        
        return jsonify({
            'success': True,
            'is_halted': is_halted
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/trade', methods=['POST'])
def execute_trade():
    """
    Execute a manual trade.
    
    POST body:
    - symbol: Stock/crypto symbol
    - side: "buy" or "sell"
    - qty: Number of shares (optional if notional provided)
    - notional: Dollar amount (optional if qty provided)
    - force: Boolean to bypass halt check (default False)
    
    Note: Trades are blocked when system is halted unless force=True
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Check halt status - block trades when halted unless force=True
        halt_manager = get_halt_manager()
        force = data.get('force', False)
        if halt_manager.is_halted() and not force:
            return jsonify({
                'success': False, 
                'error': 'Trading is halted. Use force=True to override.'
            }), 403
        
        symbol = data.get('symbol', '').upper()
        side = data.get('side', '').lower()
        qty = data.get('qty')
        notional = data.get('notional')
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400
        if side not in ['buy', 'sell']:
            return jsonify({'success': False, 'error': 'Side must be buy or sell'}), 400
        if not qty and not notional:
            return jsonify({'success': False, 'error': 'Either qty or notional required'}), 400
        
        alpaca = get_alpaca_client()
        
        # Place the order
        if qty:
            order = alpaca.place_market_order(symbol=symbol, side=side, qty=float(qty))
        else:
            order = alpaca.place_market_order(symbol=symbol, side=side, notional=float(notional))
        
        return jsonify({
            'success': True,
            'order_id': order.get('id'),
            'symbol': symbol,
            'side': side,
            'status': order.get('status')
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/liquidate', methods=['POST'])
def liquidate_all():
    """
    Liquidate all positions (emergency function).
    
    Closes all open positions at market price.
    Use with caution!
    
    Note: This bypasses halt checks since it's an emergency function.
    """
    try:
        # Log the liquidation attempt for audit
        from trading_hydra.core.logging import get_logger
        logger = get_logger()
        logger.log("manual_liquidate_all", {"source": "dashboard"})
        
        alpaca = get_alpaca_client()
        
        # Get all positions
        positions = alpaca.get_positions()
        
        if not positions:
            return jsonify({
                'success': True,
                'message': 'No positions to liquidate',
                'closed': 0
            })
        
        closed = 0
        errors = []
        
        for pos in positions:
            try:
                side = 'sell' if pos.side == 'long' else 'buy'
                qty = abs(float(pos.qty))
                alpaca.place_market_order(symbol=pos.symbol, side=side, qty=qty)
                closed += 1
            except Exception as e:
                errors.append(f"{pos.symbol}: {str(e)}")
        
        return jsonify({
            'success': len(errors) == 0,
            'closed': closed,
            'errors': errors
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/logs')
def get_logs():
    """
    Get recent log entries.
    
    Query params:
    - limit: Number of entries (default 100)
    - level: Filter by level (info, warn, error)
    - search: Search term to filter logs
    """
    try:
        limit = int(request.args.get('limit', 100))
        level_filter = request.args.get('level', '').lower()
        search = request.args.get('search', '').lower()
        
        settings = load_settings()
        log_path = settings.get('system', {}).get('log_path', './logs/app.jsonl')
        
        logs = []
        
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                # Read last N lines efficiently
                lines = f.readlines()
                for line in reversed(lines[-limit*2:]):  # Read extra for filtering
                    if len(logs) >= limit:
                        break
                    try:
                        entry = json.loads(line.strip())
                        
                        # Apply filters
                        if level_filter and entry.get('level', '').lower() != level_filter:
                            continue
                        if search and search not in json.dumps(entry).lower():
                            continue
                        
                        logs.append(entry)
                    except:
                        continue
        
        return jsonify({
            'success': True,
            'logs': logs,
            'count': len(logs)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'logs': []
        }), 500


@api_bp.route('/config', methods=['GET', 'POST'])
def config_endpoint():
    """
    Get or update configuration files.
    
    GET: Returns contents of settings.yaml and bots.yaml
    POST: Updates a config file
        - file: "settings" or "bots"
        - content: New YAML content
    """
    import yaml
    
    if request.method == 'GET':
        try:
            settings_path = 'config/settings.yaml'
            bots_path = 'config/bots.yaml'
            
            settings_content = ""
            bots_content = ""
            
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    settings_content = f.read()
            
            if os.path.exists(bots_path):
                with open(bots_path, 'r') as f:
                    bots_content = f.read()
            
            return jsonify({
                'success': True,
                'settings': settings_content,
                'bots': bots_content
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # POST - update config
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        file_type = data.get('file')
        content = data.get('content')
        
        if file_type not in ['settings', 'bots']:
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        if not content:
            return jsonify({'success': False, 'error': 'Content required'}), 400
        
        # Validate YAML syntax
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            return jsonify({'success': False, 'error': f'Invalid YAML: {e}'}), 400
        
        # Write to file
        file_path = f'config/{file_type}.yaml'
        with open(file_path, 'w') as f:
            f.write(content)
        
        return jsonify({
            'success': True,
            'message': f'{file_type}.yaml updated'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================================================
# OPTIMIZER API ROUTES - ML + AI Config Optimization
# =============================================================================

@api_bp.route('/optimizer/status')
def get_optimizer_status():
    """Get auto-optimizer status and statistics."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        optimizer = get_auto_optimizer()
        
        return jsonify({
            'success': True,
            'stats': optimizer.get_statistics(),
            'pending_recommendations': len(optimizer.get_pending_recommendations())
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/analyze', methods=['POST'])
def analyze_bot():
    """Analyze a bot and generate optimization recommendations."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        
        data = request.get_json() or {}
        bot_id = data.get('bot_id', 'cryptobot')
        force = data.get('force', False)
        
        optimizer = get_auto_optimizer()
        report = optimizer.analyze_bot(bot_id, force=force)
        
        return jsonify({
            'success': True,
            'report': report.to_dict()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/analyze-all', methods=['POST'])
def analyze_all_bots():
    """Analyze all bots and generate optimization recommendations."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        
        data = request.get_json() or {}
        force = data.get('force', False)
        
        optimizer = get_auto_optimizer()
        reports = optimizer.analyze_all_bots(force=force)
        
        return jsonify({
            'success': True,
            'reports': {k: v.to_dict() for k, v in reports.items()}
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/recommendations')
def get_recommendations():
    """Get all pending optimization recommendations."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        optimizer = get_auto_optimizer()
        
        recommendations = optimizer.get_pending_recommendations()
        
        return jsonify({
            'success': True,
            'recommendations': [r.to_dict() for r in recommendations]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/apply', methods=['POST'])
def apply_recommendation():
    """Apply a specific optimization recommendation with safety validation."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer, OptimizationRecommendation
        from trading_hydra.core.config import load_settings
        
        settings = load_settings()
        tuner_config = settings.get("ml_config_tuner", {})
        if not tuner_config.get("enabled", True):
            return jsonify({'success': False, 'error': 'Config tuner is disabled in settings'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        dry_run = data.get('dry_run', True)
        rec_data = data.get('recommendation', {})
        
        if not rec_data:
            return jsonify({'success': False, 'error': 'Recommendation data required'}), 400
        
        if not dry_run and not tuner_config.get("auto_apply", False):
            min_conf = tuner_config.get("auto_apply_min_confidence", 0.85)
            if rec_data.get("confidence", 0) < min_conf:
                return jsonify({
                    'success': False, 
                    'error': f'Auto-apply disabled or confidence {rec_data.get("confidence", 0)} < {min_conf}'
                }), 403
        
        recommendation = OptimizationRecommendation(
            bot_id=rec_data.get('bot_id', ''),
            parameter_path=rec_data.get('parameter_path', ''),
            current_value=rec_data.get('current_value'),
            recommended_value=rec_data.get('recommended_value'),
            change_pct=rec_data.get('change_pct', 0),
            reason=rec_data.get('reason', ''),
            confidence=rec_data.get('confidence', 0),
            expected_improvement=rec_data.get('expected_improvement', ''),
            priority=rec_data.get('priority', 1)
        )
        
        optimizer = get_auto_optimizer()
        success, message = optimizer.apply_recommendation(recommendation, dry_run=dry_run)
        
        return jsonify({
            'success': success,
            'message': message,
            'dry_run': dry_run,
            'applied': not dry_run and success
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/compare/<bot_id>')
def compare_with_optimized(bot_id):
    """Compare current settings with optimized recommendations."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        optimizer = get_auto_optimizer()
        
        comparison = optimizer.compare_with_optimized(bot_id)
        
        return jsonify({
            'success': True,
            'comparison': comparison
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/optimizer/history')
def get_optimization_history():
    """Get history of applied optimizations."""
    try:
        from trading_hydra.services.auto_optimizer import get_auto_optimizer
        optimizer = get_auto_optimizer()
        
        limit = request.args.get('limit', 50, type=int)
        history = optimizer.get_applied_optimizations(limit=limit)
        
        return jsonify({
            'success': True,
            'history': history
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/health')
def get_health():
    """Get system health check status."""
    try:
        from trading_hydra.services.health_check import get_health_service
        health = get_health_service()
        
        result = health.check_all()
        
        return jsonify({
            'success': True,
            'health': result.to_dict()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/health/metrics')
def get_health_metrics():
    """Get Prometheus-compatible health metrics."""
    try:
        from trading_hydra.services.health_check import get_health_service
        health = get_health_service()

        metrics = health.get_metrics_export()

        lines = []
        for key, value in metrics.items():
            lines.append(f"{key} {value}")

        return '\n'.join(lines), 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return f"# Error: {str(e)}", 500, {'Content-Type': 'text/plain'}


# =============================================================================
# STREAMING DASHBOARD ROUTES - For YouTube/Live Trading Show
# =============================================================================

@main_bp.route('/streaming')
def streaming_dashboard():
    """
    Streaming-optimized dashboard for live trading show.

    Features:
    - Large, camera-friendly visuals
    - Real-time trade signals
    - Animated celebrations
    - Sound effects system
    - Optimized for OBS/YouTube streaming
    """
    return render_template('streaming.html')


@api_bp.route('/streaming/signals')
def get_streaming_signals():
    """
    Get next trade signals for streaming display.

    Returns upcoming trade opportunities with:
    - Entry price
    - Target price
    - Stop loss
    - Bot generating signal
    - Confidence score
    """
    try:
        # Get decision tracker data
        tracker = get_decision_tracker()

        # Get recent signals from decision tracker
        signals = []

        # Try to get signals from state
        pending_signals = get_state('streaming.pending_signals') or []

        # If no signals in state, create placeholder
        if not pending_signals:
            # Could check for actual bot signals here
            # For now return empty
            pass

        return jsonify({
            'success': True,
            'signals': pending_signals,
            'timestamp': get_market_clock().now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'signals': []
        }), 500


@api_bp.route('/streaming/positions')
def get_streaming_positions():
    """
    Get current positions optimized for streaming display.

    Includes:
    - Entry price and time
    - Current price and P&L
    - Target and stop levels
    - Progress to target
    - Time in trade
    """
    try:
        alpaca = get_alpaca_client()

        # Get positions from Alpaca
        positions = alpaca.get_positions()

        position_list = []
        for pos in positions:
            # Calculate additional streaming metrics
            entry_price = float(getattr(pos, 'avg_entry_price', 0) or 0)
            current_price = float(getattr(pos, 'current_price', 0) or 0)
            unrealized_pl = float(getattr(pos, 'unrealized_pl', 0) or 0)
            unrealized_plpc = float(getattr(pos, 'unrealized_plpc', None) or
                                   getattr(pos, 'unrealized_pl_pc', 0) or 0)

            # Estimate target (would ideally come from bot tracking)
            target_price = entry_price * 1.05  # Default +5% target
            stop_price = entry_price * 0.97    # Default -3% stop

            # Get entry time from state if available
            entry_time = get_state(f'position.{pos.symbol}.entry_time')

            position_list.append({
                'symbol': getattr(pos, 'symbol', 'Unknown'),
                'qty': float(getattr(pos, 'qty', 0) or 0),
                'side': getattr(pos, 'side', 'long'),
                'avg_entry_price': entry_price,
                'current_price': current_price,
                'unrealized_pl': unrealized_pl,
                'unrealized_plpc': unrealized_plpc * 100,
                'target_price': target_price,
                'stop_price': stop_price,
                'entry_time': entry_time or get_market_clock().now().isoformat(),
                'market_value': float(getattr(pos, 'market_value', 0) or 0)
            })

        return jsonify({
            'success': True,
            'positions': position_list,
            'count': len(position_list),
            'timestamp': get_market_clock().now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'positions': []
        }), 500


@api_bp.route('/streaming/performance')
def get_streaming_performance():
    """
    Get performance stats for streaming display.

    Returns:
    - Today/week/month P&L
    - Win rate
    - Bot performance breakdown
    - Recent wins
    """
    try:
        alpaca = get_alpaca_client()
        account = alpaca.get_account()

        # Account metrics
        equity = float(getattr(account, 'equity', 0) or 0)
        last_equity = float(getattr(account, 'last_equity', equity) or equity)
        day_pnl = equity - last_equity
        day_pnl_pct = (day_pnl / last_equity * 100) if last_equity > 0 else 0

        # Get bot stats from state
        bot_stats = []
        for bot_name in ['HailMary', 'TwentyMin', 'Options', 'Crypto', 'Bounce']:
            stats = get_state(f'bot.{bot_name.lower()}.stats') or {}
            bot_stats.append({
                'name': bot_name,
                'win_rate': stats.get('win_rate', 0),
                'total_pnl': stats.get('total_pnl', 0),
                'trades_today': stats.get('trades_today', 0),
                'profit_factor': stats.get('profit_factor', 0)
            })

        return jsonify({
            'success': True,
            'performance': {
                'equity': equity,
                'day_pnl': day_pnl,
                'day_pnl_pct': day_pnl_pct,
                'week_pnl': day_pnl * 3,  # Placeholder
                'win_rate': 58,  # Placeholder
                'bot_stats': bot_stats
            },
            'timestamp': get_market_clock().now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
