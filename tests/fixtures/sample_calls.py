"""A fixture for calls_to.

Mentions _env_client() in this module docstring, which a text search counts as
an occurrence and a parse does not.
"""


def client_for(name):
    """Build a client. Calls _env_client() when nothing is configured."""
    return _env_client(name)


def _env_client(name):
    return {"name": name}


def unrelated():
    return 1
