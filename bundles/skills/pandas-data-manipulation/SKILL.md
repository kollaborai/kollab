---
name: pandas-data-manipulation
description: "Pandas Data Manipulation skill - master DataFrame operations and transformations"
---

pandas manipulation mode: DATA TRANSFORMATION MASTERY

when this skill is active, you follow pandas best practices for
efficient, readable, and performant data manipulation.


PHASE 0: PANDAS ENVIRONMENT VERIFICATION

before attempting ANY pandas operations, verify your tools are ready.


check pandas installation and version

  <terminal>python -c "import pandas; print(f'pandas version: {pandas.__version__}')"</terminal>

if pandas not available:
  <terminal>pip install pandas</terminal>

verify recommended pandas version (>= 2.0.0):
  <terminal>python -c "import pandas; import sys; print('ok' if pandas.__version__ >= '2.0.0' else 'upgrade needed')"</terminal>


check numpy dependency

  <terminal>python -c "import numpy; print(f'numpy version: {numpy.__version__}')"</terminal>

if numpy not available:
  <terminal>pip install numpy</terminal>


check memory availability for large datasets

  <terminal>python -c "import psutil; mem = psutil.virtual_memory(); print(f'total: {mem.total/1024**3:.1f}GB, available: {mem.available/1024**3:.1f}GB')"</terminal>

if psutil not installed:
  <terminal>pip install psutil</terminal>


verify pandas display settings

  <terminal>python -c "import pandas as pd; pd.set_option('display.max_rows', 10); pd.set_option('display.max_columns', 10); print('display settings configured')"</terminal>


check for sample data files

  <terminal>find . -maxdepth 2 -name "*.csv" -o -name "*.parquet" -o -name "*.xlsx" | head -10</terminal>

  <terminal>ls -lh data/ 2>/dev/null || ls -lh *.csv 2>/dev/null || echo "no data files found"</terminal>


PHASE 1: DATA LOADING FUNDAMENTALS


reading csv files efficiently

basic csv loading:
  import pandas as pd

  df = pd.read_csv('data.csv')

specify dtypes for efficiency:
  df = pd.read_csv('data.csv',
                   dtype={
                       'id': 'int32',
                       'category': 'category',
                       'value': 'float64'
                   })

handle date columns:
  df = pd.read_csv('data.csv',
                   parse_dates=['date_column', 'timestamp'])

specify columns to read:
  df = pd.read_csv('data.csv',
                   usecols=['id', 'name', 'value'])

skip rows:
  df = pd.read_csv('data.csv', skiprows=5)

limit rows:
  df = pd.read_csv('data.csv', nrows=10000)


reading large files in chunks

for files too large for memory:
  chunksize = 10000
  chunks = []
  for chunk in pd.read_csv('large_file.csv', chunksize=chunksize):
      # process each chunk
      processed = process_chunk(chunk)
      chunks.append(processed)

  df = pd.concat(chunks, ignore_index=True)

or process without storing all:
  for chunk in pd.read_csv('huge_file.csv', chunksize=10000):
      result = chunk.groupby('category').sum()
      result.to_csv('output.csv', mode='a', header=False)


reading from different sources

read excel:
  df = pd.read_excel('data.xlsx', sheet_name='Sheet1')

read parquet (recommended for large datasets):
  df = pd.read_parquet('data.parquet')

read json:
  df = pd.read_json('data.json')

read sql database:
  import sqlite3

  conn = sqlite3.connect('database.db')
  df = pd.read_sql_query("SELECT * FROM table", conn)

read html tables:
  tables = pd.read_html('https://example.com/data')
  df = tables[0]


PHASE 2: DATA INSPECTION AND EXPLORATION


basic dataframe inspection

get dataframe info:
  df.info()

summary statistics:
  df.describe()

first few rows:
  df.head()
  df.head(10)

last few rows:
  df.tail()

random sample:
  df.sample(n=5)
  df.sample(frac=0.1)  # 10% of data


checking data types

view dtypes:
  df.dtypes

count dtypes:
  df.dtypes.value_counts()

convert dtype:
  df['column'] = df['column'].astype('float64')

convert to datetime:
  df['date'] = pd.to_datetime(df['date'])

convert to category:
  df['category_column'] = df['category_column'].astype('category')


checking for missing values

count missing values per column:
  df.isnull().sum()

percentage missing:
  df.isnull().sum() / len(df) * 100

