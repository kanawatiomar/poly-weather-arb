$py  = "C:\Users\kanaw\AppData\Local\Programs\Python\Python311\python.exe"
$dir = "C:\Users\kanaw\.openclaw\workspace\ventures\ventures\poly-weather-arb"
Set-Location $dir
& $py "$dir\scanner.py" --days 3 --min-edge 0.05 --min-vol 500 --temp-only
& $py "$dir\auto_trade.py"
