import unittest

from sqlalchemy import create_engine, text

from backend.observability import enable_sql_logging, request_id


class SQLLoggingTests(unittest.TestCase):
    def test_sql_log_has_request_id_and_duration_without_bound_values(self):
        engine = create_engine("sqlite:///:memory:")
        enable_sql_logging(engine)
        token = request_id.set("test-request")
        try:
            with self.assertLogs("uvicorn.error", level="INFO") as captured:
                with engine.connect() as connection:
                    connection.execute(text("SELECT :private_value"), {"private_value": "secret-value"})
        finally:
            request_id.reset(token)
            engine.dispose()
        output = "\n".join(captured.output)
        self.assertIn("sql request_id=test-request", output)
        self.assertIn("duration_ms=", output)
        self.assertIn("SELECT ?", output)
        self.assertNotIn("secret-value", output)

    def test_failed_statement_logs_error_type(self):
        engine = create_engine("sqlite:///:memory:")
        enable_sql_logging(engine)
        try:
            with self.assertLogs("uvicorn.error", level="WARNING") as captured:
                with self.assertRaises(Exception):
                    with engine.connect() as connection:
                        connection.execute(text("SELECT * FROM absent_table"))
        finally:
            engine.dispose()
        self.assertIn("sql_error request_id=-", "\n".join(captured.output))
        self.assertIn("OperationalError", "\n".join(captured.output))

    def test_sql_logging_never_emits_bound_values_even_with_legacy_flag(self):
        engine = create_engine("sqlite:///:memory:")
        enable_sql_logging(engine, include_parameters=True)
        try:
            with self.assertLogs("uvicorn.error", level="INFO") as captured:
                with engine.connect() as connection:
                    connection.execute(text("SELECT :private_value"), {"private_value": "private-query"})
        finally:
            engine.dispose()
        self.assertNotIn("private-query", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
