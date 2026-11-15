"""JSON logs compatible with Cloud Logging's structured-field ingestion."""
import json
import logging
import time
from contextvars import ContextVar
from psycopg import Cursor

log_context = ContextVar("ridepulse_log_context", default={})


class JsonFormatter(logging.Formatter):
    def format(self, record):
        fields={"severity": record.levelname, "message": record.getMessage(), **log_context.get(),
                **getattr(record, "json_fields", {})}
        if record.exc_info:
            fields["exception"]=self.formatException(record.exc_info)
        return json.dumps(fields, default=str)


class TimedCursor(Cursor):
    def execute(self,*args,**kwargs):
        start=time.monotonic()
        try:
            return super().execute(*args,**kwargs)
        finally:
            logging.getLogger("ridepulse.sql").info("sql_completed",extra={"json_fields":{
                "sql_ms":round((time.monotonic()-start)*1000,2)}})


def configure_logging():
    logger = logging.getLogger("ridepulse")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
