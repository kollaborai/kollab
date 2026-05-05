---
name: sql-query-optimization
description: "SQL Query Optimization skill - write efficient database queries and optimize performance"
---

sql optimization mode: PERFORMANCE-FOCUSED QUERIES

when this skill is active, you follow disciplined sql optimization practices.
this is a comprehensive guide to writing efficient, scalable database queries.


PHASE 0: SQL ENVIRONMENT VERIFICATION

before writing ANY sql queries, verify your database environment.


check database connectivity

  <terminal>python -c "import sqlite3; print('sqlite3 available')" 2>/dev/null || echo "sqlite3 not available"</terminal>

if using postgresql:
  <terminal>python -c "import psycopg2; print('psycopg2 available')" 2>/dev/null || pip install psycopg2-binary</terminal>

if using mysql:
  <terminal>python -c "import pymysql; print('pymysql available')" 2>/dev/null || pip install pymysql</terminal>

verify sqlalchemy:
  <terminal>python -c "import sqlalchemy; print(f'sqlalchemy {sqlalchemy.__version__}')" 2>/dev/null || pip install sqlalchemy</terminal>


check database files

  <terminal>find . -maxdepth 2 -type f \( -name "*.db" -o -name "*.sqlite" -o -name "*.sql" \) 2>/dev/null | head -10</terminal>

list database sizes:
  <terminal>find . -maxdepth 2 -type f \( -name "*.db" -o -name "*.sqlite" \) -exec ls -lh {} \; 2>/dev/null</terminal>


check existing query patterns

  <terminal>find . -name "*.sql" -type f 2>/dev/null | head -10</terminal>

sample existing queries:
  <terminal>find . -name "*.sql" -type f 2>/dev/null -exec head -50 {} \; | head -100</terminal>


check for query logging

  <terminal>python -c "import logging; print('logging module ready')" 2>/dev/null</terminal>

if using sqlalchemy, enable query logging:
  import logging
  logging.basicConfig()
  logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


verify database introspection tools

  <terminal>python -c "import pandas; print('pandas available for query results')" 2>/dev/null</terminal>

verify read_sql:
  <terminal>python -c "import pandas; import sqlite3; con = sqlite3.connect(':memory:'); print('pandas.read_sql available')" 2>/dev/null</terminal>


PHASE 1: SQL OPTIMIZATION MINDSET


understand the data before querying

optimization starts with understanding:
  - table sizes and row counts
  - existing indexes
  - column types and distributions
  - foreign key relationships
  - query patterns and frequency

measure before optimizing:
  - execution time
  - rows examined
  - index usage
  - memory consumption

premature optimization is the root of all evil.


the optimization hierarchy

  [1] eliminate unnecessary work
      - select only needed columns
      - filter early, filter often
      - avoid cartesian products

  [2] use indexes effectively
      - index columns used in where, join, order by
      - use composite indexes for multi-column queries
      - avoid function calls on indexed columns

  [3] minimize data movement
      - use appropriate joins
      - filter before joining when possible
      - use subqueries wisely

  [4] optimize query structure
      - use exists instead of in for subqueries
      - use union all instead of union
      - avoid select distinct when possible


PHASE 2: QUERY PERFORMANCE ANALYSIS


explain plan analysis

sqlite explain:
  import sqlite3

  conn = sqlite3.connect('database.db')
  cursor = conn.cursor()
  cursor.execute("EXPLAIN QUERY PLAN SELECT * FROM users WHERE id = 1")
  for row in cursor.fetchall():
      print(row)

postgresql explain:
  EXPLAIN ANALYZE SELECT * FROM users WHERE id = 1;

mysql explain:
  EXPLAIN SELECT * FROM users WHERE id = 1;

what to look for:
  - full table scans (bad)
  - index scans (good)
  - index seeks (better)
  - key lookups (context-dependent)
  - sort operations (can be avoided with indexes)


execution time measurement

python timing:
  import time
  import sqlite3
  import pandas as pd

  start = time.time()
  df = pd.read_sql("SELECT * FROM large_table", conn)
  elapsed = time.time() - start
  print(f"Query executed in {elapsed:.2f} seconds")
  print(f"Rows returned: {len(df)}")

