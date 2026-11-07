from django.db import models


class OrderedJSONField(models.JSONField):
    """
    JSON stored in a Postgres `json` column, so object keys keep the order they were written in.

    A plain JSONField is `jsonb`, which sorts keys by length and then bytes. Flow Studio's engine depends on stored key
    order (03 engine, section 12 iterates `draft.nodes` in stored key order), so that would change results. Nothing
    queries inside these columns: the columns on the model exist for that.
    """

    def db_type(self, connection):
        return "json"

    def from_db_value(self, value, expression, connection):
        # psycopg2 decodes `json` columns itself, unlike the `jsonb` columns Django's JSONField expects.
        return value
