"""Candidate PostgreSQL 16 view baseline, generated from repository migration 001.

Changing migration or PostgreSQL major requires A/D review and regeneration.
This pins definitions, not the privileges of owners or dependent objects; A/D review is pending.
"""

POSTGRES_MAJOR = 16
VIEW_SHA256 = {
    "paper_statistics": "d5f605a924e82e12b9c6244d2732040cfeee4c3e67694b2c9fe5913aecf4edf6",
    "paper_topics": "d234630ff1a830a00fdb4472118ae5c3418da406ca5c86170259d2ae0dd7dfe8",
}

BINDING_BODY = (
    "\n    SELECT EXISTS (SELECT 1 FROM paperpilot_security.project_readers "
    "WHERE login_role = SESSION_USER AND project_id = expected_project)\n"
)
