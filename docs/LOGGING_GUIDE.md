# Logging & Troubleshooting Guide

## 📋 Where Are the Logs?

### Main Application Log
**Location**: `logs/portfolio_manager.log`

This file contains ALL application activity including:
- ✅ yfinance price fetching (with detailed symbol-by-symbol results)
- ✅ Portfolio calculations
- ✅ Import operations
- ✅ Errors and exceptions
- ✅ Cache hits/misses

### Log Rotation
- **Max Size**: 5 MB per file
- **Backups**: 5 files kept
- **Old Logs**: `portfolio_manager.log.1`, `.2`, `.3`, etc.

---

## 🔍 How to View Logs

### Option 1: Real-time Monitoring (PowerShell)
Watch logs as they happen:
```powershell
Get-Content logs/portfolio_manager.log -Wait -Tail 50
```

### Option 2: View Recent Entries
See last 100 lines:
```powershell
Get-Content logs/portfolio_manager.log -Tail 100
```

### Option 3: Search for Specific Issues
Find errors:
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "ERROR|✗"
```

Find yfinance activity:
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "yfinance|fetch_last_prices"
```

---

## 📊 What to Look For - yfinance Issues

### Successful Price Fetch
```
INFO fetch_last_prices called with 10 symbols: ['CGDV', 'IGM', 'QQQ', ...]
INFO Fetching prices from yfinance for 11 symbols: ['CGDV', 'IGM', 'QQQ', ..., 'USDCAD=X']
INFO ✓ Fetched price for CGDV: 25.43
INFO ✓ Fetched price for IGM: 42.15
INFO Returning 11 prices: ['CGDV', 'IGM', 'QQQ', ...]
```

### Failed Price Fetch (Symbol Not Found)
```
WARNING ✗ No price available for XYZ123 (fast_info.last_price is None)
```

### Network/API Error
```
ERROR ✗ Error creating yfinance Tickers object: ConnectionError: ...
```

### Using Cached Prices
```
DEBUG Using cached price for AAPL: 180.50
INFO All prices served from cache
```

---

## 🐛 Troubleshooting Common Issues

### Issue: All Prices Show Dashes (—)

**Check the log for:**
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "fetch_last_prices" -Context 5
```

**Possible Causes:**

1. **Internet Connection Issue**
   - Look for: `ConnectionError`, `timeout`, `network`
   - Solution: Check internet connectivity

2. **Invalid Ticker Symbols**
   - Look for: `✗ No price available for SYMBOL`
   - Solution: Verify ticker symbols are correct (e.g., Canadian stocks need `.TO` suffix)

3. **yfinance Library Issue**
   - Look for: `ModuleNotFoundError`, `ImportError`
   - Solution: Reinstall yfinance: `pip install --upgrade yfinance`

4. **Yahoo Finance API Down**
   - Look for: `HTTP 429`, `rate limit`, `503 Service Unavailable`
   - Solution: Wait and retry later

### Issue: Some Prices Missing

**Canadian Stocks**: Make sure symbols end with `.TO` (e.g., `VDY.TO` not `VDY`)
**US Stocks**: Use plain symbol (e.g., `AAPL`)
**Mutual Funds**: May not have real-time pricing via Yahoo Finance

### Issue: Prices Not Updating

Prices are cached for **15 minutes**. Check the log:
```
DEBUG Using cached price for AAPL: 180.50
```

**To force refresh:**
1. Wait 15 minutes, or
2. Restart the Django server, or
3. Clear cache: `python manage.py shell -c "from django.core.cache import cache; cache.clear()"`

---

## 🔧 Enable More Detailed Logging

The logging is already set to `DEBUG` level. If you need even more detail:

### View Django SQL Queries
Edit `portfolio_manager/settings.py`, add to `LOGGING`:
```python
'loggers': {
    'django.db.backends': {
        'level': 'DEBUG',
        'handlers': ['console', 'file'],
    },
},
```

### External Library Logging (yfinance, requests)
```python
'loggers': {
    'yfinance': {
        'level': 'DEBUG',
        'handlers': ['console', 'file'],
    },
},
```

---

## 📈 Performance Monitoring

### Watch Portfolio Summary Page Load
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "Portfolio Summary|fetch_last_prices" | Select-Object -Last 20
```

### Check Import Operations
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "import|parsing" -CaseSensitive:$false
```

---

## 🚀 Quick Diagnostic Commands

### 1. Test yfinance Installation
```powershell
python -c "import yfinance as yf; print(yf.Ticker('AAPL').info['regularMarketPrice'])"
```

### 2. Check Last 50 Log Entries
```powershell
Get-Content logs/portfolio_manager.log -Tail 50
```

### 3. Count Errors Today
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "ERROR" | Measure-Object -Line
```

### 4. View All yfinance Activity
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "yfinance|fetch_last_prices|compute_portfolio_value"
```

---

## 📝 Log Message Types

| Level | Symbol | Meaning | Example |
|-------|--------|---------|---------|
| INFO | ℹ️ | Normal operation | `INFO Fetched price for AAPL: 180.50` |
| DEBUG | 🔍 | Detailed info | `DEBUG Using cached price for AAPL` |
| WARNING | ⚠️ | Potential issue | `WARNING No price available for XYZ` |
| ERROR | ❌ | Something failed | `ERROR Error fetching price: ConnectionError` |

---

## 💡 Pro Tips

1. **Keep log terminal open** while testing to see real-time feedback
2. **Clear cache** if testing price updates: `python manage.py shell -c "from django.core.cache import cache; cache.clear()"`
3. **Check symbol format**: Canadian stocks need `.TO`, ETFs might need special suffixes
4. **Yahoo Finance limits**: Too many requests in short time may cause temporary blocks
5. **Logs persist** across server restarts - check older entries for patterns

---

**Last Updated**: May 8, 2026
