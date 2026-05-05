---
name: exploratory-data-analysis
description: "Exploratory Data Analysis skill - comprehensive data discovery and understanding"
---

exploratory data analysis: DISCOVER INSIGHTS THROUGH SYSTEMATIC EXPLORATION

when this skill is active, you follow rigorous EDA methodology.
this is a comprehensive guide to understanding your data before modeling.


PHASE 0: EDA ENVIRONMENT VERIFICATION

before starting ANY analysis, verify your data science stack is ready.


verify python data packages

  <terminal>python -c "import pandas as pd; print(f'pandas {pd.__version__} ready')"</terminal>

if pandas not available:
  <terminal>pip install pandas numpy scipy</terminal>

verify visualization:
  <terminal>python -c "import matplotlib.pyplot as plt; import seaborn as sns; print('viz packages ready')"</terminal>

if visualization not available:
  <terminal>pip install matplotlib seaborn plotly</terminal>


verify data file accessibility

list data files:
  <terminal>find . -maxdepth 2 -type f \( -name "*.csv" -o -name "*.json" -o -name "*.xlsx" -o -name "*.parquet" \)</terminal>

check file sizes:
  <terminal>find . -maxdepth 2 -type f \( -name "*.csv" -o -name "*.json" -o -name "*.xlsx" -o -name "*.parquet" \) -exec ls -lh {} \;</terminal>

verify readability:
  <terminal>python -c "import pandas as pd; df = pd.read_csv('data.csv'); print(f'shape: {df.shape}'); print(f'dtypes:\n{df.dtypes}')"</terminal>


check available memory

  <terminal>python -c "import psutil; mem = psutil.virtual_memory(); print(f'total: {mem.total / 1e9:.2f} GB'); print(f'available: {mem.available / 1e9:.2f} GB')"</terminal>

if memory is limited for large datasets:
  <terminal>pip install dask modin</terminal>


verify jupyter/lab notebooks (optional but recommended)

  <terminal>jupyter --version 2>/dev/null || echo "jupyter not installed"</terminal>

if jupyter not installed:
  <terminal>pip install jupyter jupyterlab</terminal>


PHASE 1: INITIAL DATA LOAD AND INSPECTION


load data with appropriate reader

csv files:
  <terminal>python -c "
import pandas as pd
df = pd.read_csv('data.csv', parse_dates=True, infer_datetime_format=True)
print(f'loaded: {df.shape[0]} rows, {df.shape[1]} columns')
"</terminal>

json files:
  <terminal>python -c "
import pandas as pd
df = pd.read_json('data.json')
print(f'loaded: {df.shape[0]} rows, {df.shape[1]} columns')
"</terminal>

excel files:
  <terminal>python -c "
import pandas as pd
df = pd.read_excel('data.xlsx', engine='openpyxl')
print(f'loaded: {df.shape[0]} rows, {df.shape[1]} columns')
"</terminal>

parquet files (for large data):
  <terminal>python -c "
import pandas as pd
df = pd.read_parquet('data.parquet')
print(f'loaded: {df.shape[0]} rows, {df.shape[1]} columns')
"</terminal>


handle large datasets with chunking

chunked reading:
  <terminal>python -c "
import pandas as pd

chunk_size = 10000
chunks = pd.read_csv('large_data.csv', chunksize=chunk_size)

total_rows = 0
for i, chunk in enumerate(chunks):
    total_rows += len(chunk)
    print(f'chunk {i}: {len(chunk)} rows')

print(f'total rows: {total_rows}')
"</terminal>

sample large dataset:
  <terminal>python -c "
import pandas as pd

# read first N rows
df = pd.read_csv('large_data.csv', nrows=100000)
print(f'sample: {df.shape}')
"</terminal>


basic data overview

shape and memory:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
print(f'shape: {df.shape}')
print(f'memory usage: {df.memory_usage(deep=True).sum() / 1e6:.2f} MB')
print(f'\ncolumns: {list(df.columns)}')
"</terminal>