total missing:
  df.isnull().sum().sum()

boolean mask of missing:
  missing_mask = df.isnull()

rows with any missing:
  rows_with_missing = df[df.isnull().any(axis=1)]


checking for duplicates

duplicate rows:
  df.duplicated()

count duplicates:
  df.duplicated().sum()

specific column duplicates:
  df.duplicated(subset=['id'])

first occurrence is not duplicate:
  df.duplicated(keep='first')

last occurrence is not duplicate:
  df.duplicated(keep='last')

mark all duplicates:
  df.duplicated(keep=False)


unique values and counts

unique values:
  df['column'].unique()

count unique:
  df['column'].nunique()

value counts:
  df['column'].value_counts()

value counts as percentage:
  df['column'].value_counts(normalize=True)

value counts with missing:
  df['column'].value_counts(dropna=False)


PHASE 3: DATA SELECTION AND FILTERING


selecting columns

single column:
  df['column_name']

multiple columns:
  df[['col1', 'col2', 'col3']]

column by position:
  df.iloc[:, 0]  # first column

columns by position range:
  df.iloc[:, 0:3]

columns by name range:
  df.loc[:, 'col1':'col3']

columns by condition:
  df.select_dtypes(include=['number'])
  df.select_dtypes(include=['object', 'category'])


filtering rows

by value:
  df[df['column'] == 'value']

by multiple values:
  df[df['column'].isin(['value1', 'value2'])]

by range:
  df[(df['column'] >= 10) & (df['column'] <= 20)]

by string contains:
  df[df['column'].str.contains('pattern')]

by string startswith:
  df[df['column'].str.startswith('prefix')]

by date range:
  df[(df['date'] >= '2024-01-01') & (df['date'] <= '2024-12-31')]

complex boolean logic:
  df[
      (df['category'] == 'A') |
      ((df['value'] > 100) & (df['status'] == 'active'))
  ]


query method

string queries:
  df.query('column > 100 and category == "A"')

with variables:
  threshold = 100
  df.query('value > @threshold')

with column names with spaces:
  df.query('`column with spaces` > 100')


positional indexing with iloc

single cell:
  df.iloc[0, 0]  # row 0, column 0

single row:
  df.iloc[0]  # first row

multiple rows:
  df.iloc[0:5]  # rows 0-4

rows and columns:
  df.iloc[0:5, 2:5]  # rows 0-4, columns 2-4

specific rows and columns:
  df.iloc[[0, 2, 4], [1, 3]]


label-based indexing with loc

single cell:
  df.loc[0, 'column']

single row:
  df.loc[0]

multiple rows:
  df.loc[0:5]

rows and columns:
  df.loc[0:5, 'col1':'col3']

boolean indexing:
  df.loc[df['value'] > 100, 'column']


PHASE 4: DATA CLEANING AND HANDLING MISSING VALUES


handling missing values

drop rows with any missing:
  df_clean = df.dropna()

drop rows where specific columns are missing:
  df_clean = df.dropna(subset=['column1', 'column2'])

drop columns with missing:
  df_clean = df.dropna(axis=1)

drop rows with all missing:
  df_clean = df.dropna(how='all')

threshold for dropping:
  df_clean = df.dropna(thresh=2)  # require at least 2 non-NA values


filling missing values

fill with constant:
  df['column'] = df['column'].fillna(0)

fill with mean:
  df['column'] = df['column'].fillna(df['column'].mean())

fill with median:
  df['column'] = df['column'].fillna(df['column'].median())

fill with mode:
  df['column'] = df['column'].fillna(df['column'].mode()[0])

forward fill:
  df['column'] = df['column'].fillna(method='ffill')

backward fill:
  df['column'] = df['column'].fillna(method='bfill')

interpolate:
  df['column'] = df['column'].interpolate()

fill with different values per column:
  df.fillna({'column1': 0, 'column2': 'unknown'})


removing duplicates

drop all duplicates:
  df_clean = df.drop_duplicates()

drop duplicates keeping first:
  df_clean = df.drop_duplicates(keep='first')

drop duplicates keeping last:
  df_clean = df.drop_duplicates(keep='last')

drop all duplicate rows:
  df_clean = df.drop_duplicates(keep=False)

drop duplicates on subset:
  df_clean = df.drop_duplicates(subset=['id'])


handling outliers

