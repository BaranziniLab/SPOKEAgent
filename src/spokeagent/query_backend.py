"""SPOKE connection configuration and streaming Neo4j reads."""
import base64
import os

DIALECT = 'cypher'
SECRET_KEYS = ('SPOKEAGENT_PASSCODE', 'KNOWLEDGE_GRAPH_USERNAME', 'KNOWLEDGE_GRAPH_PASSWORD')
ENV_KEYS = ('KNOWLEDGE_GRAPH_URI', 'KNOWLEDGE_GRAPH_USERNAME', 'KNOWLEDGE_GRAPH_PASSWORD', 'KNOWLEDGE_GRAPH_DATABASE', 'SPOKEAGENT_PASSCODE')


def from_environment(env=None):
    env = os.environ if env is None else env
    if env.get('KNOWLEDGE_GRAPH_URI'):
        values = dict(uri=env['KNOWLEDGE_GRAPH_URI'], username=env.get('KNOWLEDGE_GRAPH_USERNAME'), password=env.get('KNOWLEDGE_GRAPH_PASSWORD'), database=env.get('KNOWLEDGE_GRAPH_DATABASE', 'neo4j'))
    elif env.get('SPOKEAGENT_PASSCODE'):
        key = env['SPOKEAGENT_PASSCODE'].encode()
        def decode(value):
            return bytes(b ^ key[i % len(key)] for i, b in enumerate(base64.b64decode(value))).decode()
        try:
            values = dict(uri=decode('ER8DH18bTh8cHBsKRQZTDUIZEAMJRQBQFFZbRUhY'), username=decode('HRUAXw8='), password=decode('ICAgICBQBBo='), database=decode('AAAAAAA='))
        except (UnicodeError, ValueError):
            raise ValueError('Invalid SPOKE passcode; configure credentials through BioRouter.') from None
    else:
        values = {}
    check_credentials(values)
    return values


def check_credentials(values):
    if not all(values.get(k) for k in ('uri', 'username', 'password', 'database')):
        raise ValueError('Configure SPOKEAGENT_PASSCODE, or KNOWLEDGE_GRAPH_URI, KNOWLEDGE_GRAPH_USERNAME, KNOWLEDGE_GRAPH_PASSWORD and optionally KNOWLEDGE_GRAPH_DATABASE through BioRouter or interactive auth.')
    if not values['uri'].startswith(('bolt://', 'bolt+s://', 'bolt+ssc://', 'neo4j://', 'neo4j+s://', 'neo4j+ssc://')):
        raise ValueError('Invalid Neo4j URI or SPOKE passcode.')


def encode_value(value):
    from neo4j.graph import Node, Relationship, Path
    from neo4j.spatial import Point
    if isinstance(value, Node):
        return {'_type': 'node', 'element_id': value.element_id, 'labels': sorted(value.labels), 'properties': {k: encode_value(v) for k, v in value.items()}}
    if isinstance(value, Relationship):
        return {'_type': 'relationship', 'element_id': value.element_id, 'type': value.type, 'start_element_id': value.start_node.element_id, 'end_element_id': value.end_node.element_id, 'properties': {k: encode_value(v) for k, v in value.items()}}
    if isinstance(value, Path):
        return {'_type': 'path', 'nodes': [encode_value(v) for v in value.nodes], 'relationships': [encode_value(v) for v in value.relationships]}
    if isinstance(value, Point):
        return {'_type': 'point', 'srid': value.srid, 'coordinates': list(value)}
    if isinstance(value, dict):
        return {k: encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    return value


def stream(request, credentials, progress):
    from neo4j import GraphDatabase
    check_credentials(credentials)
    with GraphDatabase.driver(credentials['uri'], auth=(credentials['username'], credentials['password']), connection_timeout=20, max_connection_pool_size=1) as driver:
        with driver.session(database=credentials['database'], default_access_mode='READ', fetch_size=1000) as session:
            with session.begin_transaction(timeout=request['timeout_seconds']) as tx:
                progress['phase'] = 'planning' if request['mode'] == 'explain' else 'executing'
                query = ('EXPLAIN ' if request['mode'] == 'explain' else '') + request['query']
                result = tx.run(query, request['parameters'])
                if request['mode'] == 'explain':
                    yield ['plan'], [[result.consume().plan]]
                else:
                    columns = list(result.keys())
                    batch = []
                    for record in result:
                        batch.append([encode_value(value) for value in record.values()])
                        if len(batch) == 1000:
                            yield columns, batch
                            batch = []
                    yield columns, batch
                tx.commit()