multiple runs for average:
  times = []
  for i in range(5):
      start = time.time()
      df = pd.read_sql(query, conn)
      times.append(time.time() - start)

  avg_time = sum(times) / len(times)
  print(f"Average execution time: {avg_time:.2f}s")


rows examined vs returned

sqlite:
  SELECT COUNT(*) FROM table;  -- total rows

postgresql:
  EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM table;
  -- look for "rows=" vs "actual rows="

goal:
  - rows examined should be close to rows returned
  - large difference indicates missing index


PHASE 3: SELECT OPTIMIZATION


select only needed columns

bad:
  SELECT * FROM orders

good:
  SELECT id, customer_id, order_date, total_amount 
  FROM orders

why:
  - reduces i/o
  - reduces memory usage
  - reduces network transfer
  - enables index-only scans


avoid select distinct when possible

bad:
  SELECT DISTINCT customer_id FROM orders

better:
  SELECT customer_id FROM orders GROUP BY customer_id

best (if table has unique customer_id):
  SELECT id FROM customers WHERE EXISTS (
      SELECT 1 FROM orders WHERE customer_id = customers.id
  )


use limit for testing

before running on full dataset:
  SELECT * FROM large_table LIMIT 100

verify query logic and performance:
  SELECT * FROM expensive_query LIMIT 1000


avoid function calls in where clause

bad (prevents index usage):
  WHERE UPPER(name) = 'JOHN'
  WHERE DATE(created_at) = '2024-01-15'
  WHERE YEAR(created_at) = 2024

good:
  WHERE name = 'JOHN' COLLATE NOCASE  -- sqlite
  WHERE created_at >= '2024-01-15' AND created_at < '2024-01-16'
  WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'


PHASE 4: WHERE CLAUSE OPTIMIZATION


filter early, filter often

bad:
  SELECT * FROM orders, customers 
  WHERE orders.customer_id = customers.id
  AND customers.active = 1

good:
  SELECT o.* 
  FROM orders o
  INNER JOIN customers c ON o.customer_id = c.id
  WHERE c.active = 1


use indexed columns first

in where clause, put indexed columns first:
  WHERE indexed_column = value AND non_indexed = value


use exists instead of in for subqueries

bad (may scan entire table):
  SELECT * FROM orders 
  WHERE customer_id IN (SELECT id FROM customers WHERE active = 1)

good (stops at first match):
  SELECT * FROM orders o
  WHERE EXISTS (
      SELECT 1 FROM customers c 
      WHERE c.id = o.customer_id AND c.active = 1
  )


use between for ranges

bad:
  WHERE date >= '2024-01-01' AND date <= '2024-12-31'

good:
  WHERE date BETWEEN '2024-01-01' AND '2024-12-31'

note: between is inclusive on both ends


PHASE 5: JOIN OPTIMIZATION


choose correct join type

inner join:
  - only matching rows
  - fastest when most rows match
  - use when you only want related data

left join:
  - all rows from left table
  - slower, may cause null handling
  - use when you need unmatched rows

cross join:
  - cartesian product
  - very slow, rarely needed
  - avoid unless specifically required


join order matters

join smaller tables first:
  SELECT *
  FROM small_table s
  JOIN medium_table m ON s.id = m.small_id
  JOIN large_table l ON m.id = l.medium_id

filter before joining:
  SELECT *
  FROM orders o
  INNER JOIN (
      SELECT id, name FROM customers WHERE active = 1
  ) c ON o.customer_id = c.id


use alias for readability

good:
  SELECT o.id, o.total, c.name
  FROM orders o
  INNER JOIN customers c ON o.customer_id = c.id


avoid joining on nullable columns

nullable columns in joins:
  - prevent index usage
  - slow down queries
  - may produce unexpected nulls

use not null columns when possible.


PHASE 6: INDEX OPTIMIZATION


create indexes on query columns

