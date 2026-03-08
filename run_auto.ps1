$py  = "C:\Users\kanaw\AppData\Local\Programs\Python\Python311\python.exe"
$dir = "C:\Users\kanaw\.openclaw\workspace\ventures\ventures\poly-weather-arb"
$dashboard = "C:\Users\kanaw\.openclaw\workspace\polymarket-dashboard"
$log = "$dir\auto_run.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $log -Append
}

Log "=== Poly Auto-Run START ==="

# 1. Run scanner
Log "Running scanner..."
$scanOut = & $py "$dir\scanner.py" --days 3 --min-edge 0.05 --min-vol 500 2>&1
$scanOut | Out-File "$dir\last_scan.txt" -Encoding utf8
Log "Scanner done."

# 2. Check for opportunities
$results = Get-Content "$dir\scan_results.json" | ConvertFrom-Json
$opps = $results.opportunities | Where-Object { [double]$_.edge_pct -ge 0.20 -and $_.date -ge (Get-Date).AddDays(2).ToString("yyyy-MM-dd") }
Log "Opportunities with >20% edge, 2+ days out: $($opps.Count)"

# 3. Auto trade if there are edges
if ($opps.Count -gt 0) {
    Log "Running auto_trade..."
    $tradeOut = & $py "$dir\auto_trade.py" 2>&1
    $tradeOut | Out-File "$dir\last_trade.txt" -Encoding utf8
    Log "Trade output: $($tradeOut -join ' | ')"
} else {
    Log "No eligible trades. Skipping."
}

# 4. Update dashboard data.json with live prices
Log "Updating dashboard prices..."
node "$dashboard\patch_prices.mjs" 2>&1 | Out-File "$dashboard\last_patch.txt" -Encoding utf8

# 5. Push dashboard to GitHub
Set-Location $dashboard
$status = git status --porcelain data.json
if ($status) {
    git add data.json
    git commit -m "auto: prices $(Get-Date -Format 'MM-dd HH:mm')"
    git push
    Log "Dashboard pushed."
} else {
    Log "No dashboard changes."
}

Log "=== Poly Auto-Run DONE ==="