data types:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
print('data types:')
print(df.dtypes)
print(f'\ntype distribution:')
print(df.dtypes.value_counts())
"</terminal>

sample data:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
print('first 5 rows:')
print(df.head())
print('\nlast 5 rows:')
print(df.tail())
print('\nrandom sample:')
print(df.sample(5))
"</terminal>


PHASE 2: DATA QUALITY ASSESSMENT


missing value analysis

overall missing data:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
missing = df.isnull().sum()
missing_pct = (missing / len(df)) * 100

print('missing values:')
print(pd.DataFrame({
    'count': missing,
    'percentage': missing_pct
}))
"</terminal>

missing data visualization:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
missing = df.isnull()

plt.figure(figsize=(12, 8))
sns.heatmap(missing, cbar=False, cmap='viridis')
plt.title('missing data heatmap')
plt.tight_layout()
plt.savefig('missing_data_heatmap.png')
print('saved: missing_data_heatmap.png')
"</terminal>

missing data patterns:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# check if missing values are related
print('missing correlation:')
print(df.isnull().corr())

# check row-level missing
df['missing_count'] = df.isnull().sum(axis=1)
print(f'\nrows with missing data: {(df[\"missing_count\"] > 0).sum()}')
print(f'missing per row stats:')
print(df['missing_count'].describe())
"</terminal>


duplicate detection

exact duplicates:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
duplicates = df.duplicated()

print(f'duplicate rows: {duplicates.sum()} ({duplicates.sum()/len(df)*100:.2f}%)')
print(f'\nunique rows: {len(df.drop_duplicates())}')
"</terminal>

subset duplicates:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# check duplicates on specific columns
subset_cols = ['id', 'name', 'date']
subset_dups = df.duplicated(subset=subset_cols)

print(f'duplicates on {subset_cols}: {subset_dups.sum()}')
"</terminal>

duplicate analysis:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# show duplicate examples
duplicates = df[df.duplicated(keep=False)]
print('duplicate examples:')
print(duplicates.sort_values(by=list(df.columns)).head(10))
"</terminal>


data type validation

type mismatch detection:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# check numeric columns with non-numeric values
for col in df.select_dtypes(include=['object']).columns:
    # try to convert to numeric
    try:
        numeric = pd.to_numeric(df[col], errors='coerce')
        non_numeric = numeric.isnull() & df[col].notnull()
        if non_numeric.any():
            print(f'{col}: {non_numeric.sum()} non-numeric values')
            print(f'  examples: {df.loc[non_numeric, col].head().tolist()}')
    except:
        pass
"</terminal>

datetime validation:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# try to parse object columns as datetime
for col in df.select_dtypes(include=['object']).columns:
    if 'date' in col.lower() or 'time' in col.lower():
        try:
            parsed = pd.to_datetime(df[col], errors='coerce')
            failed = parsed.isnull() & df[col].notnull()
            if failed.any():
                print(f'{col}: {failed.sum()} invalid datetime values')
                print(f'  examples: {df.loc[failed, col].head().tolist()}')
        except:
            pass
"</terminal>


outlier detection

statistical outliers (z-score):
  <terminal>python -c "
import pandas as pd
import numpy as np

df = pd.read_csv('data.csv')

numeric_cols = df.select_dtypes(include=[np.number]).columns

for col in numeric_cols:
    z_scores = np.abs((df[col] - df[col].mean()) / df[col].std())
    outliers = z_scores > 3
    if outliers.any():
        print(f'{col}: {outliers.sum()} outliers (z > 3)')
        print(f'  outlier values: {df.loc[outliers, col].describe()}')
"</terminal>

iqr outliers:
  <terminal>python -c "
import pandas as pd
import numpy as np

df = pd.read_csv('data.csv')

numeric_cols = df.select_dtypes(include=[np.number]).columns