columns in where clauses:
  CREATE INDEX idx_orders_customer_id ON orders(customer_id);

columns in joins:
  CREATE INDEX idx_orders_customer_id ON orders(customer_id);
  CREATE INDEX idx_customers_id ON customers(id);

columns in order by:
  CREATE INDEX idx_orders_date ON orders(order_date);


composite indexes for multiple columns

order matters in composite indexes:
  WHERE customer_id = 1 AND order_date > '2024-01-01'

create:
  CREATE INDEX idx_orders_customer_date ON orders(customer_id, order_date);

note: order in where clause should match index order


cover indexes for common queries

avoid table access entirely:
  CREATE INDEX idx_orders_cover ON orders(customer_id, order_date, total_amount);

query uses only indexed columns:
  SELECT order_date, total_amount 
  FROM orders 
  WHERE customer_id = 123;

this is an "index-only scan" - very fast.


check index usage

sqlite:
  EXPLAIN QUERY PLAN SELECT * FROM orders WHERE customer_id = 1;
  -- look for "USING INDEX"

postgresql:
  EXPLAIN ANALYZE SELECT * FROM orders WHERE customer_id = 1;
  -- look for "Index Scan" vs "Seq Scan"


remove unused indexes

indexes slow down inserts/updates.
remove indexes that are never used.

postgresql find unused indexes:
  SELECT schemaname, tablename, indexname, idx_scan
  FROM pg_stat_user_indexes
  WHERE idx_scan = 0;


PHASE 7: AGGREGATION OPTIMIZATION


use group by with indexes

query:
  SELECT customer_id, COUNT(*), SUM(total_amount)
  FROM orders
  GROUP BY customer_id;

index should be on grouping column:
  CREATE INDEX idx_orders_customer_id ON orders(customer_id);


use having for filtering aggregates

bad:
  SELECT customer_id, COUNT(*) as order_count
  FROM orders
  GROUP BY customer_id
  WHERE order_count > 10;  -- error: can't use alias in where

good:
  SELECT customer_id, COUNT(*) as order_count
  FROM orders
  GROUP BY customer_id
  HAVING COUNT(*) > 10;


pre-aggregate in subqueries

for complex aggregations:
  SELECT 
    c.name,
    o.order_count,
    o.total_amount
  FROM customers c
  INNER JOIN (
      SELECT customer_id, 
             COUNT(*) as order_count,
             SUM(total_amount) as total_amount
      FROM orders
      GROUP BY customer_id
      HAVING COUNT(*) > 10
  ) o ON c.id = o.customer_id;


window functions vs subqueries

use window functions when possible:
  SELECT 
    id,
    customer_id,
    total_amount,
    SUM(total_amount) OVER (
        PARTITION BY customer_id 
        ORDER BY order_date
    ) as running_total
  FROM orders;

better than correlated subqueries.


PHASE 8: SUBQUERY OPTIMIZATION


use exists instead of in

bad:
  SELECT * FROM orders o
  WHERE o.customer_id IN (SELECT id FROM customers WHERE active = 1)

good:
  SELECT * FROM orders o
  WHERE EXISTS (
      SELECT 1 FROM customers c 
      WHERE c.id = o.customer_id AND c.active = 1
  )


use lateral joins (postgresql) instead of correlated subqueries

bad:
  SELECT 
    c.id,
    c.name,
    (SELECT MAX(total_amount) 
     FROM orders o 
     WHERE o.customer_id = c.id) as max_order
  FROM customers c

good:
  SELECT 
    c.id,
    c.name,
    o.max_order
  FROM customers c
  LEFT JOIN LATERAL (
      SELECT MAX(total_amount) as max_order
      FROM orders o
      WHERE o.customer_id = c.id
  ) o ON true


materialize common subqueries

if subquery used multiple times:
  WITH customer_totals AS (
      SELECT 
        customer_id,
        COUNT(*) as order_count,
        SUM(total_amount) as total_amount
      FROM orders
      GROUP BY customer_id
  )
  SELECT c.name, ct.order_count, ct.total_amount
  FROM customers c
  INNER JOIN customer_totals ct ON c.id = ct.customer_id
  WHERE ct.order_count > 10;


