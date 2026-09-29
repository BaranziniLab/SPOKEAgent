import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from neo4j.exceptions import AuthError
from spokeagent import server


class ResolverReviewTests(unittest.TestCase):
    def call(self, arguments, handler, clock=None):
        driver = MagicMock()
        tx = driver.session.return_value.__enter__.return_value.begin_transaction.return_value.__enter__.return_value
        queries = []

        def run(query, parameters):
            queries.append((query, parameters))
            rows = handler(query, parameters)
            return [MagicMock(data=lambda row=row: row) for row in rows]

        tx.run.side_effect = run

        async def invoke():
            mcp = server.create_spoke_server(server.SPOKEConfig(
                uri='bolt://localhost:7687', username='test', password='test', database='neo4j'))
            async with Client(mcp) as client:
                return await client.call_tool('resolve_entity', arguments, raise_on_error=False)

        with patch.object(server.GraphDatabase, 'driver', return_value=driver):
            if clock is not None:
                with patch.object(server.time, 'monotonic', side_effect=clock):
                    result = asyncio.run(invoke())
            else:
                result = asyncio.run(invoke())
        return result, queries

    def test_label_injection_rejected_before_database_call(self):
        result, queries = self.call({'query': 'EGFR', 'label': 'Gene) DELETE n //'}, lambda *_: [])
        self.assertTrue(result.is_error)
        self.assertEqual(queries, [])

    def test_authentication_failure_is_not_reported_as_no_candidates(self):
        def denied(*_):
            raise AuthError('authentication failed')
        result, queries = self.call({'query': 'EGFR', 'label': 'Gene'}, denied)
        self.assertTrue(result.is_error)
        self.assertEqual(len(queries), 1)

    def test_explicit_gene_label_does_not_scan_disease_or_organism(self):
        def rows(query, params):
            if 'n.identifier = $qi' in query:
                return [{'l': 'Gene', 'name': 'EGFR', 'id': 1956}]
            return []
        result, queries = self.call({'query': '1956', 'label': 'Gene'}, rows)
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text)['candidates'][0]['identifier'], 1956)
        self.assertTrue(all('Disease' not in query and 'Organism' not in query for query, _ in queries))

    def test_resolution_shares_one_transaction_budget(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        tx = session.begin_transaction.return_value.__enter__.return_value
        tx.run.return_value = []

        async def invoke():
            mcp = server.create_spoke_server(server.SPOKEConfig(
                uri='bolt://localhost:7687', username='test', password='test', database='neo4j'))
            async with Client(mcp) as client:
                return await client.call_tool('resolve_entity', {'query': 'EGFR', 'label': 'Gene'},
                                              raise_on_error=False)

        from types import SimpleNamespace
        timer = SimpleNamespace(monotonic=MagicMock(side_effect=[100, 110, 136]))
        with patch.object(server.GraphDatabase, 'driver', return_value=driver), patch.object(server, 'time', timer):
            result = asyncio.run(invoke())
        self.assertTrue(result.is_error)
        self.assertEqual(tx.run.call_count, 1)
        self.assertEqual(session.begin_transaction.call_args.kwargs['timeout'], 25)

    def test_all_discovery_and_query_tools_withhold_driver_secrets(self):
        secret = 'SENTINEL_PASSWORD_AND_PATIENT_VALUE'
        driver = MagicMock()
        tx = driver.session.return_value.__enter__.return_value.begin_transaction.return_value.__enter__.return_value
        tx.run.side_effect = AuthError(secret)

        async def invoke():
            mcp = server.create_spoke_server(server.SPOKEConfig(
                uri='bolt://localhost:7687', username='test', password='test', database='neo4j'))
            cases = {
                'get_spoke_schema': {},
                'resolve_entity': {'query': 'EGFR', 'label': 'Gene'},
                'describe_node': {'query': 'EGFR', 'label': 'Gene'},
                'find_path': {'source': 'EGFR', 'target': 'TP53', 'source_label': 'Gene'},
                'query_spoke': {'cypher_query': 'RETURN 1 AS value'},
            }
            async with Client(mcp) as client:
                for name, arguments in cases.items():
                    result = await client.call_tool(name, arguments, raise_on_error=False)
                    self.assertTrue(result.is_error, name)
                    message = result.content[0].text
                    self.assertNotIn(secret, message, name)
                    self.assertIn('authentication or authorization', message, name)

        with patch.object(server.GraphDatabase, 'driver', return_value=driver), self.assertLogs(server.logger, level='ERROR') as captured:
            asyncio.run(invoke())
        self.assertNotIn(secret, '\n'.join(captured.output))
        self.assertTrue(all(record.getMessage() == 'AuthError' for record in captured.records))

    def test_error_categories_and_initialization_suppress_raw_messages(self):
        from fastmcp.exceptions import ToolError
        from neo4j.exceptions import ClientError, ServiceUnavailable
        cases = [
            (TimeoutError('SENTINEL'), 'timed out'),
            (ServiceUnavailable('SENTINEL'), 'connection unavailable'),
        ]
        for code, category in [
            ('Neo.ClientError.Procedure.ProcedureNotFound', 'APOC'),
            ('Neo.ClientError.Statement.SyntaxError', 'query or schema'),
            ('Neo.TransientError.Transaction.TransactionTimedOut', 'timed out'),
        ]:
            error = ClientError('SENTINEL')
            error._neo4j_code = code
            cases.append((error, category))
        with self.assertLogs(server.logger, level='ERROR'):
            for error, category in cases:
                message = str(server._safe_tool_error(error))
                self.assertIn(category, message)
                self.assertNotIn('SENTINEL', message)
            with patch.object(server.GraphDatabase, 'driver', side_effect=RuntimeError('SENTINEL')):
                with self.assertRaises(ToolError) as raised:
                    server.create_spoke_server(server.SPOKEConfig(
                        uri='bolt://localhost:7687', username='test', password='test', database='neo4j'))
                self.assertNotIn('SENTINEL', str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)

    def test_missing_optional_index_only_is_tolerated(self):
        from neo4j.exceptions import ClientError
        missing = ClientError('missing')
        missing._neo4j_code = 'Neo.ClientError.Schema.IndexNotFound'
        server._optional_lookup_error(missing)
        denied = ClientError('denied')
        denied._neo4j_code = 'Neo.ClientError.Security.Forbidden'
        with self.assertRaises(ClientError):
            server._optional_lookup_error(denied)


if __name__ == '__main__':
    unittest.main()
