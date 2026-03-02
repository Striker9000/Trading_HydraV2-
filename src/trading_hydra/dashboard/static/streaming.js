/**
 * Trading Hydra - Streaming Dashboard JavaScript
 * Real-time updates, animations, and sound system
 */

// ============= CONFIGURATION =============
const CONFIG = {
    updateInterval: 2000, // Update every 2 seconds
    soundEnabled: true,
    masterVolume: 0.7,
    musicVolume: 0.3,
    celebrationsEnabled: true,
    confettiEnabled: true,
    showDemoButton: true
};

// Sound file mappings
const SOUND_FILES = {
    signal: {
        chime: '/static/sounds/chime.mp3',
        bell: '/static/sounds/bell.mp3',
        ding: '/static/sounds/ding.mp3',
        none: null
    },
    entry: {
        click: '/static/sounds/click.mp3',
        pop: '/static/sounds/pop.mp3',
        whoosh: '/static/sounds/whoosh.mp3',
        none: null
    },
    profit: {
        kaching: '/static/sounds/kaching.mp3',
        success: '/static/sounds/success.mp3',
        coins: '/static/sounds/coins.mp3',
        none: null
    },
    stoploss: {
        buzz: '/static/sounds/buzz.mp3',
        error: '/static/sounds/error.mp3',
        thud: '/static/sounds/thud.mp3',
        none: null
    }
};

// ============= STATE =============
let lastData = null;
let previousPositions = new Set();

// ============= INITIALIZATION =============
document.addEventListener('DOMContentLoaded', () => {
    initializeSettings();
    startUpdates();
    updateClock();
    setInterval(updateClock, 1000);
});

// ============= CLOCK =============
function updateClock() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    });
    document.getElementById('current-time').textContent = timeStr;
}

// ============= DATA UPDATES =============
function startUpdates() {
    updateDashboard();
    setInterval(updateDashboard, CONFIG.updateInterval);
}

async function updateDashboard() {
    try {
        // Fetch data from API
        const [statusData, positionsData, signalsData] = await Promise.all([
            fetchJSON('/api/status'),
            fetchJSON('/api/streaming/positions'),
            fetchJSON('/api/streaming/signals')
        ]);

        // Update scoreboard
        updateScoreboard(statusData);

        // Update spotlight (next signal)
        updateSpotlight(signalsData);

        // Update live positions
        updatePositions(positionsData);

        // Update options view
        updateOptionsView(signalsData, positionsData);

        // Check for new events (trades, wins, etc.)
        checkForEvents(positionsData);

        lastData = { statusData, positionsData, signalsData };

    } catch (error) {
        console.error('Dashboard update failed:', error);
    }
}

async function fetchJSON(url) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
}

// ============= SCOREBOARD =============
function updateScoreboard(data) {
    if (!data || !data.success) return;

    // Today P&L
    const todayPnl = data.day_pnl || 0;
    const todayEl = document.getElementById('today-pnl');
    todayEl.textContent = formatCurrency(todayPnl);
    todayEl.className = 'stat-value ' + (todayPnl >= 0 ? 'positive' : 'negative');

    // Week P&L (placeholder - would need actual data)
    const weekPnl = todayPnl * 3; // Simulated
    const weekEl = document.getElementById('week-pnl');
    weekEl.textContent = formatCurrency(weekPnl);
    weekEl.className = 'stat-value ' + (weekPnl >= 0 ? 'positive' : 'negative');

    // Win rate (would need trade history)
    document.getElementById('win-rate').textContent = '58%'; // Placeholder

    // Active positions count
    document.getElementById('active-count').textContent = data.position_count || 0;
}

// ============= SPOTLIGHT =============
function updateSpotlight(data) {
    const container = document.getElementById('spotlight-content');

    if (!data || !data.signals || data.signals.length === 0) {
        // No signals - show scanning
        container.innerHTML = `
            <div class="scanning-indicator">
                <div style="font-size: 3rem; margin-bottom: 1rem;">🔍</div>
                <div>Scanning for opportunities<span class="scanning-dots"></span></div>
            </div>
        `;
        return;
    }

    // Show next signal
    const signal = data.signals[0];
    const entryPrice = signal.entry_price || 0;
    const targetPrice = signal.target_price || 0;
    const stopPrice = signal.stop_price || 0;
    const targetPct = ((targetPrice - entryPrice) / entryPrice * 100).toFixed(1);
    const stopPct = ((stopPrice - entryPrice) / entryPrice * 100).toFixed(1);

    container.innerHTML = `
        <div class="setup-card">
            <div class="setup-symbol">${signal.symbol}</div>

            <div class="setup-prices">
                <div class="price-box">
                    <div class="price-label">💰 ENTRY</div>
                    <div class="price-value entry">$${entryPrice.toFixed(2)}</div>
                </div>
                <div class="price-box">
                    <div class="price-label">🎯 TARGET</div>
                    <div class="price-value target">$${targetPrice.toFixed(2)}</div>
                    <div style="font-size: 0.9rem; color: #10b981; margin-top: 0.3rem;">+${targetPct}%</div>
                </div>
                <div class="price-box">
                    <div class="price-label">🛡️ STOP</div>
                    <div class="price-value stop">$${stopPrice.toFixed(2)}</div>
                    <div style="font-size: 0.9rem; color: #ef4444; margin-top: 0.3rem;">${stopPct}%</div>
                </div>
            </div>

            <div class="setup-meta">
                <div>
                    <span style="color: #9ca3af;">🤖 Bot:</span>
                    <span style="font-weight: bold;">${signal.bot || 'Unknown'}</span>
                </div>
                <div>
                    <span style="color: #9ca3af;">Position:</span>
                    <span style="font-weight: bold;">${signal.position_size || '$500'}</span>
                </div>
            </div>

            ${signal.confidence ? `
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width: ${signal.confidence}%">
                        ${signal.confidence}% 🔥
                    </div>
                </div>
            ` : ''}

            <div style="text-align: center; margin-top: 1.5rem; font-size: 1.2rem; color: #3b82f6;">
                ⏰ ${signal.status || 'WAITING FOR ENTRY...'}
            </div>
        </div>
    `;

    // Play signal sound if new
    if (lastData && !lastData.signalsData?.signals?.find(s => s.symbol === signal.symbol)) {
        playSound('signal');
    }
}

