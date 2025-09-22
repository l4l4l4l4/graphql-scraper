import sys
import json
import time
import argparse
from typing import Dict, List, Any, Optional
import requests
from urllib.parse import urlparse

class GraphQLScraper:
    def __init__(self, endpoint_url: str, headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None):
        self.endpoint_url = endpoint_url
        self.headers = headers or {'Content-Type': 'application/json'}
        self.cookies = cookies or {}
        self.schema = None

    def validate_url(self) -> bool:
        """Validate the URL format"""
        try:
            result = urlparse(self.endpoint_url)
            return all([result.scheme, result.netloc])
        except:
            return False

    def fetch_schema(self) -> Dict:
        """Fetch GraphQL schema using introspection"""
        introspection_query = {
            "query": """
            query IntrospectionQuery {
                __schema {
                    queryType { name }
                    mutationType { name }
                    subscriptionType { name }
                    types {
                        ...FullType
                    }
                    directives {
                        name
                        description
                        locations
                        args {
                            ...InputValue
                        }
                    }
                }
            }

            fragment FullType on __Type {
                kind
                name
                description
                fields(includeDeprecated: true) {
                    name
                    description
                    args {
                        ...InputValue
                    }
                    type {
                        ...TypeRef
                    }
                    isDeprecated
                    deprecationReason
                }
                inputFields {
                    ...InputValue
                }
                interfaces {
                    ...TypeRef
                }
                enumValues(includeDeprecated: true) {
                    name
                    description
                    isDeprecated
                    deprecationReason
                }
                possibleTypes {
                    ...TypeRef
                }
            }

            fragment InputValue on __InputValue {
                name
                description
                type {
                    ...TypeRef
                }
                defaultValue
            }

            fragment TypeRef on __Type {
                kind
                name
                ofType {
                    kind
                    name
                    ofType {
                        kind
                        name
                        ofType {
                            kind
                            name
                            ofType {
                                kind
                                name
                                ofType {
                                    kind
                                    name
                                    ofType {
                                        kind
                                        name
                                        ofType {
                                            kind
                                            name
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """
        }

        try:
            response = requests.post(
                self.endpoint_url,
                headers=self.headers,
                json=introspection_query,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()
            if 'errors' in data:
                raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")

            self.schema = data['data']['__schema']
            print("✅ Schema fetched successfully")
            return self.schema

        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP error: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON response: {e}")

    def get_root_types(self) -> Dict[str, Any]:
        """Get root query, mutation, and subscription types"""
        if not self.schema:
            self.fetch_schema()

        root_types = {}
        for root_type in ['queryType', 'mutationType', 'subscriptionType']:
            if self.schema.get(root_type) and self.schema[root_type].get('name'):
                type_name = self.schema[root_type]['name']
                # Find the type definition
                for schema_type in self.schema['types']:
                    if schema_type['name'] == type_name:
                        root_types[root_type.replace('Type', '').capitalize()] = schema_type
                        break

        return root_types

    def generate_queries_for_type(self, type_def: Dict, operation_type: str) -> List[tuple]:
        """Generate basic queries for a type"""
        queries = []

        if type_def.get('fields'):
            for field in type_def['fields']:
                field_name = field['name']

                # Skip introspection fields
                if field_name.startswith('__'):
                    continue

                # Generate a query with arguments
                query, variables = self._build_query(field, operation_type)
                if query:
                    queries.append((query, variables))

        return queries

    def _get_type_string(self, type_ref: Dict) -> str:
        """Convert a type reference to a GraphQL type string"""
        if type_ref['kind'] == 'NON_NULL':
            return f"{self._get_type_string(type_ref['ofType'])}!"
        elif type_ref['kind'] == 'LIST':
            return f"[{self._get_type_string(type_ref['ofType'])}]"
        else:
            return type_ref['name']

    def _generate_default_value(self, type_ref: Dict) -> Any:
        """Generate a default value for a given type reference"""
        if type_ref['kind'] == 'NON_NULL':
            return self._generate_default_value(type_ref['ofType'])
        elif type_ref['kind'] == 'SCALAR':
            type_name = type_ref['name']
            if type_name == 'Int':
                return 0
            elif type_name == 'Float':
                return 0.0
            elif type_name == 'String':
                return "default"
            elif type_name == 'Boolean':
                return True
            elif type_name == 'ID':
                return "1"
            else:
                return "default"
        elif type_ref['kind'] == 'LIST':
            return []
        else:
            # For enum types, try to use the first value if available
            return "default"

    def _build_query(self, field: Dict, operation_type: str) -> tuple:
        """Build a GraphQL query for a field with arguments"""
        field_name = field['name']
        variables = {}
        variable_definitions = []
        args_list = []

        # Handle arguments
        if field.get('args'):
            for arg in field['args']:
                arg_name = arg['name']
                arg_type = arg['type']
                # For required arguments without defaults, generate a value
                if self._is_required_type(arg_type) and not arg.get('defaultValue'):
                    var_name = f"var_{arg_name}"
                    type_str = self._get_type_string(arg_type)
                    variable_definitions.append(f"${var_name}: {type_str}")
                    args_list.append(f"{arg_name}: ${var_name}")
                    variables[var_name] = self._generate_default_value(arg_type)
                else:
                    # Skip optional arguments for now
                    continue

        # Build the query
        query_parts = [field_name]
        if args_list:
            query_parts.append(f"({', '.join(args_list)})")

        # Add subfields if it's not a leaf type (i.e., not scalar or enum)
        base_type = self._get_base_type(field['type'])
        if base_type and base_type['kind'] not in ['SCALAR', 'ENUM']:
            subfields = ['__typename']
            # For object types, add up to 3 additional fields
            if base_type['kind'] == 'OBJECT' and base_type.get('fields'):
                for subfield in base_type['fields']:
                    if (not subfield['name'].startswith('__') and 
                        subfield['name'] != '__typename' and 
                        len(subfields) < 4):
                        subfields.append(subfield['name'])
            query_parts.append(f"{{ {' '.join(subfields)} }}")

        # Build the final query string
        query_body = ' '.join(query_parts)
        if variable_definitions:
            query_str = f"{operation_type}({', '.join(variable_definitions)}) {{ {query_body} }}"
        else:
            query_str = f"{operation_type} {{ {query_body} }}"

        return query_str, variables

    def _get_base_type(self, type_ref: Dict) -> Optional[Dict]:
        """Get the base type from a type reference"""
        while type_ref.get('ofType'):
            type_ref = type_ref['ofType']
        return type_ref

    def _get_base_type_name(self, type_ref: Dict) -> Optional[str]:
        """Get the base type name from a type reference"""
        base_type = self._get_base_type(type_ref)
        return base_type.get('name') if base_type else None

    def _is_required_type(self, type_ref: Dict) -> bool:
        """Check if a type is required (non-null)"""
        return type_ref.get('kind') == 'NON_NULL'

    def generate_all_queries(self) -> List[str]:
        """Generate queries for all root types"""
        if not self.schema:
            self.fetch_schema()

        root_types = self.get_root_types()
        all_queries = []

        for type_name, type_def in root_types.items():
            print(f"🔍 Generating queries for {type_name} type...")
            # Convert type name to lowercase for GraphQL operation type
            operation_type = type_name.lower()
            queries = self.generate_queries_for_type(type_def, operation_type)
            all_queries.extend(queries)
            print(f"   Generated {len(queries)} queries for {type_name}")

        return all_queries

    def execute_query(self, query: str, variables: Dict) -> Dict:
        """Execute a GraphQL query"""
        payload = {
            'query': query,
            'variables': variables,
            'operationName': None
        }

        try:
            response = requests.post(
                self.endpoint_url,
                headers=self.headers,
                cookies=self.cookies,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {'error': str(e)}
        except json.JSONDecodeError as e:
            return {'error': f'Invalid JSON response: {e}'}

    def scrape_everything(self) -> Dict:
        """Main scraping function"""
        print(f"🚀 Starting GraphQL scraping...")
        print(f"📡 Target endpoint: {self.endpoint_url}")

        if not self.validate_url():
            raise ValueError("Invalid URL format")

        # 1. Fetch schema
        self.fetch_schema()

        # 2. Generate all queries
        queries = self.generate_all_queries()
        print(f"✅ Generated {len(queries)} total queries")

        if not queries:
            print("❌ No queries generated. The schema might not have queryable fields.")
            return {'total_queries': 0, 'successful': 0, 'failed': 0, 'coverage': 0, 'results': []}

        # 3. Execute all queries
        results = []

        for i, (query, variables) in enumerate(queries, 1):
            print(f"📊 Executing query {i}/{len(queries)}: {query[:50]}...")

            result = self.execute_query(query, variables)

            success = 'errors' not in result and 'error' not in result
            results.append({
                'query': query,
                'variables': variables,
                'result': result,
                'success': success,
                'errors': result.get('errors'),
                'error': result.get('error')
            })

            # Add delay to avoid overwhelming the server
            time.sleep(0.1)

        # 4. Analyze results
        successful_queries = [r for r in results if r['success']]
        failed_queries = [r for r in results if not r['success']]

        coverage = (len(successful_queries) / len(queries)) * 100 if queries else 0

        print(f"\n📈 Scraping Results:")
        print(f"✅ Successful queries: {len(successful_queries)}")
        print(f"❌ Failed queries: {len(failed_queries)}")
        print(f"📊 Total coverage: {coverage:.2f}%")

        return {
            'total_queries': len(queries),
            'successful': len(successful_queries),
            'failed': len(failed_queries),
            'coverage': coverage,
            'results': results
        }

def main():
    """Main function with CLI argument handling"""
    parser = argparse.ArgumentParser(
        description='GraphQL API Scraper - Generates and executes all possible queries from a GraphQL schema'
    )
    parser.add_argument(
        'endpoint_url',
        help='GraphQL endpoint URL (e.g., https://api.example.com/graphql)'
    )
    parser.add_argument(
        '--output', '-o',
        default='graphql-scraping-results.json',
        help='Output filename (default: graphql-scraping-results.json)'
    )
    parser.add_argument(
        '--delay', '-d',
        type=float,
        default=0.1,
        help='Delay between queries in seconds (default: 0.1)'
    )
    parser.add_argument(
        '--auth-token', '-a',
        help='Authorization token (if required)'
    )
    parser.add_argument(
        '--cookie', '-c',
        help='Cookie string (e.g., "session=abc123; token=xyz")'
    )

    args = parser.parse_args()

    # Set up headers if auth token provided
    headers = {'Content-Type': 'application/json'}
    if args.auth_token:
        headers['Authorization'] = f'Bearer {args.auth_token}'

    # Parse cookies if provided
    cookies = {}
    if args.cookie:
        for cookie in args.cookie.split(';'):
            if '=' in cookie:
                key, value = cookie.strip().split('=', 1)
                cookies[key] = value

    scraper = GraphQLScraper(args.endpoint_url, headers, cookies)

    try:
        results = scraper.scrape_everything()

        # Save results to file
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)

        print(f"💾 Results saved to {args.output}")

        # Show sample successful results
        successful_results = [r for r in results['results'] if r['success']]
        if successful_results:
            print("\n🎯 Sample successful queries:")
            for i, result in enumerate(successful_results[:3], 1):
                print(f"\n--- Query {i} ---")
                print(f"Query: {result['query']}")
                if 'data' in result['result']:
                    data_keys = list(result['result']['data'].keys()) if result['result']['data'] else []
                    print(f"Response keys: {data_keys}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
