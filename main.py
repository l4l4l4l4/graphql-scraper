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
            json.dump(self.schema, open("schema.json", 'w'))
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

    def _build_selection_set(self, field_type: Dict, depth: int = 0) -> str:
        """Recursively build a selection set for a field type"""
        if depth >= 3:  # Limit depth to avoid overly complex queries
            return ""
            
        base_type = self._get_base_type(field_type)
        if not base_type or base_type['kind'] in ['SCALAR', 'ENUM']:
            return ""
            
        # Get the type definition from the schema
        type_name = base_type['name']
        type_def = None
        for t in self.schema['types']:
            if t['name'] == type_name:
                type_def = t
                break
                
        if not type_def or not type_def.get('fields'):
            return ""
            
        fields = []
        for field in type_def['fields']:
            if field['name'].startswith('__'):
                continue
                
            field_str = field['name']
            field_base_type = self._get_base_type(field['type'])
            
            # If it's a non-scalar field, add a selection set
            if field_base_type and field_base_type['kind'] not in ['SCALAR', 'ENUM']:
                sub_selection = self._build_selection_set(field['type'], depth + 1)
                if sub_selection:
                    field_str += f" {{ {sub_selection} }}"
                else:
                    # If no sub-selection could be built, try to include at least one field
                    sub_type_name = field_base_type['name']
                    sub_type_def = None
                    for t in self.schema['types']:
                        if t['name'] == sub_type_name:
                            sub_type_def = t
                            break
                    
                    if sub_type_def and sub_type_def.get('fields'):
                        # Find the first scalar field
                        for sub_field in sub_type_def['fields']:
                            if sub_field['name'].startswith('__'):
                                continue
                            sub_field_base_type = self._get_base_type(sub_field['type'])
                            if sub_field_base_type and sub_field_base_type['kind'] in ['SCALAR', 'ENUM']:
                                field_str += f" {{ {sub_field['name']} }}"
                                break
            fields.append(field_str)
                
        return ' '.join(fields)

    def _build_query(self, field: Dict, operation_type: str) -> tuple:
        """Build a GraphQL query for a field with arguments"""
        field_name = field['name']
        variables = {}
        variable_definitions = []
        args_list = []

        # Handle arguments - include all required arguments (non-null) regardless of default value
        if field.get('args'):
            for arg in field['args']:
                arg_name = arg['name']
                arg_type = arg['type']
                # Include all required arguments (non-null types)
                if self._is_required_type(arg_type):
                    var_name = f"var_{arg_name}"
                    type_str = self._get_type_string(arg_type)
                    variable_definitions.append(f"${var_name}: {type_str}")
                    args_list.append(f"{arg_name}: ${var_name}")
                    variables[var_name] = self._generate_default_value(arg_type)

        # Build the query
        query_parts = [field_name]
        if args_list:
            query_parts.append(f"({', '.join(args_list)})")

        # Build selection set for non-leaf types
        base_type = self._get_base_type(field['type'])
        if base_type and base_type['kind'] not in ['SCALAR', 'ENUM']:
            selection_set = self._build_selection_set(field['type'])
            if selection_set:
                query_parts.append(f"{{ {selection_set} }}")
            else:
                # Fallback: include id field if available
                query_parts.append("{ id }")

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

    def _extract_parameters_from_result(self, data: Dict, parameter_values: Dict):
        """Recursively extract IDs and other values from query results"""
        if not isinstance(data, dict) and not isinstance(data, list):
            return
        
        if isinstance(data, dict):
            for key, value in data.items():
                if key == 'id' and isinstance(value, str):
                    if 'ids' not in parameter_values:
                        parameter_values['ids'] = []
                    if value not in parameter_values['ids']:
                        parameter_values['ids'].append(value)
                elif key == 'username' and isinstance(value, str):
                    if 'usernames' not in parameter_values:
                        parameter_values['usernames'] = []
                    if value not in parameter_values['usernames']:
                        parameter_values['usernames'].append(value)
                elif isinstance(value, str):
                    if 'strings' not in parameter_values:
                        parameter_values['strings'] = []
                    if value not in parameter_values['strings']:
                        parameter_values['strings'].append(value)
                elif isinstance(value, int):
                    if 'ints' not in parameter_values:
                        parameter_values['ints'] = []
                    if value not in parameter_values['ints']:
                        parameter_values['ints'].append(value)
                elif isinstance(value, bool):
                    if 'bools' not in parameter_values:
                        parameter_values['bools'] = []
                    if value not in parameter_values['bools']:
                        parameter_values['bools'].append(value)
                
                # Recursively process nested structures
                self._extract_parameters_from_result(value, parameter_values)
        
        elif isinstance(data, list):
            for item in data:
                self._extract_parameters_from_result(item, parameter_values)

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

        # 2. Generate all queries and separate mutations
        all_operations = self.generate_all_queries()
        queries = []
        mutations = []
        
        for op, vars in all_operations:
            if op.startswith('mutation'):
                mutations.append((op, vars))
            else:
                queries.append((op, vars))
                
        print(f"✅ Generated {len(queries)} queries and {len(mutations)} mutations")

        # 3. First, execute non-parameterized queries to gather IDs and other parameters
        print("🔍 First pass: executing non-parameterized queries...")
        first_pass_results = []
        parameter_values = {}
        
        # Execute queries without parameters first
        for i, (query, variables) in enumerate(queries, 1):
            if not variables:  # No parameters needed
                print(f"📊 Executing query {i}/{len(queries)}: {query[:50]}...")
                result = self.execute_query(query, variables)
                success = 'errors' not in result and 'error' not in result
                first_pass_results.append({
                    'query': query,
                    'variables': variables,
                    'result': result,
                    'success': success,
                    'errors': result.get('errors'),
                    'error': result.get('error')
                })
                
                # Extract IDs and other values from successful results
                if success and 'data' in result:
                    self._extract_parameters_from_result(result['data'], parameter_values)
                
                time.sleep(0.1)

        # 4. Now execute parameterized queries with extracted values
        print("🔍 Second pass: executing parameterized queries...")
        second_pass_results = []
        
        for i, (query, variables) in enumerate(queries, 1):
            if variables:  # This query has parameters
                print(f"📊 Executing parameterized query {i}/{len(queries)}: {query[:50]}...")
                
                # Fill variables with extracted values
                filled_variables = {}
                for var_name, var_value in variables.items():
                    # Try to find a suitable value based on the variable name or type
                    value_found = None
                    
                    # Look for IDs first
                    if 'id' in var_name.lower() and parameter_values.get('ids'):
                        value_found = parameter_values['ids'][0]
                    elif 'username' in var_name.lower() and parameter_values.get('usernames'):
                        value_found = parameter_values['usernames'][0]
                    elif parameter_values.get('strings'):
                        value_found = parameter_values['strings'][0]
                    elif parameter_values.get('ints'):
                        value_found = parameter_values['ints'][0]
                    elif parameter_values.get('bools'):
                        value_found = parameter_values['bools'][0]
                    
                    if value_found is not None:
                        filled_variables[var_name] = value_found
                    else:
                        # If no value found, use the original generated value
                        filled_variables[var_name] = var_value
                
                result = self.execute_query(query, filled_variables)
                success = 'errors' not in result and 'error' not in result
                second_pass_results.append({
                    'query': query,
                    'variables': filled_variables,
                    'result': result,
                    'success': success,
                    'errors': result.get('errors'),
                    'error': result.get('error')
                })
                
                time.sleep(0.1)

        # 5. Add mutations to results without executing them
        mutation_results = []
        for mutation, variables in mutations:
            mutation_results.append({
                'query': mutation,
                'variables': variables,
                'result': {'skipped': True, 'message': 'Mutations are not executed'},
                'success': False,
                'errors': ['Mutation skipped - not executed for safety'],
                'error': None
            })

        # 6. Combine all results
        results = first_pass_results + second_pass_results + mutation_results
        all_queries = queries + mutations

        # 7. Analyze results
        successful_queries = [r for r in results if r['success']]
        failed_queries = [r for r in results if not r['success']]

        coverage = (len(successful_queries) / len(all_queries)) * 100 if all_queries else 0

        print(f"\n📈 Scraping Results:")
        print(f"✅ Successful queries: {len(successful_queries)}")
        print(f"❌ Failed queries: {len(failed_queries)}")
        print(f"⏭️  Skipped mutations: {len(mutations)}")
        print(f"📊 Total coverage: {coverage:.2f}%")

        return {
            'total_queries': len(all_queries),
            'successful': len(successful_queries),
            'failed': len(failed_queries),
            'skipped_mutations': len(mutations),
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
