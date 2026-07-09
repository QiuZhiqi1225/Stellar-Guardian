$port = if ($env:PORT) { $env:PORT } else { "8000" }
$backendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "0.0.0.0" }
python -m uvicorn app.main:app --host $backendHost --port $port --reload