PHASE 9: UNION OPTIMIZATION


use union all instead of union

union removes duplicates (expensive):
  SELECT name FROM employees_a
  UNION
  SELECT name FROM employees_b;

union all is faster (no duplicate removal):
  SELECT name FROM employees_a
  UNION ALL
  SELECT name FROM employees_b;

use union all if you know data doesn't overlap or duplicates are acceptable.


avoid union when possible

if unions are from same table, use or:
  SELECT * FROM orders
  WHERE customer_id = 1 OR customer_id = 2;

instead of:
  SELECT * FROM orders WHERE customer_id = 1
  UNION
  SELECT * FROM orders WHERE customer_id = 2;


PHASE 10: PAGINATION OPTIMIZATION


avoid offset for large offsets

bad (slow for large offsets):
  SELECT * FROM orders 
  ORDER BY order_date DESC 
  LIMIT 20 OFFSET 1000;

good (keyset pagination):
  SELECT * FROM orders 
  WHERE order_date < '2024-01-01'  -- last seen date
  ORDER BY order_date DESC 
  LIMIT 20;

use last row's values for next page.


cursor-based pagination

store last id and use for next page:
  -- page 1
  SELECT * FROM orders 
  WHERE id > 0
  ORDER BY id 
  LIMIT 20;

  -- page 2 (last_id from page 1)
  SELECT * FROM orders 
  WHERE id > 12345
  ORDER BY id 
  LIMIT 20;


PHASE 11: DATA TYPE OPTIMIZATION


use appropriate data types

sqlite types:
  - INTEGER: numbers, primary keys
  - REAL: floating point
  - TEXT: strings
  - BLOB: binary data

postgresql types:
  - SMALLINT: 2-byte integer
  - INTEGER: 4-byte integer
  - BIGINT: 8-byte integer
  - DECIMAL/NUMERIC: precise decimal
  - VARCHAR(n): variable-length string
  - TEXT: unlimited string
  - TIMESTAMP: date/time
  - JSON: json data


use smallest sufficient type

bad (overkill):
  BIGINT for ids under 1 million
  VARCHAR(255) for 10-character codes

good:
  INTEGER for ids up to 2 billion
  VARCHAR(10) for 10-character codes


PHASE 12: QUERY CACHING


use parameterized queries

python with sqlite:
  import sqlite3

  conn = sqlite3.connect('database.db')
  cursor = conn.cursor()

  # parameterized query (safe, cacheable)
  cursor.execute(
      "SELECT * FROM orders WHERE customer_id = ?",
      (customer_id,)
  )

python with sqlalchemy:
  from sqlalchemy import create_engine, text

  engine = create_engine('sqlite:///database.db')

  with engine.connect() as conn:
      result = conn.execute(
          text("SELECT * FROM orders WHERE customer_id = :cust_id"),
          {"cust_id": customer_id}
      )


prepare statements for repeated queries

postgresql:
  PREPARE get_orders (INT) AS
      SELECT * FROM orders WHERE customer_id = $1;

  EXECUTE get_orders(123);
  EXECUTE get_orders(456);
  DEALLOCATE get_orders;


PHASE 13: DATABASE-SPECIFIC OPTIMIZATION


sqlite specific

enable wal mode for concurrent access:
  PRAGMA journal_mode = WAL;

increase cache size:
  PRAGMA cache_size = -10000;  -- 10MB pages

optimize for specific queries:
  PRAGMA synchronous = NORMAL;  -- less durability, more speed


postgresql specific

use vacuum analyze after large changes:
  VACUUM ANALYZE orders;

use parallel query for large scans:
  SET max_parallel_workers_per_gather = 4;
  SELECT * FROM large_table;

use partitioning for large tables:
  CREATE TABLE orders (
      id SERIAL,
      order_date DATE,
      ...
  ) PARTITION BY RANGE (order_date);


mysql specific

use explain analyze for detailed plans:
  EXPLAIN ANALYZE SELECT * FROM orders;