iqr method:
  Q1 = df['column'].quantile(0.25)
  Q3 = df['column'].quantile(0.75)
  IQR = Q3 - Q1
  lower_bound = Q1 - 1.5 * IQR
  upper_bound = Q3 + 1.5 * IQR

  df_clean = df[
      (df['column'] >= lower_bound) &
      (df['column'] <= upper_bound)
  ]

z-score method:
  from scipy import stats

  z_scores = stats.zscore(df['column'])
  df_clean = df[abs(z_scores) < 3]

cap outliers:
  df['column'] = df['column'].clip(lower_bound, upper_bound)


PHASE 5: DATA TRANSFORMATION


string operations

convert to uppercase:
  df['column'] = df['column'].str.upper()

convert to lowercase:
  df['column'] = df['column'].str.lower()

strip whitespace:
  df['column'] = df['column'].str.strip()

replace substrings:
  df['column'] = df['column'].str.replace('old', 'new')

extract with regex:
  df['extracted'] = df['column'].str.extract(r'pattern')

split strings:
  df[['first', 'last']] = df['name'].str.split(' ', expand=True)

concatenate strings:
  df['full_name'] = df['first'] + ' ' + df['last']


numeric operations

arithmetic:
  df['total'] = df['price'] * df['quantity']

absolute value:
  df['absolute'] = df['column'].abs()

round:
  df['rounded'] = df['column'].round(2)

floor:
  df['floor'] = df['column'].apply(np.floor)

ceiling:
  df['ceiling'] = df['column'].apply(np.ceil)

binning:
  df['bin'] = pd.cut(df['value'], bins=[0, 10, 20, 30, 40, 50])

percentile rank:
  df['percentile'] = df['value'].rank(pct=True)


datetime operations

extract components:
  df['year'] = df['date'].dt.year
  df['month'] = df['date'].dt.month
  df['day'] = df['date'].dt.day
  df['hour'] = df['date'].dt.hour
  df['dayofweek'] = df['date'].dt.dayofweek
  df['weekday_name'] = df['date'].dt.strftime('%A')

calculate difference:
  df['days_diff'] = (df['end_date'] - df['start_date']).dt.days

add time:
  df['future_date'] = df['date'] + pd.Timedelta(days=7)

resample time series:
  df.set_index('date').resample('D').mean()


categorical operations

encode categories:
  df['category_encoded'] = df['category'].cat.codes

get categories:
  df['category'].cat.categories

rename categories:
  df['category'] = df['category'].cat.rename_categories({
      'A': 'Alpha',
      'B': 'Beta'
  })

reorder categories:
  df['category'] = df['category'].cat.reorder_categories(
      ['Low', 'Medium', 'High'],
      ordered=True
  )


PHASE 6: DATA AGGREGATION AND GROUPING


groupby basics

single column groupby:
  df.groupby('category')['value'].sum()

multiple columns groupby:
  df.groupby(['category', 'subcategory'])['value'].mean()

multiple aggregations:
  df.groupby('category')['value'].agg(['mean', 'std', 'count'])

different aggregations per column:
  df.groupby('category').agg({
      'value': 'mean',
      'count': 'sum',
      'price': 'max'
  })

named aggregations:
  df.groupby('category').agg(
      mean_value=('value', 'mean'),
      std_value=('value', 'std'),
      total_count=('id', 'count')
  )


groupby transformations

transform:
  df['value_zscore'] = df.groupby('category')['value'].transform(
      lambda x: (x - x.mean()) / x.std()
  )

fill missing with group mean:
  df['value'] = df.groupby('category')['value'].transform(
      lambda x: x.fillna(x.mean())
  )

group rank:
  df['rank_in_group'] = df.groupby('category')['value'].rank()


groupby filtering

filter groups:
  df.groupby('category').filter(lambda x: len(x) > 10)

filter by aggregate:
  df.groupby('category').filter(
      lambda x: x['value'].mean() > 100
  )


pivot tables

basic pivot:
  pd.pivot_table(
      df,
      values='value',
      index='category',
      columns='month',
      aggfunc='mean'
  )

multiple aggregations:
  pd.pivot_table(
      df,
      values='value',
      index='category',
      columns='month',
      aggfunc=['mean', 'sum']
  )

fill missing values:
  pd.pivot_table(
      df,
      values='value',
      index='category',
      columns='month',
      aggfunc='sum',
      fill_value=0
  )

with margins:
  pd.pivot_table(
      df,
      values='value',
      index='category',
      columns='month',
      aggfunc='sum',
      margins=True
  )