for col in numeric_cols:
    q1 = df[col].quantile(0.25)
    q3 = df[col].quantile(0.75)
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    
    outliers = (df[col] < lower_bound) | (df[col] > upper_bound)
    if outliers.any():
        print(f'{col}: {outliers.sum()} outliers (iqr method)')
        print(f'  bounds: [{lower_bound:.2f}, {upper_bound:.2f}]')
"</terminal>

visual outlier detection:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

fig, axes = plt.subplots(len(numeric_cols), 1, figsize=(10, 5*len(numeric_cols)))
if len(numeric_cols) == 1:
    axes = [axes]

for i, col in enumerate(numeric_cols):
    df[col].plot(kind='box', ax=axes[i])
    axes[i].set_title(f'{col} boxplot')

plt.tight_layout()
plt.savefig('outlier_boxplots.png')
print('saved: outlier_boxplots.png')
"</terminal>


PHASE 3: UNIVARIATE ANALYSIS


numeric variable analysis

descriptive statistics:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

print('descriptive statistics:')
print(df[numeric_cols].describe().transpose())

print('\nskewness:')
print(df[numeric_cols].skew())

print('\nkurtosis:')
print(df[numeric_cols].kurtosis())
"</terminal>

distribution visualization:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

fig, axes = plt.subplots(len(numeric_cols), 2, figsize=(15, 5*len(numeric_cols)))
if len(numeric_cols) == 1:
    axes = axes.reshape(1, -1)

for i, col in enumerate(numeric_cols):
    # histogram
    df[col].hist(ax=axes[i, 0], bins=50)
    axes[i, 0].set_title(f'{col} distribution')
    axes[i, 0].set_xlabel(col)
    axes[i, 0].set_ylabel('frequency')
    
    # density plot
    df[col].plot(kind='kde', ax=axes[i, 1])
    axes[i, 1].set_title(f'{col} density')
    axes[i, 1].set_xlabel(col)
    axes[i, 1].set_ylabel('density')

plt.tight_layout()
plt.savefig('numeric_distributions.png')
print('saved: numeric_distributions.png')
"</terminal>

normality tests:
  <terminal>python -c "
import pandas as pd
from scipy import stats

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

for col in numeric_cols:
    data = df[col].dropna()
    
    # shapiro-wilk test (for small samples)
    if len(data) < 5000:
        stat, p = stats.shapiro(data)
        print(f'{col}: shapiro-wilk p={p:.4f} (normal={p > 0.05})')
    else:
        # kolmogorov-smirnov test (for large samples)
        stat, p = stats.kstest(data, 'norm')
        print(f'{col}: ks-test p={p:.4f} (normal={p > 0.05})')
"</terminal>


categorical variable analysis

value counts:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns

for col in categorical_cols:
    print(f'\n{col}:')
    print(f'  unique values: {df[col].nunique()}')
    print(f'  top 5 values:')
    print(df[col].value_counts().head())
"</terminal>

cardinality analysis:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns

print('cardinality analysis:')
cardinality = pd.DataFrame({
    'unique': df[categorical_cols].nunique(),
    'total': len(df),
    'ratio': df[categorical_cols].nunique() / len(df)
})
print(cardinality)

print('\nhigh cardinality columns (> 0.5):')
high_card = cardinality[cardinality['ratio'] > 0.5]
if not high_card.empty:
    print(high_card)
"</terminal>

categorical visualization:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns

# plot top categories for each column
for col in categorical_cols:
    if df[col].nunique() <= 20:
        plt.figure(figsize=(10, 6))
        df[col].value_counts().plot(kind='bar')
        plt.title(f'{col} distribution')
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(f'{col}_distribution.png')
        plt.close()
        print(f'saved: {col}_distribution.png')
"</terminal>


datetime variable analysis

date range:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')

# identify datetime columns
datetime_cols = []
for col in df.columns:
    try:
        df[col] = pd.to_datetime(df[col], errors='coerce')
        if df[col].notnull().any():
            datetime_cols.append(col)
    except:
        pass

