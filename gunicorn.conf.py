# gunicorn.conf.py
bind = "0.0.0.0:5000"

# A single process keeps the session, cache and Prometheus registry consistent;
# threads give concurrency. More workers would need PROMETHEUS_MULTIPROC_DIR and a shared cache.
workers = 1
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 20
keepalive = 5

# Request logging is done (as JSON, without query strings) by the app itself.
accesslog = None
errorlog = "-"
