import os
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"
workers = 1
threads = 8
timeout = 300
keepalive = 5
worker_class = "gthread"
preload_app = False