for col in datetime_cols:
    print(f'\n{col}:')
    print(f'  range: {df[col].min()} to {df[col].max()}')
    print(f'  span: {(df[col].max() - df[col].min()).days} days')
    print(f'  missing: {df[col].isnull().sum()}')
"</terminal>

temporal patterns:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv')

# assume first datetime column
date_col = df.select_dtypes(include=['datetime64']).columns[0]

# extract time components
df['year'] = df[date_col].dt.year
df['month'] = df[date_col].dt.month
df['day'] = df[date_col].dt.day
df['weekday'] = df[date_col].dt.weekday
df['hour'] = df[date_col].dt.hour

fig, axes = plt.subplots(2, 2, figsize=(15, 10))

# yearly pattern
df['year'].value_counts().sort_index().plot(kind='bar', ax=axes[0,0])
axes[0,0].set_title('yearly pattern')

# monthly pattern
df['month'].value_counts().sort_index().plot(kind='bar', ax=axes[0,1])
axes[0,1].set_title('monthly pattern')

# weekday pattern
df['weekday'].value_counts().sort_index().plot(kind='bar', ax=axes[1,0])
axes[1,0].set_title('weekday pattern')

# hourly pattern
df['hour'].value_counts().sort_index().plot(kind='bar', ax=axes[1,1])
axes[1,1].set_title('hourly pattern')

plt.tight_layout()
plt.savefig('temporal_patterns.png')
print('saved: temporal_patterns.png')
"</terminal>


PHASE 4: BIVARIATE ANALYSIS


numeric-numeric relationships

correlation matrix:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

# compute correlation matrix
corr = df[numeric_cols].corr()

# plot heatmap
plt.figure(figsize=(12, 10))
sns.heatmap(corr, annot=True, fmt='.2f', cmap='coolwarm', center=0)
plt.title('correlation matrix')
plt.tight_layout()
plt.savefig('correlation_matrix.png')
print('saved: correlation_matrix.png')
"</terminal>

scatter plot matrix:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns[:5]  # limit to first 5

# pairplot
sns.pairplot(df[numeric_cols], diag_kind='kde')
plt.tight_layout()
plt.savefig('scatter_matrix.png')
print('saved: scatter_matrix.png')
"</terminal>

strong correlations:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns
corr = df[numeric_cols].corr()

# find strong correlations (|r| > 0.7)
strong_corr = []
for i in range(len(corr.columns)):
    for j in range(i+1, len(corr.columns)):
        if abs(corr.iloc[i, j]) > 0.7:
            strong_corr.append({
                'var1': corr.columns[i],
                'var2': corr.columns[j],
                'correlation': corr.iloc[i, j]
            })

if strong_corr:
    strong_corr_df = pd.DataFrame(strong_corr)
    strong_corr_df = strong_corr_df.sort_values('correlation', key=abs, ascending=False)
    print('strong correlations:')
    print(strong_corr_df)
else:
    print('no strong correlations found')
"</terminal>


categorical-categorical relationships

crosstab analysis:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns[:4]

for i in range(len(categorical_cols)):
    for j in range(i+1, len(categorical_cols)):
        col1, col2 = categorical_cols[i], categorical_cols[j]
        if df[col1].nunique() <= 10 and df[col2].nunique() <= 10:
            print(f'\n{col1} vs {col2}:')
            crosstab = pd.crosstab(df[col1], df[col2])
            print(crosstab)
"</terminal>

chi-square test:
  <terminal>python -c "
import pandas as pd
from scipy import stats

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns[:4]

for i in range(len(categorical_cols)):
    for j in range(i+1, len(categorical_cols)):
        col1, col2 = categorical_cols[i], categorical_cols[j]
        if df[col1].nunique() <= 10 and df[col2].nunique() <= 10:
            crosstab = pd.crosstab(df[col1], df[col2])
            chi2, p, dof, expected = stats.chi2_contingency(crosstab)
            print(f'{col1} vs {col2}: chi2={chi2:.2f}, p={p:.4f}, significant={p < 0.05}')