// ============= LIVE POSITIONS =============
function updatePositions(data) {
    const container = document.getElementById('positions-content');

    if (!data || !data.positions || data.positions.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 3rem; color: #9ca3af;">
                No active positions
            </div>
        `;
        previousPositions.clear();
        return;
    }

    const positions = data.positions;
    let html = '';

    positions.forEach(pos => {
        const pnl = pos.unrealized_pl || 0;
        const pnlPct = pos.unrealized_plpc || 0;
        const isWinning = pnl >= 0;
        const cardClass = isWinning ? 'winning' : 'losing';

        // Calculate time in position
        const entryTime = pos.entry_time ? new Date(pos.entry_time) : new Date();
        const now = new Date();
        const minutesInTrade = Math.floor((now - entryTime) / 60000);
        const timeStr = formatDuration(minutesInTrade);

        // Calculate progress to target
        const currentPrice = pos.current_price || 0;
        const entryPrice = pos.avg_entry_price || 0;
        const targetPrice = pos.target_price || (entryPrice * 1.05); // Default +5%
        const progress = Math.min(100, Math.max(0,
            ((currentPrice - entryPrice) / (targetPrice - entryPrice)) * 100
        ));

        html += `
            <div class="position-card ${cardClass}">
                <div class="position-header">
                    <div>
                        <div class="position-symbol">${pos.symbol}</div>
                        <div class="position-time">${timeStr}</div>
                    </div>
                    <div class="position-pnl ${isWinning ? 'positive' : 'negative'}">
                        ${formatCurrency(pnl)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)
                    </div>
                </div>

                <div class="position-details">
                    <div>Entry: $${entryPrice.toFixed(2)}</div>
                    <div>Now: $${currentPrice.toFixed(2)}</div>
                    <div>Target: $${targetPrice.toFixed(2)}</div>
                    <div>Stop: $${(pos.stop_price || entryPrice * 0.95).toFixed(2)}</div>
                </div>

                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>
                <div class="progress-label">
                    <span>To Target</span>
                    <span>${progress.toFixed(0)}%</span>
                </div>
            </div>
        `;

        // Check if this is a new position
        if (!previousPositions.has(pos.symbol)) {
            playSound('entry');
        }

        // Check if target hit (position about to close)
        if (progress >= 95 && !pos.notified_target) {
            playSound('profit');
            showCelebration(pos.symbol, pnl, pnlPct);
            pos.notified_target = true;
        }

        previousPositions.add(pos.symbol);
    });

    container.innerHTML = html;

    // Remove closed positions from tracking
    const currentSymbols = new Set(positions.map(p => p.symbol));
    previousPositions.forEach(symbol => {
        if (!currentSymbols.has(symbol)) {
            previousPositions.delete(symbol);
        }
    });
}

// ============= EVENTS & CELEBRATIONS =============
function checkForEvents(data) {
    // This would check for completed trades, big wins, etc.
    // For now, handled in updatePositions when target hit
}

function showCelebration(symbol, pnl, pnlPct) {
    if (!CONFIG.celebrationsEnabled) return;

    // MONEY SHOWER for big wins ($100+)
    if (CONFIG.confettiEnabled && pnl >= 100) {
        moneyShower(pnl);
    }
    // Regular confetti for smaller wins
    else if (CONFIG.confettiEnabled && pnl > 20) {
        showConfetti();
    }

    console.log(`🎉 ${symbol} hit target! +${formatCurrency(pnl)} (${pnlPct.toFixed(1)}%)`);
}

function showConfetti() {
    const overlay = document.getElementById('celebration-overlay');
    const colors = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6'];

    // Create confetti particles
    for (let i = 0; i < 50; i++) {
        setTimeout(() => {
            const confetti = document.createElement('div');
            confetti.className = 'confetti';
            confetti.style.left = Math.random() * 100 + '%';
            confetti.style.background = colors[Math.floor(Math.random() * colors.length)];
            confetti.style.animationDelay = Math.random() * 0.5 + 's';
            overlay.appendChild(confetti);

            setTimeout(() => confetti.remove(), 3000);
        }, i * 50);
    }
}

// MONEY SHOWER - Enhanced celebration for big wins!
function moneyShower(amount) {
    const overlay = document.getElementById('celebration-overlay');

    // Create money rain (dollar bills falling)
    for (let i = 0; i < 30; i++) {
        setTimeout(() => {
            const bill = document.createElement('div');
            bill.innerHTML = '💵';
            bill.style.position = 'absolute';
            bill.style.left = Math.random() * 100 + '%';
            bill.style.top = '-50px';
            bill.style.fontSize = (20 + Math.random() * 30) + 'px';
            bill.style.animation = `money-fall ${2 + Math.random() * 2}s linear`;
            bill.style.opacity = '0.9';
            bill.style.zIndex = '10000';
            overlay.appendChild(bill);

            setTimeout(() => bill.remove(), 4000);
        }, i * 100);
    }

    // Add money explosions
    for (let i = 0; i < 15; i++) {
        setTimeout(() => {
            const explosion = document.createElement('div');
            explosion.innerHTML = '💰';
            explosion.style.position = 'absolute';
            explosion.style.left = (20 + Math.random() * 60) + '%';
            explosion.style.top = (20 + Math.random() * 60) + '%';
            explosion.style.fontSize = '40px';
            explosion.style.animation = 'money-burst 1s ease-out';
            explosion.style.zIndex = '10001';
            overlay.appendChild(explosion);

            setTimeout(() => explosion.remove(), 1000);
        }, i * 150);
    }

    // Big flash overlay
    const flash = document.createElement('div');
    flash.style.position = 'fixed';
    flash.style.top = '0';
    flash.style.left = '0';
    flash.style.width = '100%';
    flash.style.height = '100%';
    flash.style.background = 'radial-gradient(circle, rgba(16, 185, 129, 0.3), transparent)';
    flash.style.animation = 'flash 0.5s ease-out';
    flash.style.pointerEvents = 'none';
    flash.style.zIndex = '9998';
    overlay.appendChild(flash);
    setTimeout(() => flash.remove(), 500);

    // Show big profit number in center
    const bigProfit = document.createElement('div');
    bigProfit.textContent = '+$' + amount.toFixed(0);
    bigProfit.style.position = 'fixed';
    bigProfit.style.top = '50%';
    bigProfit.style.left = '50%';
    bigProfit.style.transform = 'translate(-50%, -50%)';
    bigProfit.style.fontSize = '120px';
    bigProfit.style.fontWeight = 'bold';
    bigProfit.style.color = '#10b981';
    bigProfit.style.textShadow = '0 0 30px rgba(16, 185, 129, 0.8), 0 0 60px rgba(16, 185, 129, 0.4)';
    bigProfit.style.animation = 'profit-pop 2s ease-out';
    bigProfit.style.zIndex = '10002';
    bigProfit.style.pointerEvents = 'none';
    overlay.appendChild(bigProfit);
    setTimeout(() => bigProfit.remove(), 2000);
}

// ============= SOUND SYSTEM =============
function playSound(type) {
    if (!CONFIG.soundEnabled) return;

    const audioElement = document.getElementById(`audio-${type}`);
    if (!audioElement) return;

    const soundSelect = document.getElementById(`${type}-sound`);
    if (!soundSelect) return;

    const selectedSound = soundSelect.value;
    if (selectedSound === 'none') return;

    const soundFile = SOUND_FILES[type]?.[selectedSound];
    if (!soundFile) return;

    audioElement.src = soundFile;
    audioElement.volume = CONFIG.masterVolume;
    audioElement.play().catch(err => console.log('Sound play failed:', err));
}

function testSound(type) {
    playSound(type);
}

// ============= SETTINGS =============
function toggleSettings() {
    const panel = document.getElementById('settings-panel');
    panel.classList.toggle('open');
}

function initializeSettings() {
    // Master volume
    const masterVolumeSlider = document.getElementById('master-volume');
    const masterVolumeLabel = document.getElementById('master-volume-label');
    masterVolumeSlider.addEventListener('input', (e) => {
        CONFIG.masterVolume = e.target.value / 100;
        masterVolumeLabel.textContent = e.target.value + '%';
        saveSettings();
    });

    // Music volume
    const musicVolumeSlider = document.getElementById('music-volume');
    const musicVolumeLabel = document.getElementById('music-volume-label');
    musicVolumeSlider.addEventListener('input', (e) => {
        CONFIG.musicVolume = e.target.value / 100;
        musicVolumeLabel.textContent = e.target.value + '%';
        saveSettings();
    });

    // Sound selections
    ['signal-sound', 'entry-sound', 'profit-sound', 'stoploss-sound'].forEach(id => {
        const select = document.getElementById(id);
        select.addEventListener('change', saveSettings);
    });

    // Checkboxes
    document.getElementById('show-celebrations').addEventListener('change', (e) => {
        CONFIG.celebrationsEnabled = e.target.checked;
        saveSettings();
    });

    document.getElementById('show-confetti').addEventListener('change', (e) => {
        CONFIG.confettiEnabled = e.target.checked;
        saveSettings();
    });

    document.getElementById('show-demo-button').addEventListener('change', (e) => {
        CONFIG.showDemoButton = e.target.checked;
        updateDemoButtonVisibility();
        saveSettings();
    });

    // Load saved settings
    loadSettings();
}

function updateDemoButtonVisibility() {
    const demoBtn = document.querySelector('.demo-btn');
    if (demoBtn) {
        demoBtn.style.display = CONFIG.showDemoButton ? 'block' : 'none';
    }
}

function saveSettings() {
    const settings = {
        masterVolume: CONFIG.masterVolume,
        musicVolume: CONFIG.musicVolume,
        celebrationsEnabled: CONFIG.celebrationsEnabled,
        confettiEnabled: CONFIG.confettiEnabled,
        showDemoButton: CONFIG.showDemoButton,
        sounds: {
            signal: document.getElementById('signal-sound').value,
            entry: document.getElementById('entry-sound').value,
            profit: document.getElementById('profit-sound').value,
            stoploss: document.getElementById('stoploss-sound').value
        }
    };

    localStorage.setItem('streaming-dashboard-settings', JSON.stringify(settings));
}

function loadSettings() {
    const saved = localStorage.getItem('streaming-dashboard-settings');
    if (!saved) return;

    try {
        const settings = JSON.parse(saved);

        // Apply settings
        CONFIG.masterVolume = settings.masterVolume || 0.7;
        CONFIG.musicVolume = settings.musicVolume || 0.3;
        CONFIG.celebrationsEnabled = settings.celebrationsEnabled !== false;
        CONFIG.confettiEnabled = settings.confettiEnabled !== false;
        CONFIG.showDemoButton = settings.showDemoButton !== false;

        // Update UI
        document.getElementById('master-volume').value = CONFIG.masterVolume * 100;
        document.getElementById('master-volume-label').textContent = Math.round(CONFIG.masterVolume * 100) + '%';

        document.getElementById('music-volume').value = CONFIG.musicVolume * 100;
        document.getElementById('music-volume-label').textContent = Math.round(CONFIG.musicVolume * 100) + '%';

        if (settings.sounds) {
            document.getElementById('signal-sound').value = settings.sounds.signal || 'chime';
            document.getElementById('entry-sound').value = settings.sounds.entry || 'click';
            document.getElementById('profit-sound').value = settings.sounds.profit || 'kaching';
            document.getElementById('stoploss-sound').value = settings.sounds.stoploss || 'buzz';
        }

        document.getElementById('show-celebrations').checked = CONFIG.celebrationsEnabled;
        document.getElementById('show-confetti').checked = CONFIG.confettiEnabled;
        document.getElementById('show-demo-button').checked = CONFIG.showDemoButton;

        // Apply demo button visibility
        updateDemoButtonVisibility();

    } catch (err) {
        console.error('Failed to load settings:', err);
    }
}

// ============= DEMO MODE =============
async function runDemo() {
    console.log('🎬 Starting demo sequence...');

    // Update scoreboard with demo values
    document.getElementById('today-pnl').textContent = '+$1,234';
    document.getElementById('today-pnl').className = 'stat-value positive';
    document.getElementById('week-pnl').textContent = '+$4,567';
    document.getElementById('week-pnl').className = 'stat-value positive';
    document.getElementById('win-rate').textContent = '58%';
    document.getElementById('active-count').textContent = '1';

    // STEP 1: Show signal in spotlight (0-5 seconds)
    const spotlightContent = document.getElementById('spotlight-content');
    spotlightContent.innerHTML = `
        <div class="setup-card" style="animation: slideIn 0.5s ease;">
            <div class="setup-symbol">TSLA</div>

            <div class="setup-prices">
                <div class="price-box">
                    <div class="price-label">💰 ENTRY</div>
                    <div class="price-value entry">$245.50</div>
                </div>
                <div class="price-box">
                    <div class="price-label">🎯 TARGET</div>
                    <div class="price-value target">$270.00</div>
                    <div style="font-size: 0.9rem; color: #10b981; margin-top: 0.3rem;">+10.0%</div>
                </div>
                <div class="price-box">
                    <div class="price-label">🛡️ STOP</div>
                    <div class="price-value stop">$230.00</div>
                    <div style="font-size: 0.9rem; color: #ef4444; margin-top: 0.3rem;">-6.3%</div>
                </div>
            </div>

            <div class="setup-meta">
                <div>
                    <span style="color: #9ca3af;">🤖 Bot:</span>
                    <span style="font-weight: bold;">HailMary</span>
                </div>
                <div>
                    <span style="color: #9ca3af;">Position:</span>
                    <span style="font-weight: bold;">$500</span>
                </div>
            </div>

            <div class="confidence-bar">
                <div class="confidence-fill" style="width: 85%">
                    85% 🔥
                </div>
            </div>

            <div style="text-align: center; margin-top: 1.5rem; font-size: 1.2rem; color: #3b82f6;">
                ⏰ WAITING FOR ENTRY...
            </div>
        </div>
    `;

    // Play signal sound
    playSound('signal');

    // STEP 2: Entry after 5 seconds
    await sleep(5000);
    console.log('💰 Entering position...');
    playSound('entry');

    // Show position in live positions
    const positionsContent = document.getElementById('positions-content');
    let currentPrice = 245.50;
    let startTime = Date.now();

    // STEP 3: Animate price increasing (5-15 seconds)
    for (let i = 0; i < 20; i++) {
        await sleep(500);
        currentPrice += Math.random() * 2; // Price goes up

        const timeInTrade = Math.floor((Date.now() - startTime) / 60000);
        const pnl = ((currentPrice - 245.50) / 245.50) * 500;
        const pnlPct = ((currentPrice - 245.50) / 245.50) * 100;
        const progress = Math.min(100, ((currentPrice - 245.50) / (270 - 245.50)) * 100);

        positionsContent.innerHTML = `
            <div class="position-card winning" style="animation: slideIn 0.3s ease;">
                <div class="position-header">
                    <div>
                        <div class="position-symbol">TSLA</div>
                        <div class="position-time">${timeInTrade}m</div>
                    </div>
                    <div class="position-pnl positive">
                        +$${pnl.toFixed(0)} (+${pnlPct.toFixed(1)}%)
                    </div>
                </div>

                <div class="position-details">
                    <div>Entry: $245.50</div>
                    <div>Now: $${currentPrice.toFixed(2)}</div>
                    <div>Target: $270.00</div>
                    <div>Stop: $230.00</div>
                </div>

                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>
                <div class="progress-label">
                    <span>To Target</span>
                    <span>${progress.toFixed(0)}%</span>
                </div>
            </div>
        `;

        // Update scoreboard
        document.getElementById('today-pnl').textContent = `+$${(1234 + pnl).toFixed(0)}`;
    }

    // STEP 4: TARGET HIT! MONEY SHOWER!
    await sleep(1000);
    console.log('🎉 TARGET HIT!');

    const finalPnl = ((270 - 245.50) / 245.50) * 500;
    playSound('profit');
    moneyShower(finalPnl); // 💵💰💵 MONEY SHOWER! 💵💰💵

    // Update to show closed position
    await sleep(2000);
    positionsContent.innerHTML = `
        <div style="text-align: center; padding: 3rem;">
            <div style="font-size: 3rem; margin-bottom: 1rem;">🎊</div>
            <div style="font-size: 1.5rem; color: #10b981; font-weight: bold;">
                TSLA Closed: +$${finalPnl.toFixed(0)} (+${(((270 - 245.50) / 245.50) * 100).toFixed(1)}%)
            </div>
            <div style="margin-top: 1rem; color: #9ca3af;">
                Demo completed! Switch to Performance view to see the win!
            </div>
        </div>
    `;

    // Add to recent wins (performance view)
    const timeInTrade = Math.floor((Date.now() - startTime) / 60000);
    const recentWinsContent = document.getElementById('recent-wins-content');
    recentWinsContent.innerHTML = `
        <div class="win-card">
            <div class="win-symbol">TSLA</div>
            <div class="win-details">
                <div class="win-main">+$${finalPnl.toFixed(0)} (+${(((270 - 245.50) / 245.50) * 100).toFixed(1)}%)</div>
                <div class="win-meta">
                    <span>🤖 HailMary</span>
                    <span>⏱️ ${timeInTrade}min</span>
                    <span>🎯 TARGET HIT</span>
                </div>
            </div>
            <div class="win-time">Just now</div>
        </div>
        <div class="win-card">
            <div class="win-symbol">SPY 560C</div>
            <div class="win-details">
                <div class="win-main">+$875 (+175%)</div>
                <div class="win-meta">
                    <span>🤖 Options0DTE</span>
                    <span>⏱️ 18min</span>
                    <span>🎯 TARGET HIT</span>
                </div>
            </div>
            <div class="win-time">12min ago</div>
        </div>
        <div class="win-card">
            <div class="win-symbol">NVDA</div>
            <div class="win-details">
                <div class="win-main">+$324 (+8.1%)</div>
                <div class="win-meta">
                    <span>🤖 TwentyMin</span>
                    <span>⏱️ 23min</span>
                    <span>🎯 TARGET HIT</span>
                </div>
            </div>
            <div class="win-time">45min ago</div>
        </div>
    `;

    // Update bot leaderboard stats
    const botCards = document.querySelectorAll('.bot-stat-card');
    if (botCards.length >= 3) {
        // HailMary stats
        botCards[0].querySelector('.bot-stats-grid').innerHTML = `
            <div class="bot-stat-item"><div class="bot-stat-label">Win Rate</div><div class="bot-stat-value">62%</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Today P&L</div><div class="bot-stat-value positive">+$${finalPnl.toFixed(0)}</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Trades</div><div class="bot-stat-value">1</div></div>
        `;
        // TwentyMin stats
        botCards[1].querySelector('.bot-stats-grid').innerHTML = `
            <div class="bot-stat-item"><div class="bot-stat-label">Win Rate</div><div class="bot-stat-value">58%</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Today P&L</div><div class="bot-stat-value positive">+$324</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Trades</div><div class="bot-stat-value">1</div></div>
        `;
        // Options stats
        botCards[2].querySelector('.bot-stats-grid').innerHTML = `
            <div class="bot-stat-item"><div class="bot-stat-label">Win Rate</div><div class="bot-stat-value">71%</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Today P&L</div><div class="bot-stat-value positive">+$875</div></div>
            <div class="bot-stat-item"><div class="bot-stat-label">Trades</div><div class="bot-stat-value">1</div></div>
        `;
    }

    // Populate OPTIONS DETAILS tab
    const upcomingOptionsContent = document.getElementById('upcoming-options-content');
    upcomingOptionsContent.innerHTML = `
        <div class="option-card call">
            <div class="option-header">
                <div class="option-symbol">AAPL</div>
                <div class="option-contract-info">
                    <div class="option-contract-type call">CALL</div>
                    <div class="option-contract-details">Strike: $195.00 | Exp: Feb 21, 2026</div>
                </div>
                <div class="option-pnl-badge">
                    🎯 UPCOMING
                </div>
            </div>
            <div class="option-stats-grid">
                <div class="option-stat-box">
                    <div class="option-stat-label">Entry</div>
                    <div class="option-stat-value">$3.85</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Current</div>
                    <div class="option-stat-value highlight">$3.85</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Quantity</div>
                    <div class="option-stat-value">2</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Days to Exp</div>
                    <div class="option-stat-value">6</div>
                </div>
            </div>
            <div class="option-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$3.85</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Take Profit</div>
                    <div class="option-level-value">$5.80</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+50.6%</div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Trailing Stop</div>
                    <div class="option-level-value">$2.90</div>
                    <div style="font-size: 0.85rem; color: #ef4444; margin-top: 0.3rem;">-24.7%</div>
                </div>
            </div>
            <div class="option-meta-info">
                <div class="option-meta-item">
                    <div class="option-meta-label">🤖 Bot</div>
                    <div class="option-meta-value">OptionsCore</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">⏱️ Time</div>
                    <div class="option-meta-value">Pending</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📅 Expiry</div>
                    <div class="option-meta-value">Feb 21, 2026</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📊 Strike</div>
                    <div class="option-meta-value">$195.00</div>
                </div>
            </div>
        </div>
        <div class="option-card put">
            <div class="option-header">
                <div class="option-symbol">META</div>
                <div class="option-contract-info">
                    <div class="option-contract-type put">PUT</div>
                    <div class="option-contract-details">Strike: $650.00 | Exp: Feb 18, 2026</div>
                </div>
                <div class="option-pnl-badge">
                    🎯 UPCOMING
                </div>
            </div>
            <div class="option-stats-grid">
                <div class="option-stat-box">
                    <div class="option-stat-label">Entry</div>
                    <div class="option-stat-value">$8.20</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Current</div>
                    <div class="option-stat-value highlight">$8.20</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Quantity</div>
                    <div class="option-stat-value">1</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Days to Exp</div>
                    <div class="option-stat-value">3</div>
                </div>
            </div>
            <div class="option-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$8.20</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Take Profit</div>
                    <div class="option-level-value">$16.40</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+100.0%</div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Trailing Stop</div>
                    <div class="option-level-value">$4.10</div>
                    <div style="font-size: 0.85rem; color: #ef4444; margin-top: 0.3rem;">-50.0%</div>
                </div>
            </div>
            <div class="option-meta-info">
                <div class="option-meta-item">
                    <div class="option-meta-label">🤖 Bot</div>
                    <div class="option-meta-value">Options0DTE</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">⏱️ Time</div>
                    <div class="option-meta-value">Pending</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📅 Expiry</div>
                    <div class="option-meta-value">Feb 18, 2026</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📊 Strike</div>
                    <div class="option-meta-value">$650.00</div>
                </div>
            </div>
        </div>
    `;

    const activeOptionsContent = document.getElementById('active-options-content');
    activeOptionsContent.innerHTML = `
        <div class="option-card call">
            <div class="option-header">
                <div class="option-symbol">SPY</div>
                <div class="option-contract-info">
                    <div class="option-contract-type call">CALL</div>
                    <div class="option-contract-details">Strike: $560.00 | Exp: Feb 15, 2026</div>
                </div>
                <div class="option-pnl-badge positive">
                    +$425
                    <div style="font-size: 1rem; color: #9ca3af;">(+85.0%)</div>
                </div>
            </div>
            <div class="option-stats-grid">
                <div class="option-stat-box">
                    <div class="option-stat-label">Entry</div>
                    <div class="option-stat-value">$5.00</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Current</div>
                    <div class="option-stat-value highlight">$9.25</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Quantity</div>
                    <div class="option-stat-value">1</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Days to Exp</div>
                    <div class="option-stat-value">0</div>
                </div>
            </div>
            <div class="option-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$5.00</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Take Profit</div>
                    <div class="option-level-value">$10.00</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+100.0%</div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Trailing Stop</div>
                    <div class="option-level-value">$7.40</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+48.0%</div>
                </div>
            </div>
            <div class="option-meta-info">
                <div class="option-meta-item">
                    <div class="option-meta-label">🤖 Bot</div>
                    <div class="option-meta-value">Options0DTE</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">⏱️ Time</div>
                    <div class="option-meta-value">2h 15m</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📅 Expiry</div>
                    <div class="option-meta-value">Feb 15, 2026</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📊 Strike</div>
                    <div class="option-meta-value">$560.00</div>
                </div>
            </div>
        </div>
        <div class="option-card call">
            <div class="option-header">
                <div class="option-symbol">AMZN</div>
                <div class="option-contract-info">
                    <div class="option-contract-type call">CALL</div>
                    <div class="option-contract-details">Strike: $225.00 | Exp: Feb 28, 2026</div>
                </div>
                <div class="option-pnl-badge positive">
                    +$156
                    <div style="font-size: 1rem; color: #9ca3af;">(+31.2%)</div>
                </div>
            </div>
            <div class="option-stats-grid">
                <div class="option-stat-box">
                    <div class="option-stat-label">Entry</div>
                    <div class="option-stat-value">$5.00</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Current</div>
                    <div class="option-stat-value highlight">$6.56</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Quantity</div>
                    <div class="option-stat-value">1</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Days to Exp</div>
                    <div class="option-stat-value">13</div>
                </div>
            </div>
            <div class="option-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$5.00</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Take Profit</div>
                    <div class="option-level-value">$8.50</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+70.0%</div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Trailing Stop</div>
                    <div class="option-level-value">$5.25</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">+5.0%</div>
                </div>
            </div>
            <div class="option-meta-info">
                <div class="option-meta-item">
                    <div class="option-meta-label">🤖 Bot</div>
                    <div class="option-meta-value">OptionsCore</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">⏱️ Time</div>
                    <div class="option-meta-value">48m</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📅 Expiry</div>
                    <div class="option-meta-value">Feb 28, 2026</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📊 Strike</div>
                    <div class="option-meta-value">$225.00</div>
                </div>
            </div>
        </div>
    `;

    // Reset spotlight
    spotlightContent.innerHTML = `
        <div class="scanning-indicator">
            <div style="font-size: 3rem; margin-bottom: 1rem;">🔍</div>
            <div>Scanning for opportunities<span class="scanning-dots"></span></div>
        </div>
    `;

    // Populate PRE-MARKET SCAN tab with demo data
    await populatePreMarketDemo();

    // Populate CHARTS tab with demo data
    await populateChartsDemo();

    console.log('✅ Demo complete!');
}

async function populatePreMarketDemo() {
    console.log('📊 Populating pre-market scan demo...');

    // Simulate live scanning
    const scanSymbols = [
        { symbol: 'TSLA', score: 92 },
        { symbol: 'NVDA', score: 88 },
        { symbol: 'AAPL', score: 85 },
        { symbol: 'META', score: 83 },
        { symbol: 'SPY', score: 91 },
        { symbol: 'AMZN', score: 79 },
        { symbol: 'GOOGL', score: 75 },
        { symbol: 'MSFT', score: 87 },
        { symbol: 'AMD', score: 72 },
        { symbol: 'NFLX', score: 68 }
    ];

    // Add scanning items with delays
    for (let item of scanSymbols) {
        addScanItem(item.symbol, 'analyzing');
        await sleep(800);
        addScanItem(item.symbol, item.score >= 75 ? 'scored' : 'rejected', item.score);
        await sleep(400);
    }

    // Populate top 5 picks
    const topPicks = [
        {
            symbol: 'TSLA',
            contractType: '$245 CALL',
            strike: 245,
            expiry: 'Feb 21, 2026',
            entry: 8.50,
            target: 17.00,
            stop: 4.25,
            score: 92,
            reasons: [
                { icon: '📈', text: '<span class="reasoning-highlight">Strong bullish momentum</span> - Price broke above key resistance at $240 with heavy volume' },
                { icon: '🎯', text: '<span class="reasoning-highlight">Gap up pre-market</span> - Trading 3.2% higher on positive earnings sentiment' },
                { icon: '💹', text: '<span class="reasoning-highlight">High IV crush potential</span> - Implied volatility at 65%, expecting post-earnings compression' },
                { icon: '🔥', text: '<span class="reasoning-highlight">Technical confluence</span> - MACD crossover + RSI bounce from oversold = strong setup' }
            ]
        },
        {
            symbol: 'SPY',
            contractType: '$560 CALL',
            strike: 560,
            expiry: 'Feb 15, 2026',
            entry: 5.20,
            target: 10.50,
            stop: 2.60,
            score: 91,
            reasons: [
                { icon: '📊', text: '<span class="reasoning-highlight">Market breadth improving</span> - 78% of S&P stocks above 50-day MA, bullish divergence' },
                { icon: '🎪', text: '<span class="reasoning-highlight">0DTE opportunity</span> - Same-day expiration with rapid theta decay in our favor' },
                { icon: '💪', text: '<span class="reasoning-highlight">Support holding</span> - Tested $557 support 3 times overnight, bounce imminent' },
                { icon: '📰', text: '<span class="reasoning-highlight">Catalyst: Fed minutes release</span> - Expected dovish tone could push indexes higher' }
            ]
        },
        {
            symbol: 'NVDA',
            contractType: '$880 CALL',
            strike: 880,
            expiry: 'Feb 28, 2026',
            entry: 12.80,
            target: 22.00,
            stop: 7.50,
            score: 88,
            reasons: [
                { icon: '🚀', text: '<span class="reasoning-highlight">Sector rotation into tech</span> - Semiconductor index (SOX) up 2.1% pre-market' },
                { icon: '📢', text: '<span class="reasoning-highlight">Analyst upgrade</span> - Morgan Stanley raised PT to $950, citing AI chip demand' },
                { icon: '📈', text: '<span class="reasoning-highlight">Cup & handle formation</span> - Textbook pattern completion at $870, breakout confirmed' },
                { icon: '💰', text: '<span class="reasoning-highlight">Institutional accumulation</span> - Dark pool activity shows 2.3M shares bought yesterday' }
            ]
        },
        {
            symbol: 'MSFT',
            contractType: '$425 CALL',
            strike: 425,
            expiry: 'Feb 21, 2026',
            entry: 6.40,
            target: 11.50,
            stop: 3.80,
            score: 87,
            reasons: [
                { icon: '☁️', text: '<span class="reasoning-highlight">Azure growth accelerating</span> - Cloud revenue beat estimates by 8%, AI integration driving adoption' },
                { icon: '🎯', text: '<span class="reasoning-highlight">Reclaiming key level</span> - Price back above $420 pivot after brief dip, bulls in control' },
                { icon: '📊', text: '<span class="reasoning-highlight">Volume surge</span> - Pre-market volume 3x average, institutional buying detected' },
                { icon: '🔔', text: '<span class="reasoning-highlight">Relative strength</span> - Outperforming QQQ by 1.8% this week, leader in mega-cap space' }
            ]
        },
        {
            symbol: 'META',
            contractType: '$650 PUT',
            strike: 650,
            expiry: 'Feb 18, 2026',
            entry: 9.20,
            target: 18.50,
            stop: 4.60,
            score: 83,
            reasons: [
                { icon: '📉', text: '<span class="reasoning-highlight">Bearish divergence forming</span> - Price making higher highs but RSI making lower highs' },
                { icon: '🎪', text: '<span class="reasoning-highlight">Resistance rejection</span> - Failed to break $665 three times, sellers stepping in' },
                { icon: '⚠️', text: '<span class="reasoning-highlight">Regulatory headwinds</span> - EU antitrust investigation announced, sentiment turning negative' },
                { icon: '💹', text: '<span class="reasoning-highlight">Options flow bearish</span> - $2.8M in put volume vs $800K calls, smart money positioning short' }
            ]
        }
    ];

    populateTopPicks(topPicks);
    console.log('✅ Pre-market scan demo populated!');
}

async function populateChartsDemo() {
    console.log('📊 Populating charts demo...');

    // Initialize chart if needed
    initializePnLChart();

    // Add some demo trade bubbles
    const demoTrades = [
        { symbol: 'TSLA', pnl: 458, time: '2m ago' },
        { symbol: 'SPY 560C', pnl: 875, time: '12m ago' },
        { symbol: 'NVDA', pnl: 324, time: '23m ago' },
        { symbol: 'AAPL', pnl: -120, time: '35m ago' },
        { symbol: 'META 650P', pnl: 650, time: '48m ago' }
    ];

    // Add trades with staggered timing
    for (let i = 0; i < demoTrades.length; i++) {
        setTimeout(() => {
            addTradeBubble(demoTrades[i]);
        }, i * 3000); // Stagger by 3 seconds
    }

    // Simulate live P&L updates (add new data points every few seconds)
    let currentBalance = 90164;
    let updateCount = 0;
    const chartUpdateInterval = setInterval(() => {
        updateCount++;
        // Add some variation
        const change = (Math.random() - 0.4) * 50; // Slight upward bias
        currentBalance += change;
        const dayPnl = currentBalance - 88930; // Starting balance

        addPnLDataPoint(currentBalance, dayPnl);

        // Stop after 10 updates (for demo)
        if (updateCount >= 10) {
            clearInterval(chartUpdateInterval);
        }
    }, 2000);

    console.log('✅ Charts demo populated!');
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ============= OPTIONS DETAILS VIEW =============
function updateOptionsView(signalsData, positionsData) {
    // Update upcoming options signals
    updateUpcomingOptions(signalsData);

    // Update active options positions
    updateActiveOptions(positionsData);
}

function updateUpcomingOptions(data) {
    const container = document.getElementById('upcoming-options-content');

    if (!data || !data.signals || data.signals.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 2rem; color: #9ca3af;">
                Scanning for option setups...
            </div>
        `;
        return;
    }

    let html = '';
    data.signals.forEach(signal => {
        // Determine if it's an option (check if symbol contains option-like data)
        const isOption = signal.asset_class === 'option' || signal.symbol?.includes('C') || signal.symbol?.includes('P');
        const optionType = signal.option_type || (signal.symbol?.includes('C') ? 'CALL' : 'PUT');
        const strike = signal.strike_price || 0;
        const expiry = signal.expiry_date || 'Unknown';
        const entryPrice = signal.entry_price || 0;
        const targetPrice = signal.target_price || 0;
        const stopPrice = signal.stop_price || 0;

        html += renderOptionCard({
            symbol: signal.symbol?.split(/[CP]/)[0] || signal.symbol,
            optionType: optionType,
            strike: strike,
            expiry: expiry,
            entry: entryPrice,
            current: entryPrice,
            target: targetPrice,
            stop: stopPrice,
            trailingStop: signal.trailing_stop || stopPrice,
            pnl: 0,
            pnlPct: 0,
            bot: signal.bot || 'Unknown',
            timeInTrade: 'Pending',
            quantity: signal.quantity || 1,
            status: 'upcoming',
            daysToExpiry: signal.days_to_expiry || calculateDaysToExpiry(expiry)
        });
    });

    container.innerHTML = html || `<div style="text-align: center; padding: 2rem; color: #9ca3af;">No option signals at this time</div>`;
}

