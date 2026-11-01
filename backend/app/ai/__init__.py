"""Claude API features (PLAN 7): Ask, the monthly review and category suggestions.

Everything the model sees is read through *_safe views on a read-only
connection with an authorizer (safe.py). Calls go through one client wrapper
(client.py) that checks the caps first and writes an ai_usage row after.
"""