"</terminal>


numeric-categorical relationships

group statistics:
  <terminal>python -c "
import pandas as pd

df = pd.read_csv('data.csv')
categorical_cols = df.select_dtypes(include=['object', 'category']).columns[:3]
numeric_cols = df.select_dtypes(include=['number']).columns[:3]

for cat_col in categorical_cols:
    if df[cat_col].nunique() <= 10:
        print(f'\n{cat_col}:')
        for num_col in numeric_cols:
            group_stats = df.groupby(cat_col)[num_col].agg(['mean', 'std', 'count'])
            print(f'  {num_col}:')
            print(group_stats)
"</terminal>

boxplot by category:
  <terminal>python -c "
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('data.csv')
cat_col = df.select_dtypes(include=['object', 'category']).columns[0]
num_col = df.select_dtypes(include=['number']).columns[0]

if df[cat_col].nunique() <= 10:
    plt.figure(figsize=(12, 6))
    sns.boxplot(x=cat_col, y=num_col, data=df)
    plt.title(f'{num_col} by {cat_col}')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(f'{num_col}_by_{cat_col}.png')
    print(f'saved: {num_col}_by_{cat_col}.png')
"</terminal>

anova test:
  <terminal>python -c "
import pandas as pd
from scipy import stats

df = pd.read_csv('data.csv')
cat_col = df.select_dtypes(include=['object', 'category']).columns[0]
num_col = df.select_dtypes(include=['number']).columns[0]

if df[cat_col].nunique() <= 10:
    groups = [group[num_col].dropna() for name, group in df.groupby(cat_col)]
    f_stat, p_value = stats.f_oneway(*groups)
    print(f'anova test for {num_col} by {cat_col}:')
    print(f'  f-statistic: {f_stat:.4f}')
    print(f'  p-value: {p_value:.4f}')
    print(f'  significant: {p_value < 0.05}')
"</terminal>


PHASE 5: MULTIVARIATE ANALYSIS


dimensionality reduction visualization

pca scatter plot:
  <terminal>python -c "
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

# prepare data
data = df[numeric_cols].dropna()
scaler = StandardScaler()
scaled_data = scaler.fit_transform(data)

# perform pca
pca = PCA(n_components=2)
pca_result = pca.fit_transform(scaled_data)

# plot
plt.figure(figsize=(10, 8))
plt.scatter(pca_result[:, 0], pca_result[:, 1], alpha=0.5)
plt.xlabel('pc1 (explained variance: {:.2%})'.format(pca.explained_variance_ratio_[0]))
plt.ylabel('pc2 (explained variance: {:.2%})'.format(pca.explained_variance_ratio_[1]))
plt.title('pca scatter plot')
plt.savefig('pca_scatter.png')
print('saved: pca_scatter.png')
print(f'total variance explained: {pca.explained_variance_ratio_.sum():.2%}')
"</terminal>

pca loading analysis:
  <terminal>python -c "
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

df = pd.read_csv('data.csv')
numeric_cols = df.select_dtypes(include=['number']).columns

# prepare data
data = df[numeric_cols].dropna()
scaler = StandardScaler()
scaled_data = scaler.fit_transform(data)

# perform pca
pca = PCA()
pca_result = pca.fit_transform(scaled_data)

# loading matrix
loadings = pd.DataFrame(
    pca.components_.T,
    columns=[f'pc{i+1}' for i in range(len(numeric_cols))],
    index=numeric_cols
)

print('pca loadings (top 3 components):')
print(loadings.iloc[:, :3])

print('\nexplained variance ratio:')
print(pca.explained_variance_ratio_[:10])
"</terminal>


feature importance analysis

random forest feature importance:
  <terminal>python -c "
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv')

# prepare features
numeric_cols = df.select_dtypes(include=['number']).columns
categorical_cols = df.select_dtypes(include=['object', 'category']).columns

