"""AI blueprint — route definitions live in server.create_app (DeepSeek pipeline).

Endpoints in this domain:
  POST /api/fit-score
  POST /api/analyse, /api/summary, /api/proposal
  POST /api/download-covering-letter, /api/download-proposal, ...
"""
from flask import Blueprint

ai_bp = Blueprint("ai", __name__)
