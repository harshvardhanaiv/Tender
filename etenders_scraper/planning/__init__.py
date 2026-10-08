"""Planning Leads: UK planning applications as pre-tender private-sector opportunities.

This package is deliberately separate from `etenders_scraper.sources`. A planning
application is not a tender: it has no submission deadline, no CPV code and no contract
value, so it breaks several assumptions baked into the tender pipeline (deadline sorting
in deadline.py, the >=8-char title check in progressive_search.py, and the value filter
in web/app.js that excludes rows with no parseable value).

It also cannot be fetched live per user search. PlanIt asks for at most one request per
minute and ~300 per day, so the data is harvested into Postgres overnight by
harvester.py and every user query is served from our own table.
"""