# encode categorical variables
for col in categorical_cols:
    df[col] = LabelEncoder().fit_transform(df[col].astype(str))

# use all columns as features
features = df.dropna()
X = features
y = features[numeric_cols[0]]  # use first numeric as target

# train random forest
rf = RandomForestRegressor(n_estimators=100, random_state=42)
rf.fit(X, y)

# feature importance
importance = pd.DataFrame({
    'feature': X.columns,
    'importance': rf.feature_importances_
}).sort_values('importance', ascending=False)

print('feature importance:')
print(importance.head(10))

# plot
plt.figure(figsize=(10, 6))
plt.barh(importance['feature'].head(10), importance['importance'].head(10))
plt.gca().invert_yaxis()
plt.title('feature importance (random forest)')
plt.tight_layout()
plt.savefig('feature_importance.png')
print('saved: feature_importance.png')
"</terminal>

mutual information:
  <terminal>python -c "
import pandas as pd
from sklearn.feature_selection import mutual_info_regression
from sklearn.preprocessing import LabelEncoder
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv')

# prepare data
numeric_cols = df.select_dtypes(include=['number']).columns
categorical_cols = df.select_dtypes(include=['object', 'category']).columns

# encode categorical variables
for col in categorical_cols:
    df[col] = LabelEncoder().fit_transform(df[col].astype(str))

# prepare features and target
features = df.dropna()
X = features[features.columns.difference([numeric_cols[0]])]
y = features[numeric_cols[0]]

# calculate mutual information
mi = mutual_info_regression(X, y)

# create dataframe
mi_df = pd.DataFrame({
    'feature': X.columns,
    'mutual_info': mi
}).sort_values('mutual_info', ascending=False)

print('mutual information:')
print(mi_df.head(10))

# plot
plt.figure(figsize=(10, 6))
plt.barh(mi_df['feature'].head(10), mi_df['mutual_info'].head(10))
plt.gca().invert_yaxis()
plt.title('mutual information')
plt.tight_layout()
plt.savefig('mutual_information.png')
print('saved: mutual_information.png')
"</terminal>


PHASE 6: DATA QUALITY REPORT


generate comprehensive report

  <terminal>python -c "
import pandas as pd
import numpy as np
from datetime import datetime

# load data
df = pd.read_csv('data.csv')

# initialize report
report = []
report.append('=' * 80)
report.append('exploratory data analysis report')
report.append('=' * 80)
report.append(f'generated: {datetime.now().strftime(\"%y-%m-%d %h:%m:%s\")}')
report.append('')

# dataset overview
report.append('dataset overview')
report.append('-' * 40)
report.append(f'rows: {len(df):,}')
report.append(f'columns: {len(df.columns)}')
report.append(f'memory usage: {df.memory_usage(deep=True).sum() / 1e6:.2f} mb')
report.append('')

# data types
report.append('data types')
report.append('-' * 40)
dtypes = df.dtypes.value_counts()
for dtype, count in dtypes.items():
    report.append(f'  {dtype}: {count}')
report.append('')

# missing data
report.append('missing data')
report.append('-' * 40)
missing = df.isnull().sum()
total_missing = missing.sum()
report.append(f'total missing: {total_missing:,}')
report.append(f'percentage: {total_missing / (len(df) * len(df.columns)) * 100:.2f}%')
report.append('')

for col, count in missing[missing > 0].items():
    pct = (count / len(df)) * 100
    report.append(f'  {col}: {count:,} ({pct:.2f}%)')
report.append('')

# numeric statistics
numeric_cols = df.select_dtypes(include=[np.number]).columns
if not numeric_cols.empty:
    report.append('numeric statistics')
    report.append('-' * 40)
    for col in numeric_cols:
        report.append(f'  {col}:')
        report.append(f'    mean: {df[col].mean():.2f}')
        report.append(f'    std: {df[col].std():.2f}')
        report.append(f'    min: {df[col].min():.2f}')
        report.append(f'    max: {df[col].max():.2f}')
        report.append(f'    missing: {df[col].isnull().sum():,}')
    report.append('')

