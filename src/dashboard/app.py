import os
import sys
import json
import time
import threading
import subprocess
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request, redirect, url_for

current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from dotenv import load_dotenv
load_dotenv()

from trading_hydra.core.config import load_settings, load_bots_config, save_settings, save_bots_config
from trading_hydra.core.state import get_state, set_state, get_all_states
from trading_hydra.core.halt import get_halt_manager
from trading_hydra.services.alpaca_client import get_alpaca_client

app = Flask(__name__)


@app.route("/api")
def api_health():
    """Health check endpoint for Replit."""
    return jsonify({"status": "ok", "service": "trading-hydra-dashboard"})


_engine_process = None
_engine_start_time = None
_engine_lock = threading.Lock()


def get_engine_status():
    global _engine_process, _engine_start_time
    with _engine_lock:
        if _engine_process is None:
            return {"running": False, "uptime_seconds": 0}
        
        poll = _engine_process.poll()
        if poll is not None:
            _engine_process = None
            _engine_start_time = None
            return {"running": False, "uptime_seconds": 0}
        
        uptime = time.time() - _engine_start_time if _engine_start_time else 0
        return {"running": True, "uptime_seconds": int(uptime)}


def start_engine():
    global _engine_process, _engine_start_time
    with _engine_lock:
        if _engine_process is not None and _engine_process.poll() is None:
            return False
        
        _engine_process = subprocess.Popen(
            [sys.executable, "-m", "src.runner.main"],
            cwd=os.path.dirname(os.path.dirname(src_dir)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        _engine_start_time = time.time()
        return True


def stop_engine():
    global _engine_process, _engine_start_time
    with _engine_lock:
        if _engine_process is None:
            return False
        
        _engine_process.terminate()
        try:
            _engine_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _engine_process.kill()
        
        _engine_process = None
        _engine_start_time = None
        return True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    try:
        alpaca = get_alpaca_client()
        account = alpaca.get_account()
        
        day_start_equity = get_state("day_start_equity", account.equity)
        current_equity = account.equity
        daily_pnl = current_equity - day_start_equity
        daily_pnl_pct = (daily_pnl / day_start_equity * 100) if day_start_equity > 0 else 0
        
        halt_manager = get_halt_manager()
        halt_status = halt_manager.get_status()
        
        engine_status = get_engine_status()
        
        wins = get_state("stats.wins", 0)
        losses = get_state("stats.losses", 0)
        total_trades = wins + losses
        success_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        return jsonify({
            "equity": current_equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "day_start_equity": day_start_equity,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "halted": halt_status.active,
            "halt_reason": halt_status.reason if halt_status.active else None,
            "engine_running": engine_status["running"],
            "uptime_seconds": engine_status["uptime_seconds"],
            "wins": wins,
            "losses": losses,
            "total_trades": total_trades,
            "success_rate": success_rate,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/screening")
def api_screening():
    """Get current screening results - selected stocks and options."""
    try:
        selected_stocks = get_state("screener.selected_stocks", [])
        selected_options = get_state("screener.selected_options", [])
        last_run = get_state("screener.last_run", None)
        
        # Get detailed scores if available
        stock_scores = get_state("stock_screener.scores", [])
        options_scores = get_state("options_screener.scores", [])
        
        return jsonify({
            "selected_stocks": selected_stocks,
            "selected_options": selected_options,
            "last_run": last_run,
            "stock_scores": stock_scores,
            "options_scores": options_scores
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
def api_positions():
    try:
        alpaca = get_alpaca_client()
        positions = alpaca.get_positions()
        
        position_data = []
        for pos in positions:
            pnl_pct = (pos.unrealized_pl / pos.market_value * 100) if pos.market_value > 0 else 0
            position_data.append({
                "symbol": pos.symbol,
                "qty": float(pos.qty),
                "side": pos.side,
                "entry_price": pos.market_value / pos.qty if pos.qty != 0 else 0,
                "current_price": pos.market_value / pos.qty if pos.qty != 0 else 0,
                "market_value": float(pos.market_value),
                "unrealized_pnl": float(pos.unrealized_pl),
                "unrealized_pnl_pct": pnl_pct,
                "asset_class": "stock"
            })
        
        return jsonify({"positions": position_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bots")
def api_bots():
    try:
        bots_config = load_bots_config()
        
        bots_data = []
        
        # Parse momentum bots (list format)
        for bot in bots_config.get("momentum_bots", []):
            bots_data.append({
                "id": bot.get("bot_id", "unknown"),
                "name": f"Momentum - {bot.get('ticker', 'N/A')}",
                "enabled": bot.get("enabled", False),
                "tickers": [bot.get("ticker", "")],
                "asset_class": "stock"
            })
        
        # Parse cryptobot (dict format)
        crypto = bots_config.get("cryptobot", {})
        if crypto:
            bots_data.append({
                "id": "cryptobot",
                "name": "CryptoBot",
                "enabled": crypto.get("enabled", False),
                "tickers": crypto.get("pairs", []),
                "asset_class": "crypto"
            })
        
        # Parse optionsbot (dict format)
        options = bots_config.get("optionsbot", {})
        if options:
            bots_data.append({
                "id": "optionsbot",
                "name": "OptionsBot",
                "enabled": options.get("enabled", False),
                "tickers": options.get("tickers", []),
                "asset_class": "options"
            })
        
        # Parse exitbot
        exitbot = bots_config.get("exitbot", {})
        if exitbot:
            bots_data.append({
                "id": "exitbot",
                "name": "ExitBot (Monitor)",
                "enabled": exitbot.get("enabled", True),
                "tickers": [],
                "asset_class": "system"
            })
        
        # Parse portfoliobot
        portfolio = bots_config.get("portfoliobot", {})
        if portfolio:
            bots_data.append({
                "id": "portfoliobot",
                "name": "PortfolioBot (Allocator)",
                "enabled": portfolio.get("enabled", True),
                "tickers": [],
                "asset_class": "system"
            })
        
        return jsonify({"bots": bots_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bots/<bot_id>/toggle", methods=["POST"])
def api_toggle_bot(bot_id):
    try:
        bots_config = load_bots_config()
        
        if bot_id not in bots_config.get("bots", {}):
            return jsonify({"error": f"Bot {bot_id} not found"}), 404
        
        current = bots_config["bots"][bot_id].get("enabled", False)
        bots_config["bots"][bot_id]["enabled"] = not current
        
        save_bots_config(bots_config)
        
        return jsonify({"success": True, "enabled": not current})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bots/<bot_id>/tickers", methods=["POST"])
def api_update_tickers(bot_id):
    try:
        data = request.json
        tickers = data.get("tickers", [])
        
        bots_config = load_bots_config()
        
        if bot_id not in bots_config.get("bots", {}):
            return jsonify({"error": f"Bot {bot_id} not found"}), 404
        
        bots_config["bots"][bot_id]["tickers"] = tickers
        save_bots_config(bots_config)
        
        return jsonify({"success": True, "tickers": tickers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trade", methods=["POST"])
def api_trade():
    try:
        data = request.json
        symbol = data.get("symbol", "").upper()
        side = data.get("side", "").lower()
        amount = float(data.get("amount", 0))
        
        if not symbol or side not in ["buy", "sell"] or amount <= 0:
            return jsonify({"error": "Invalid trade parameters"}), 400
        
        alpaca = get_alpaca_client()
        
        order = alpaca.place_market_order(
            symbol=symbol,
            side=side,
            notional=amount,
            client_order_id=f"manual_{int(time.time())}"
        )
        
        return jsonify({
            "success": True,
            "order_id": order.get("id"),
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "status": order.get("status")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/liquidate/<symbol>", methods=["POST"])
def api_liquidate_position(symbol):
    try:
        alpaca = get_alpaca_client()
        result = alpaca.close_position(symbol.upper())
        
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/liquidate-all", methods=["POST"])
def api_liquidate_all():
    try:
        alpaca = get_alpaca_client()
        result = alpaca.close_all_positions()
        
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/halt/toggle", methods=["POST"])
def api_toggle_halt():
    try:
        halt_manager = get_halt_manager()
        status = halt_manager.get_status()
        
        if status.active:
            halt_manager.clear_halt()
            return jsonify({"success": True, "halted": False})
        else:
            halt_manager.set_halt("MANUAL_HALT: Triggered from dashboard")
            return jsonify({"success": True, "halted": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/engine/start", methods=["POST"])
def api_start_engine():
    try:
        result = start_engine()
        return jsonify({"success": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/engine/stop", methods=["POST"])
def api_stop_engine():
    try:
        result = stop_engine()
        return jsonify({"success": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs")
def api_logs():
    try:
        limit = int(request.args.get("limit", 100))
        log_file = "./logs/app.jsonl"
        
        if not os.path.exists(log_file):
            return jsonify({"logs": []})
        
        with open(log_file, "r") as f:
            lines = f.readlines()
        
        logs = []
        for line in lines[-limit:]:
            try:
                logs.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
        
        logs.reverse()
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        all_states = get_all_states()
        
        trades = []
        for key, value in all_states.items():
            if key.startswith("trades."):
                if isinstance(value, dict):
                    trades.append(value)
        
        trades.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return jsonify({"trades": trades[:50]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def api_config():
    try:
        settings = load_settings()
        bots_config = load_bots_config()
        
        return jsonify({
            "settings": settings,
            "bots": bots_config
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/settings", methods=["POST"])
def api_save_settings():
    try:
        data = request.json
        save_settings(data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/bots", methods=["POST"])
def api_save_bots():
    try:
        data = request.json
        save_bots_config(data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics")
def api_analytics():
    """
    Performance analytics endpoint.
    Returns comprehensive trading performance metrics.
    """
    try:
        all_states = get_all_states()
        
        wins = get_state("stats.wins", 0)
        losses = get_state("stats.losses", 0)
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        total_profit = float(get_state("stats.total_profit", 0))
        total_loss_raw = float(get_state("stats.total_loss", 0))
        total_loss = abs(total_loss_raw)
        net_pnl = total_profit - total_loss
        profit_factor = (total_profit / total_loss) if total_loss != 0 else 0
        avg_win = (total_profit / wins) if wins > 0 else 0
        avg_loss = (total_loss / losses) if losses > 0 else 0
        
        expectancy = 0
        if total_trades > 0:
            expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)
        
        trades = []
        for key, value in all_states.items():
            if key.startswith("trades.") and isinstance(value, dict):
                trades.append(value)
        
        trades.sort(key=lambda x: x.get("timestamp", 0))
        
        daily_pnl = {}
        for trade in trades:
            ts = trade.get("timestamp", 0)
            if ts > 0:
                date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                pnl = float(trade.get("pnl", 0))
                if date_str not in daily_pnl:
                    daily_pnl[date_str] = {"profit": 0, "loss": 0, "trades": 0}
                if pnl >= 0:
                    daily_pnl[date_str]["profit"] += pnl
                else:
                    daily_pnl[date_str]["loss"] += abs(pnl)
                daily_pnl[date_str]["trades"] += 1
        
        daily_data = []
        for date_str in sorted(daily_pnl.keys()):
            data = daily_pnl[date_str]
            daily_data.append({
                "date": date_str,
                "profit": data["profit"],
                "loss": data["loss"],
                "net": data["profit"] - data["loss"],
                "trades": data["trades"]
            })
        
        equity_curve = []
        running_pnl = 0
        for trade in trades:
            pnl = float(trade.get("pnl", 0))
            running_pnl += pnl
            equity_curve.append({
                "timestamp": trade.get("timestamp", 0),
                "cumulative_pnl": running_pnl
            })
        
        peak = 0
        max_drawdown = 0
        max_drawdown_pct = 0
        for point in equity_curve:
            pnl = point["cumulative_pnl"]
            if pnl > peak:
                peak = pnl
            drawdown = peak - pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0
        
        bot_stats = {}
        for trade in trades:
            bot_id = trade.get("bot_id", "unknown")
            if bot_id not in bot_stats:
                bot_stats[bot_id] = {"wins": 0, "losses": 0, "profit": 0, "loss": 0}
            pnl = float(trade.get("pnl", 0))
            if pnl >= 0:
                bot_stats[bot_id]["wins"] += 1
                bot_stats[bot_id]["profit"] += pnl
            else:
                bot_stats[bot_id]["losses"] += 1
                bot_stats[bot_id]["loss"] += abs(pnl)
        
        bot_performance = []
        for bot_id, stats in bot_stats.items():
            total = stats["wins"] + stats["losses"]
            bot_performance.append({
                "bot_id": bot_id,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "total_trades": total,
                "win_rate": (stats["wins"] / total * 100) if total > 0 else 0,
                "net_pnl": stats["profit"] - stats["loss"],
                "profit": stats["profit"],
                "loss": stats["loss"]
            })
        
        return jsonify({
            "summary": {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_profit": total_profit,
                "total_loss": total_loss,
                "net_pnl": net_pnl,
                "profit_factor": profit_factor,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "expectancy": expectancy,
                "max_drawdown": max_drawdown,
                "max_drawdown_pct": max_drawdown_pct
            },
            "daily_pnl": daily_data,
            "equity_curve": equity_curve,
            "bot_performance": bot_performance,
            "recent_trades": trades[-20:] if trades else []
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/export")
def api_export_trades():
    """
    Export trade history as CSV.
    """
    try:
        all_states = get_all_states()
        
        trades = []
        for key, value in all_states.items():
            if key.startswith("trades.") and isinstance(value, dict):
                trades.append(value)
        
        trades.sort(key=lambda x: x.get("timestamp", 0))
        
        csv_lines = ["timestamp,date,bot_id,symbol,side,entry_price,exit_price,notional,pnl,pnl_pct"]
        for trade in trades:
            ts = trade.get("timestamp", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else ""
            line = f'{ts},{date_str},{trade.get("bot_id", "")},{trade.get("symbol", "")},{trade.get("side", "")},{trade.get("entry_price", 0)},{trade.get("exit_price", 0)},{trade.get("notional", 0)},{trade.get("pnl", 0)},{trade.get("pnl_pct", 0)}'
            csv_lines.append(line)
        
        csv_content = "\n".join(csv_lines)
        
        from flask import Response
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=trade_history.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_dashboard(host="0.0.0.0", port=5000, debug=False):
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_dashboard(debug=True)