cross tabulation

basic crosstab:
  pd.crosstab(df['category'], df['status'])

with counts:
  pd.crosstab(df['category'], df['status'], margins=True)

with normalization:
  pd.crosstab(
      df['category'],
      df['status'],
      normalize='index'
  )


PHASE 7: MERGING AND JOINING DATA


basic merge

inner join:
  pd.merge(df1, df2, on='id')

left join:
  pd.merge(df1, df2, on='id', how='left')

right join:
  pd.merge(df1, df2, on='id', how='right')

outer join:
  pd.merge(df1, df2, on='id', how='outer')


merge on different column names

  pd.merge(df1, df2, left_on='id1', right_on='id2')


merge on multiple columns

  pd.merge(df1, df2, on=['id', 'date'])


merge with suffixes

  pd.merge(
      df1,
      df2,
      on='id',
      suffixes=('_left', '_right')
  )


merge with indicator

  merged = pd.merge(df1, df2, on='id', how='outer', indicator=True)

filter for unmatched:
  merged[merged['_merge'] == 'left_only']


concatenating dataframes

vertical concat:
  pd.concat([df1, df2, df3], ignore_index=True)

horizontal concat:
  pd.concat([df1, df2], axis=1)

concat with keys:
  pd.concat([df1, df2], keys=['source1', 'source2'])


join on index

  df1.join(df2, on='id')

inner join on index:
  df1.join(df2, how='inner')


PHASE 8: RESHAPING DATA


melting data

wide to long:
  pd.melt(
      df,
      id_vars=['id', 'name'],
      value_vars=['q1', 'q2', 'q3'],
      var_name='quarter',
      value_name='sales'
  )


stacking and unstacking

stack columns to index:
  df.stack()

unstack index to columns:
  df.unstack()


pivoting

long to wide:
  df.pivot(
      index='id',
      columns='date',
      values='value'
  )


multiindex operations

create multiindex:
  df.set_index(['category', 'subcategory'])

select from multiindex:
  df.loc['category_A']

swap levels:
  df.swaplevel()

reset index:
  df.reset_index()


PHASE 9: APPLYING FUNCTIONS


apply to series

simple function:
  df['new_column'] = df['column'].apply(lambda x: x * 2)

with condition:
  df['new_column'] = df['column'].apply(
      lambda x: 'high' if x > 100 else 'low'
  )


apply to dataframe

row-wise:
  df['result'] = df.apply(
      lambda row: row['a'] + row['b'],
      axis=1
  )

column-wise:
  df.apply(lambda col: col.max() - col.min())


vectorized operations

prefer vectorized over apply:
  df['new_column'] = df['column'] * 2

much faster than:
  df['new_column'] = df['column'].apply(lambda x: x * 2)


map values

simple mapping:
  df['category'] = df['category'].map({
      'A': 'Alpha',
      'B': 'Beta'
  })


PHASE 10: PERFORMANCE OPTIMIZATION


use efficient dtypes

convert to category:
  df['column'] = df['column'].astype('category')

use int32 instead of int64:
  df['column'] = df['column'].astype('int32')

use float32 instead of float64:
  df['column'] = df['column'].astype('float32')


use vectorized operations

bad:
  for i in range(len(df)):
      df.loc[i, 'new_col'] = df.loc[i, 'col'] * 2

good:
  df['new_col'] = df['col'] * 2


use categorical for repeated strings

memory savings:
  df['category'] = df['category'].astype('category')


use query for complex filtering

faster than boolean indexing:
  df.query('value > 100 and category == "A"')


use eval for complex expressions

  df.eval('new_column = a + b * c')


avoid chaining operations

bad:
  df[df['a'] > 10]['b'] = 5

good:
  df.loc[df['a'] > 10, 'b'] = 5


PHASE 11: ADVANCED OPERATIONS


rolling windows

simple rolling mean:
  df['rolling_mean'] = df['value'].rolling(window=5).mean()

rolling with multiple aggregations:
  df['rolling_std'] = df['value'].rolling(window=5).std()

rolling with min periods:
  df['rolling'] = df['value'].rolling(window=5, min_periods=1).mean()


expanding windows

cumulative sum:
  df['cumsum'] = df['value'].expanding().sum()

cumulative mean:
  df['cummean'] = df['value'].expanding().mean()


shift and lag

shift down:
  df['lag1'] = df['value'].shift(1)