function updateActiveOptions(data) {
    const container = document.getElementById('active-options-content');

    if (!data || !data.positions || data.positions.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 2rem; color: #9ca3af;">
                No active option positions
            </div>
        `;
        return;
    }

    let html = '';
    data.positions.forEach(pos => {
        const isOption = pos.asset_class === 'us_option' || pos.symbol?.includes('C') || pos.symbol?.includes('P');
        if (!isOption) return; // Skip non-options

        const optionType = pos.option_type || (pos.symbol?.includes('C') ? 'CALL' : 'PUT');
        const strike = pos.strike_price || 0;
        const expiry = pos.expiry_date || 'Unknown';
        const entryPrice = pos.avg_entry_price || 0;
        const currentPrice = pos.current_price || 0;
        const targetPrice = pos.target_price || (entryPrice * 1.5);
        const stopPrice = pos.stop_price || (entryPrice * 0.5);
        const trailingStop = pos.trailing_stop || stopPrice;
        const pnl = pos.unrealized_pl || 0;
        const pnlPct = pos.unrealized_plpc || 0;

        const entryTime = pos.entry_time ? new Date(pos.entry_time) : new Date();
        const minutesInTrade = Math.floor((new Date() - entryTime) / 60000);
        const timeStr = formatDuration(minutesInTrade);

        html += renderOptionCard({
            symbol: pos.symbol?.split(/[CP]/)[0] || pos.symbol,
            optionType: optionType,
            strike: strike,
            expiry: expiry,
            entry: entryPrice,
            current: currentPrice,
            target: targetPrice,
            stop: stopPrice,
            trailingStop: trailingStop,
            pnl: pnl,
            pnlPct: pnlPct,
            bot: pos.bot || 'Unknown',
            timeInTrade: timeStr,
            quantity: pos.qty || 1,
            status: 'active',
            daysToExpiry: pos.days_to_expiry || calculateDaysToExpiry(expiry)
        });
    });

    container.innerHTML = html || `<div style="text-align: center; padding: 2rem; color: #9ca3af;">No active option positions</div>`;
}

function renderOptionCard(data) {
    const cardClass = data.optionType === 'CALL' ? 'call' : 'put';
    const statusBadge = data.status === 'upcoming' ? '🎯 UPCOMING' : '🔴 LIVE';

    return `
        <div class="option-card ${cardClass}">
            <div class="option-header">
                <div class="option-symbol">${data.symbol}</div>
                <div class="option-contract-info">
                    <div class="option-contract-type ${cardClass}">${data.optionType}</div>
                    <div class="option-contract-details">Strike: $${data.strike.toFixed(2)} | Exp: ${formatDate(data.expiry)}</div>
                </div>
                <div class="option-pnl-badge ${data.pnl >= 0 ? 'positive' : 'negative'}">
                    ${data.status === 'active' ? formatCurrency(data.pnl) : statusBadge}
                    ${data.status === 'active' ? `<div style="font-size: 1rem; color: #9ca3af;">(${data.pnlPct >= 0 ? '+' : ''}${data.pnlPct.toFixed(1)}%)</div>` : ''}
                </div>
            </div>

            <div class="option-stats-grid">
                <div class="option-stat-box">
                    <div class="option-stat-label">Entry</div>
                    <div class="option-stat-value">$${data.entry.toFixed(2)}</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Current</div>
                    <div class="option-stat-value highlight">$${data.current.toFixed(2)}</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Quantity</div>
                    <div class="option-stat-value">${data.quantity}</div>
                </div>
                <div class="option-stat-box">
                    <div class="option-stat-label">Days to Exp</div>
                    <div class="option-stat-value">${data.daysToExpiry}</div>
                </div>
            </div>

            <div class="option-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$${data.entry.toFixed(2)}</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Take Profit</div>
                    <div class="option-level-value">$${data.target.toFixed(2)}</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">
                        +${(((data.target - data.entry) / data.entry) * 100).toFixed(1)}%
                    </div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Trailing Stop</div>
                    <div class="option-level-value">$${data.trailingStop.toFixed(2)}</div>
                    <div style="font-size: 0.85rem; color: #ef4444; margin-top: 0.3rem;">
                        ${(((data.trailingStop - data.entry) / data.entry) * 100).toFixed(1)}%
                    </div>
                </div>
            </div>

            <div class="option-meta-info">
                <div class="option-meta-item">
                    <div class="option-meta-label">🤖 Bot</div>
                    <div class="option-meta-value">${data.bot}</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">⏱️ Time</div>
                    <div class="option-meta-value">${data.timeInTrade}</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📅 Expiry</div>
                    <div class="option-meta-value">${formatDate(data.expiry)}</div>
                </div>
                <div class="option-meta-item">
                    <div class="option-meta-label">📊 Strike</div>
                    <div class="option-meta-value">$${data.strike.toFixed(2)}</div>
                </div>
            </div>
        </div>
    `;
}

function calculateDaysToExpiry(expiryDate) {
    if (!expiryDate || expiryDate === 'Unknown') return '?';
    try {
        const expiry = new Date(expiryDate);
        const now = new Date();
        const days = Math.ceil((expiry - now) / (1000 * 60 * 60 * 24));
        return Math.max(0, days);
    } catch {
        return '?';
    }
}

function formatDate(dateStr) {
    if (!dateStr || dateStr === 'Unknown') return 'Unknown';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return dateStr;
    }
}

// ============= PRE-MARKET SCAN =============
function updateMarketCountdown() {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const marketOpen = new Date(today);
    marketOpen.setHours(9, 30, 0, 0); // 9:30 AM

    // If it's past market open, show next day
    if (now > marketOpen) {
        marketOpen.setDate(marketOpen.getDate() + 1);
    }

    const diff = marketOpen - now;
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);

    const countdownEl = document.getElementById('market-countdown');
    if (countdownEl) {
        countdownEl.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }

    // Update scan status based on time until open
    const statusEl = document.getElementById('scan-status');
    if (statusEl) {
        const minutesUntilOpen = Math.floor(diff / (1000 * 60));
        if (minutesUntilOpen <= 5) {
            statusEl.textContent = '🏆 Finalizing Top 5 Picks...';
            statusEl.style.color = '#10b981';
        } else if (minutesUntilOpen <= 10) {
            statusEl.textContent = '📊 Ranking candidates...';
            statusEl.style.color = '#f59e0b';
        } else if (minutesUntilOpen <= 30) {
            statusEl.textContent = '🔍 Live scanning in progress...';
            statusEl.style.color = '#3b82f6';
        } else {
            statusEl.textContent = '⏰ Scanner activates 30 minutes before open';
            statusEl.style.color = '#9ca3af';
        }
    }
}

function addScanItem(symbol, status, score = null) {
    const feedEl = document.getElementById('scan-feed');
    if (!feedEl) return;

    // Remove placeholder if exists
    if (feedEl.querySelector('div[style*="text-align: center"]')) {
        feedEl.innerHTML = '';
    }

    const item = document.createElement('div');
    item.className = `scan-item ${status}`;

    let statusText = '';
    let scoreHTML = '';

    if (status === 'analyzing') {
        statusText = 'Analyzing technicals...';
    } else if (status === 'scored') {
        statusText = 'Analysis complete';
        const scoreClass = score >= 80 ? 'high' : score >= 60 ? 'medium' : 'low';
        scoreHTML = `<div class="scan-score ${scoreClass}">${score}/100</div>`;
    } else if (status === 'rejected') {
        statusText = 'Below threshold';
        scoreHTML = `<div class="scan-score low">${score}/100</div>`;
    }

    item.innerHTML = `
        <div class="scan-symbol">${symbol}</div>
        <div class="scan-status">${statusText}</div>
        ${scoreHTML}
    `;

    feedEl.insertBefore(item, feedEl.firstChild);

    // Keep only last 20 items
    while (feedEl.children.length > 20) {
        feedEl.removeChild(feedEl.lastChild);
    }
}

function populateTopPicks(picks) {
    const container = document.getElementById('top-picks-content');
    if (!container) return;

    let html = '';
    picks.forEach((pick, index) => {
        html += renderTopPickCard(pick, index + 1);
    });

    container.innerHTML = html;
}

function renderTopPickCard(pick, rank) {
    const medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'];
    return `
        <div class="top-pick-card">
            <div class="top-pick-header">
                <div class="top-pick-rank">#${rank} ${medals[rank - 1]}</div>
                <div class="top-pick-symbol">${pick.symbol}</div>
                <div class="top-pick-score-badge">${pick.score}/100</div>
            </div>
            <div class="top-pick-contract">
                ${pick.contractType} ${pick.contractType.includes('CALL') ? '📈' : '📉'}
                Strike: $${pick.strike} | Exp: ${pick.expiry}
            </div>
            <div class="top-pick-levels">
                <div class="option-level-box entry">
                    <div class="option-level-label">💰 Entry</div>
                    <div class="option-level-value">$${pick.entry.toFixed(2)}</div>
                </div>
                <div class="option-level-box target">
                    <div class="option-level-label">🎯 Target</div>
                    <div class="option-level-value">$${pick.target.toFixed(2)}</div>
                    <div style="font-size: 0.85rem; color: #10b981; margin-top: 0.3rem;">
                        +${(((pick.target - pick.entry) / pick.entry) * 100).toFixed(1)}%
                    </div>
                </div>
                <div class="option-level-box stop">
                    <div class="option-level-label">🛡️ Stop Loss</div>
                    <div class="option-level-value">$${pick.stop.toFixed(2)}</div>
                    <div style="font-size: 0.85rem; color: #ef4444; margin-top: 0.3rem;">
                        ${(((pick.stop - pick.entry) / pick.entry) * 100).toFixed(1)}%
                    </div>
                </div>
            </div>
            <div class="top-pick-reasoning">
                <div class="reasoning-title">
                    🧠 Why This Trade?
                </div>
                <div class="reasoning-points">
                    ${pick.reasons.map(reason => `
                        <div class="reasoning-point">
                            <div class="reasoning-icon">${reason.icon}</div>
                            <div class="reasoning-text">${reason.text}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
    `;
}

// ============= LIVE CHARTS =============
let pnlChartData = [];
let pnlChartCtx = null;
let pnlChartAnimationFrame = null;

function initializePnLChart() {
    const canvas = document.getElementById('pnl-chart');
    if (!canvas) return;

    pnlChartCtx = canvas.getContext('2d');

    // Set canvas size
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    // Initialize with demo data if empty
    if (pnlChartData.length === 0) {
        const startBalance = 90164;
        const now = Date.now();

        // Generate data points for the last hour
        for (let i = 60; i >= 0; i--) {
            const time = now - (i * 60 * 1000); // 60 minutes ago
            const variation = Math.sin(i / 10) * 500 + Math.random() * 300;
            pnlChartData.push({
                time: time,
                balance: startBalance + variation,
                pnl: variation
            });
        }
    }

    // Start animation loop
    if (!pnlChartAnimationFrame) {
        animatePnLChart();
    }
}

function animatePnLChart() {
    if (!pnlChartCtx) return;

    const canvas = pnlChartCtx.canvas;
    const ctx = pnlChartCtx;
    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    if (pnlChartData.length < 2) return;

    // Calculate bounds
    const balances = pnlChartData.map(d => d.balance);
    const minBalance = Math.min(...balances);
    const maxBalance = Math.max(...balances);
    const range = maxBalance - minBalance;
    const padding = range * 0.1;

    // Draw grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i++) {
        const y = (height / 5) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();

        // Draw price labels
        const price = maxBalance + padding - ((range + padding * 2) / 5) * i;
        ctx.fillStyle = 'rgba(156, 163, 175, 0.6)';
        ctx.font = '12px Arial';
        ctx.fillText('$' + price.toFixed(0), 10, y - 5);
    }

    // Draw vertical grid lines (time)
    for (let i = 0; i < 6; i++) {
        const x = (width / 6) * i;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }

    // Draw the line
    ctx.beginPath();
    ctx.lineWidth = 3;

    pnlChartData.forEach((point, index) => {
        const x = (index / (pnlChartData.length - 1)) * width;
        const normalizedValue = (point.balance - (minBalance - padding)) / (range + padding * 2);
        const y = height - (normalizedValue * height);

        if (index === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });

    // Gradient stroke
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    const currentPnl = pnlChartData[pnlChartData.length - 1].pnl;
    if (currentPnl >= 0) {
        gradient.addColorStop(0, 'rgba(16, 185, 129, 0.5)');
        gradient.addColorStop(1, 'rgba(16, 185, 129, 1)');
    } else {
        gradient.addColorStop(0, 'rgba(239, 68, 68, 0.5)');
        gradient.addColorStop(1, 'rgba(239, 68, 68, 1)');
    }
    ctx.strokeStyle = gradient;
    ctx.stroke();

    // Fill area under line
    const lastPoint = pnlChartData[pnlChartData.length - 1];
    const lastX = width;
    const lastNormalized = (lastPoint.balance - (minBalance - padding)) / (range + padding * 2);
    const lastY = height - (lastNormalized * height);

    ctx.lineTo(lastX, height);
    ctx.lineTo(0, height);
    ctx.closePath();

    const fillGradient = ctx.createLinearGradient(0, 0, 0, height);
    if (currentPnl >= 0) {
        fillGradient.addColorStop(0, 'rgba(16, 185, 129, 0.2)');
        fillGradient.addColorStop(1, 'rgba(16, 185, 129, 0)');
    } else {
        fillGradient.addColorStop(0, 'rgba(239, 68, 68, 0.2)');
        fillGradient.addColorStop(1, 'rgba(239, 68, 68, 0)');
    }
    ctx.fillStyle = fillGradient;
    ctx.fill();

    // Draw current point
    ctx.beginPath();
    ctx.arc(lastX, lastY, 6, 0, Math.PI * 2);
    ctx.fillStyle = currentPnl >= 0 ? '#10b981' : '#ef4444';
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Continue animation
    pnlChartAnimationFrame = requestAnimationFrame(animatePnLChart);
}

function addPnLDataPoint(balance, pnl) {
    pnlChartData.push({
        time: Date.now(),
        balance: balance,
        pnl: pnl
    });

    // Keep only last 60 points
    if (pnlChartData.length > 60) {
        pnlChartData.shift();
    }

    // Update display
    const balanceEl = document.getElementById('chart-balance');
    const pnlEl = document.getElementById('chart-pnl');
    if (balanceEl) balanceEl.textContent = '$' + balance.toFixed(0);
    if (pnlEl) {
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(0);
        pnlEl.className = '';
        pnlEl.style.color = pnl >= 0 ? '#10b981' : '#ef4444';
    }
}

function addTradeBubble(trade) {
    const timeline = document.getElementById('trade-timeline');
    if (!timeline) return;

    const bubble = document.createElement('div');
    bubble.className = `trade-bubble ${trade.pnl < 0 ? 'loss' : ''}`;

    const icon = trade.pnl >= 0 ? '💰' : '📉';
    const randomTop = Math.random() * 120; // Random vertical position

    bubble.style.top = randomTop + 'px';
    bubble.innerHTML = `
        <div class="trade-bubble-icon">${icon}</div>
        <div class="trade-bubble-info">
            <div class="trade-bubble-symbol">${trade.symbol}</div>
            <div class="trade-bubble-pnl" style="color: ${trade.pnl >= 0 ? '#10b981' : '#ef4444'}">
                ${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toFixed(0)}
            </div>
            <div class="trade-bubble-time">${trade.time}</div>
        </div>
    `;

    timeline.appendChild(bubble);

    // Remove after animation completes
    setTimeout(() => {
        bubble.remove();
    }, 15000);
}

// ============= VIEW SWITCHING =============
function switchView(view) {
    const liveView = document.querySelector('.main-grid');
    const premarketView = document.getElementById('premarket-view');
    const chartsView = document.getElementById('charts-view');
    const optionsView = document.getElementById('options-view');
    const performanceView = document.getElementById('performance-view');
    const tabs = document.querySelectorAll('.tab-btn');

    // Hide all views
    liveView.style.display = 'none';
    if (premarketView) premarketView.style.display = 'none';
    if (chartsView) chartsView.style.display = 'none';
    optionsView.style.display = 'none';
    performanceView.style.display = 'none';

    // Remove active class from all tabs
    tabs.forEach(tab => tab.classList.remove('active'));

    if (view === 'live') {
        liveView.style.display = 'grid';
        tabs[0].classList.add('active');
    } else if (view === 'premarket') {
        if (premarketView) premarketView.style.display = 'flex';
        tabs[1].classList.add('active');
        // Start countdown if not already running
        if (!window.countdownInterval) {
            updateMarketCountdown();
            window.countdownInterval = setInterval(updateMarketCountdown, 1000);
        }
    } else if (view === 'charts') {
        if (chartsView) chartsView.style.display = 'flex';
        tabs[2].classList.add('active');
        // Initialize chart if not already done
        setTimeout(() => {
            initializePnLChart();
        }, 100);
    } else if (view === 'options') {
        optionsView.style.display = 'flex';
        tabs[3].classList.add('active');
    } else if (view === 'performance') {
        performanceView.style.display = 'block';
        tabs[4].classList.add('active');
    }
}

// ============= UTILITIES =============
function formatCurrency(value) {
    const sign = value >= 0 ? '+' : '';
    return sign + '$' + Math.abs(value).toFixed(0);
}

function formatDuration(minutes) {
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return `${hours}h ${mins}m`;
}
