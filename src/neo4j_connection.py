"""Thin wrapper around the Neo4j driver."""

import logging
import os

from config import ConfigurationError

logger = logging.getLogger(__name__)


class Neo4jConnection:
    """Run Cypher against Neo4j. Credentials come from environment variables."""

    def __init__(self, uri=None, username=None, password=None,
                 database=None, driver=None):

        self.uri = uri or os.getenv("NEO4J_URI")
        self.username = username or os.getenv("NEO4J_USERNAME")
        self.password = password or os.getenv("NEO4J_PASSWORD")
        self.database = database or os.getenv("NEO4J_DATABASE", "neo4j")

        if driver is None:
            missing = [
                name for name, value in (
                    ("NEO4J_URI", self.uri),
                    ("NEO4J_USERNAME", self.username),
                    ("NEO4J_PASSWORD", self.password),
                ) if not value
            ]

            if missing:
                raise ConfigurationError(
                    "Missing Neo4j settings: " + ", ".join(missing)
                    + ". Copy .env.example to .env and fill it in."
                )

            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password),
                connection_timeout=20,
            )

        self.driver = driver

    def verify_connection(self) -> bool:
        try:
            self.driver.verify_connectivity()
        except Exception as error:
            logger.error("Neo4j connection failed: %s: %s",
                         type(error).__name__, error)
            raise

        return True

    def run_query(self, query, parameters=None):
        """Run one auto-commit query (reads and schema commands)."""

        with self.driver.session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def run_transaction(self, statements):
        """Run several write statements atomically.

        ``statements`` is a list of ``(query, parameters)`` pairs. Either all
        of them are applied or none are. Returns one result list per statement.
        """

        def work(tx):
            return [
                tx.run(query, parameters or {}).data()
                for query, parameters in statements
            ]

        with self.driver.session(database=self.database) as session:
            return session.execute_write(work)

    def close(self):
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    with Neo4jConnection() as db:
        db.verify_connection()
        print(db.run_query("RETURN 'Hello GraphRAG!' AS message"))