shift up:
  df['lead1'] = df['value'].shift(-1)

percentage change:
  df['pct_change'] = df['value'].pct_change()


rank and quantiles

rank:
  df['rank'] = df['value'].rank()

quantile bins:
  df['quantile'] = pd.qcut(
      df['value'],
      q=4,
      labels=['Q1', 'Q2', 'Q3', 'Q4']
  )


duplicates handling

find duplicates:
  df.duplicated()

find first duplicates:
  df.duplicated(keep='first')

find all duplicates:
  df.duplicated(keep=False)


PHASE 12: WORKING WITH TIME SERIES


datetime basics

create datetime:
  pd.to_datetime('2024-01-01')

parse datetime column:
  df['date'] = pd.to_datetime(df['date'])

set datetime as index:
  df.set_index('date', inplace=True)


resampling

daily to monthly:
  df.resample('M').mean()

hourly to daily:
  df.resample('D').sum()

custom frequency:
  df.resample('2W').mean()  # 2 weeks


time-based filtering

  df.loc['2024-01':'2024-06']

  df[df.index.month == 1]

  df[df.index.dayofweek < 5]  # weekdays


rolling with time

  df.rolling('7D').mean()


PHASE 13: BEST PRACTICES CHECKLIST


before operations:

  [ ] verify data types are correct
  [ ] check for missing values
  [ ] understand data size and memory requirements
  [ ] sample data before full operations
  [ ] backup original data if important


during operations:

  [ ] use efficient dtypes (category, int32, float32)
  [ ] prefer vectorized operations over loops
  [ ] use query() for complex filtering
  [ ] avoid chained indexing
  [ ] use inplace=False by default


after operations:

  [ ] verify operation results
  [ ] check for unexpected missing values
  [ ] validate data integrity
  [ ] check memory usage
  [ ] document transformations


PHASE 14: COMMON PITFALLS TO AVOID


chained assignment

wrong:
  df[df['a'] > 10]['b'] = 5

correct:
  df.loc[df['a'] > 10, 'b'] = 5


modifying while iterating

wrong:
  for i, row in df.iterrows():
      df.loc[i, 'new'] = row['old'] * 2

correct:
  df['new'] = df['old'] * 2


ignoring copy vs view

  df_copy = df.copy()  # explicit copy


forgetting to reset_index after filtering

  df_filtered = df[condition].reset_index(drop=True)


mixing loc and iloc

  be consistent with either label-based or positional indexing


PHASE 15: MANDATORY RULES

while this skill is active, these rules are MANDATORY:

  [1] ALWAYS CHECK DATA TYPES before operations
      incorrect types cause unexpected results
      verify with df.dtypes before processing

  [2] NEVER USE LOOPS for DataFrame operations
      vectorized operations are 100-1000x faster
      always use df.apply() or built-in methods

  [3] ALWAYS HANDLE MISSING VALUES explicitly
      decide how to handle: drop, fill, or leave
      document your decision

  [4] NEVER CHAIN INDEXING without .loc or .iloc
      df[df.a > 10]['b'] = 5 is dangerous
      use df.loc[df.a > 10, 'b'] = 5

  [5] ALWAYS SAMPLE before full operations
      test on sample (df.sample(1000))
      verify correctness before full dataset

  [6] NEVER ASSUME INDEX is sorted
      sort before operations if needed
      df.sort_values('column')

  [7] ALWAYS USE EFFICIENT DTYPES for large datasets
      category for low-cardinality strings
      int32/float32 instead of 64-bit

  [8] NEVER FORGET inplace=False default
      most operations return new dataframe
      assign result: df = df.copy()

  [9] ALWAYS VERIFY OUTPUT SHAPE and content
      check df.shape after operations
      df.head() to spot issues

  [10] NEVER MIX loc and iloc in same operation
      pick one and stick with it
      be consistent in your code


FINAL REMINDERS


pandas is powerful

use vectorized operations.
your code will be faster.
and more readable.


think in operations, not rows

  not: for each row, do this
  but: apply this operation to all rows


the chainable API

  df.query('value > 100').groupby('category').agg({'value': 'mean'})

each step returns a dataframe.
chain them together.


when stuck

  [ ] read pandas documentation
  [ ] check stackoverflow
  [ ] use df.info() to understand structure
  [ ] print df.head() to see data

the goal

clean data.
transform efficiently.
get insights fast.

now go analyze that data.
