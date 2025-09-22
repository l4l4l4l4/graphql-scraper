# GraphQL Scraper

A tool to automatically generate and execute all possible queries from a GraphQL schema.

## Usage

### Basic Usage
```bash
python main.py https://example.com/graphql
```

### With Authentication Headers
```bash
python main.py https://example.com/graphql \
  --header "Authorization: Bearer your-token" \
  --header "X-Custom-Header: value"
```

### With Cookies
```bash
python main.py https://example.com/graphql \
  --cookie "session=abc123; token=xyz"
```

### Custom Output Directory
```bash
python main.py https://example.com/graphql --output-dir my-results
```

## Output

The tool creates the following directory structure:

```
result/
├── queries/
│   ├── allBugs.graphql
│   ├── findUser.graphql
│   └── ...
├── mutations/
│   ├── modifyBug.graphql
│   └── ...
└── query_responses/
    ├── allBugs.json
    ├── findUser.json
    └── ...
```

- `queries/` - Contains all generated GraphQL queries
- `mutations/` - Contains all generated GraphQL mutations (not executed)
- `query_responses/` - Contains JSON responses from executed queries

## Notes

- Mutations are generated but not executed for safety
- Queries are automatically formatted with proper indentation
- The tool includes required arguments with default values
- Responses are saved as pretty-printed JSON

## Requirements

- Python 3.6+
- requests library

Install dependencies with:
```bash
pip install requests
```