# categorical statistics
categorical_cols = df.select_dtypes(include=['object', 'category']).columns
if not categorical_cols.empty:
    report.append('categorical statistics')
    report.append('-' * 40)
    for col in categorical_cols:
        report.append(f'  {col}:')
        report.append(f'    unique values: {df[col].nunique():,}')
        report.append(f'    most common: {df[col].mode()[0] if not df[col].mode().empty else \"none\"}')
        report.append(f'    missing: {df[col].isnull().sum():,}')
    report.append('')

# duplicates
duplicates = df.duplicated().sum()
report.append('data quality')
report.append('-' * 40)
report.append(f'duplicate rows: {duplicates:,}')
report.append(f'duplicate percentage: {duplicates / len(df) * 100:.2f}%')
report.append('')

# save report
report_text = '\n'.join(report)
with open('eda_report.txt', 'w') as f:
    f.write(report_text)

print('saved: eda_report.txt')
print(report_text)
"</terminal>


PHASE 7: EDA CHECKLIST


initial inspection

  [ ] loaded data successfully
  [ ] verified data shape and columns
  [ ] checked data types
  [ ] examined sample data
  [ ] checked memory usage


data quality

  [ ] identified missing values
  [ ] quantified missing data patterns
  [ ] detected duplicate records
  [ ] identified outliers
  [ ] validated data types
  [ ] checked for inconsistencies


univariate analysis

  [ ] analyzed numeric distributions
  [ ] checked for normality
  [ ] examined categorical frequencies
  [ ] analyzed temporal patterns
  [ ] identified high cardinality columns


bivariate analysis

  [ ] computed correlation matrix
  [ ] identified strong correlations
  [ ] analyzed categorical relationships
  [ ] examined group differences
  [ ] performed significance tests


multivariate analysis

  [ ] performed dimensionality reduction
  [ ] analyzed feature importance
  [ ] identified key patterns
  [ ] detected clusters or groups


documentation

  [ ] saved all visualizations
  [ ] generated summary report
  [ ] documented findings
  [ ] noted data quality issues
  [ ] suggested next steps


PHASE 8: EDA RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS START WITH DATA INSPECTION
      never jump to modeling without understanding the data
      examine structure, types, and basic statistics first

  [2] VISUALIZE EVERYTHING
      use plots to understand distributions and relationships
      visual patterns reveal insights that statistics miss

  [3] CHECK DATA QUALITY FIRST
      identify missing values, duplicates, and outliers early
      poor data quality leads to poor insights

  [4] UNDERSTAND DISTRIBUTIONS
      know if your data is normal, skewed, or multimodal
      distribution assumptions impact statistical tests

  [5] EXPLORE RELATIONSHIPS
      examine correlations and associations between variables
      relationships drive predictive modeling

  [6] DOCUMENT EVERYTHING
      save plots, code, and findings
      others should be able to reproduce your analysis

  [7] BE SKEPTICAL OF OUTLIERS
      investigate before removing
      outliers might be errors or important signals

  [8] CONSIDER SAMPLE BIAS
      understand who/what your data represents
      bias limits generalizability

  [9] ITERATE AND REFINE
      initial findings suggest new questions
      follow interesting threads

  [10] COMMUNICATE FINDINGS
      explain what you found and why it matters
      insights are only valuable if they're understood


FINAL REMINDERS


eda is exploration

you don't know what you'll find.
follow your curiosity.
investigate anomalies.


patterns lead to insights

look for:
  - unexpected correlations
  - unusual distributions
  - hidden groups
  - temporal trends
  - outliers that matter


questions drive analysis

every plot should answer a question.
every test should address a hypothesis.
every finding should generate new questions.


the goal

not just to describe the data.
to understand what it tells us.
to guide decisions.
to inspire further investigation.

now explore your data.