optimize join buffer size:
  SET join_buffer_size = 4194304;

use query cache (if enabled):
  SELECT SQL_CACHE * FROM orders WHERE id = 1;


PHASE 14: COMMON ANTI-PATTERNS


anti-pattern: select *

problem:
  - returns unnecessary columns
  - prevents index-only scans
  - increases i/o and memory

solution:
  SELECT id, name, email FROM users;


anti-pattern: function in where clause

problem:
  WHERE UPPER(name) = 'JOHN'
  WHERE DATE(created_at) = '2024-01-01'

solution:
  WHERE name = 'JOHN' COLLATE NOCASE
  WHERE created_at >= '2024-01-01' AND created_at < '2024-01-02'


anti-pattern: order by on non-indexed column

problem:
  SELECT * FROM large_table ORDER BY name
  - requires full scan + sort
  - slow on large datasets

solution:
  CREATE INDEX idx_name ON large_table(name);
  SELECT * FROM large_table ORDER BY name


anti-pattern: excessive joins

problem:
  - joining 10+ tables
  - complex join conditions
  - performance degrades exponentially

solution:
  - break into multiple queries
  - use temporary tables
  - use materialized views


anti-pattern: n+1 queries

problem:
  for each customer:
      SELECT * FROM orders WHERE customer_id = ?

solution:
  SELECT * FROM orders WHERE customer_id IN (1, 2, 3, ...)


PHASE 15: SQL OPTIMIZATION CHECKLIST


before writing query

  [ ] do you understand the data structure?
  [ ] do you know table sizes?
  [ ] do you know existing indexes?
  [ ] do you know query frequency?


writing the query

  [ ] select only needed columns
  [ ] filter early in where clause
  [ ] use indexed columns in where/join/order by
  [ ] use appropriate join types
  [ ] avoid functions on indexed columns
  [ ] use exists instead of in for subqueries


after writing query

  [ ] run explain plan
  [ ] check for full table scans
  [ ] check index usage
  [ ] measure execution time
  [ ] test with sample data


optimization

  [ ] create missing indexes
  [ ] consider composite indexes
  [ ] consider cover indexes
  [ ] rewrite subqueries as joins
  [ ] use window functions if appropriate


verification

  [ ] re-run explain plan
  [ ] compare execution times
  [ ] test with realistic data volume
  [ ] verify results are correct


PHASE 16: MANDATORY RULES


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS RUN EXPLAIN PLAN before optimization
      never optimize without understanding current plan
      measure before and after

  [2] NEVER USE SELECT * IN PRODUCTION QUERIES
      always specify columns
      prevents unnecessary data transfer

  [3] ALWAYS FILTER EARLY
      apply where clauses as early as possible
      reduce working set size

  [4] ALWAYS USE INDEXED COLUMNS IN WHERE/JOIN/ORDER BY
      verify indexes exist
      create if missing

  [5] NEVER USE FUNCTIONS ON INDEXED COLUMNS IN WHERE CLAUSE
      this prevents index usage
      rewrite to use sargable expressions

  [6] ALWAYS USE PARAMETERIZED QUERIES
      prevents sql injection
      enables query caching

  [7] ALWAYS MEASURE EXECUTION TIME
      optimize based on measurements
      not guesses

  [8] NEVER IGNORE FULL TABLE SCANS
      investigate why index not used
      fix the problem

  [9] ALWAYS TEST WITH REALISTIC DATA VOLUMES
      performance on 100 rows differs from 10 million rows
      use production-like data

  [10] ALWAYS VERIFY CORRECTNESS AFTER OPTIMIZATION
      faster but wrong = useless
      ensure results are identical


FINAL REMINDERS


optimization is iterative

start with working query.
measure performance.
apply one optimization.
measure again.
repeat until acceptable.


data characteristics matter

optimizations that work for one dataset
may not work for another.
understand your data distribution.


readability matters

optimized but unreadable code
is hard to maintain.
balance performance with clarity.


document decisions

why did you add this index?
why did you rewrite this query?
future developers need to know.


now optimize those queries.
