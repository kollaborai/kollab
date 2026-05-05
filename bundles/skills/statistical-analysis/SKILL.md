---
name: statistical-analysis
description: "Statistical Analysis skill - perform rigorous statistical tests and inference"
---

statistical analysis mode: RIGOROUS INFERENCE

when this skill is active, you follow disciplined statistical analysis.
this is a comprehensive guide to conducting proper statistical analysis.


PHASE 0: STATISTICAL ENVIRONMENT VERIFICATION

before attempting ANY statistical analysis, verify your tools are ready.


check scipy availability

  <terminal>python -c "import scipy; print(f'scipy {scipy.__version__} available')"</terminal>

if scipy not available:
  <terminal>pip install scipy</terminal>

verify stats module:
  <terminal>python -c "from scipy import stats; print('stats module ready')"</terminal>


check statsmodels availability

  <terminal>python -c "import statsmodels; print(f'statsmodels {statsmodels.__version__} available')"</terminal>

if statsmodels not installed (recommended for advanced analysis):
  <terminal>pip install statsmodels</terminal>


check numpy and pandas

  <terminal>python -c "import numpy; print(f'numpy {numpy.__version__} available')"</terminal>
  <terminal>python -c "import pandas; print(f'pandas {pandas.__version__} available')"</terminal>


check matplotlib for statistical plots

  <terminal>python -c "import matplotlib; print(f'matplotlib {matplotlib.__version__} available')"</terminal>


check pingouin for advanced tests (optional but recommended)

  <terminal>python -c "import pingouin; print(f'pingouin {pingouin.__version__} available')" 2>/dev/null || echo "pingouin not installed"</terminal>

if pingouin not installed:
  <terminal>pip install pingouin</terminal>


PHASE 1: DESCRIPTIVE STATISTICS


central tendency measures

calculate mean:
  import pandas as pd

  mean_value = df['column'].mean()
  print(f"Mean: {mean_value:.2f}")

calculate median:
  median_value = df['column'].median()
  print(f"Median: {median_value:.2f}")

calculate mode:
  mode_value = df['column'].mode()[0]
  print(f"Mode: {mode_value}")

when to use which:
  - mean: symmetric distributions, no outliers
  - median: skewed distributions, presence of outliers
  - mode: categorical data, identifying most frequent value


measures of dispersion

standard deviation:
  std_dev = df['column'].std()
  print(f"Standard Deviation: {std_dev:.2f}")

variance:
  variance = df['column'].var()
  print(f"Variance: {variance:.2f}")

range:
  data_range = df['column'].max() - df['column'].min()
  print(f"Range: {data_range:.2f}")

interquartile range (IQR):
  q1 = df['column'].quantile(0.25)
  q3 = df['column'].quantile(0.75)
  iqr = q3 - q1
  print(f"IQR: {iqr:.2f}")

coefficient of variation (CV):
  cv = (df['column'].std() / df['column'].mean()) * 100
  print(f"CV: {cv:.2f}%")

interpretation:
  - CV < 10%: low variability
  - CV 10-20%: moderate variability
  - CV > 20%: high variability


shape of distribution

skewness:
  from scipy import stats

  skewness = df['column'].skew()
  print(f"Skewness: {skewness:.2f}")

interpretation:
  - skewness = 0: perfectly symmetric
  - skewness > 0: right-skewed (tail to the right)
  - skewness < 0: left-skewed (tail to the left)
  - |skewness| > 1: highly skewed
  - 0.5 < |skewness| < 1: moderately skewed

kurtosis:
  kurtosis = df['column'].kurtosis()
  print(f"Kurtosis: {kurtosis:.2f}")

interpretation (excess kurtosis):
  - kurtosis = 0: mesokurtic (normal-like)
  - kurtosis > 0: leptokurtic (heavy tails, more outliers)
  - kurtosis < 0: platykurtic (light tails, fewer outliers)


comprehensive summary statistics

using describe():
  summary = df['column'].describe()
  print(summary)

percentile ranges:
  percentiles = [0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
  df['column'].quantile(percentiles)

correlation matrix:
  correlation = df.corr()
  print(correlation)

correlation with specific column:
  df.corr()['target_column'].sort_values(ascending=False)


PHASE 2: PROBABILITY DISTRIBUTIONS


identifying distributions

visual inspection:
  import matplotlib.pyplot as plt

  df['column'].hist(bins=30, edgecolor='black')
  plt.xlabel('Value')
  plt.ylabel('Frequency')
  plt.title('Distribution of Column')
  plt.show()

q-q plot for normality:
  from scipy import stats

  stats.probplot(df['column'], dist="norm", plot=plt)
  plt.title('Q-Q Plot')
  plt.show()

interpretation:
  - points on diagonal: normal distribution
  - deviation at ends: heavy/light tails
  - s-curve: skewed distribution


testing normality

shapiro-wilk test (small samples, n < 5000):
  from scipy import stats

  stat, p_value = stats.shapiro(df['column'])
  print(f"Shapiro-Wilk: statistic={stat:.4f}, p-value={p_value:.4f}")

if p_value < 0.05:
  print("Reject null hypothesis: data is NOT normal")
else:
  print("Fail to reject null hypothesis: data appears normal")


kolmogorov-smirnov test (larger samples):
  from scipy import stats

  stat, p_value = stats.kstest(df['column'], 'norm')
  print(f"KS Test: statistic={stat:.4f}, p-value={p_value:.4f}")

interpretation same as shapiro-wilk


anderson-darling test (more sensitive to tails):
  from scipy import stats

  result = stats.anderson(df['column'], dist='norm')
  print(f"Anderson-Darling: statistic={result.statistic:.4f}")

for i, (cv, sl) in enumerate(zip(result.critical_values, 
                                  result.significance_level)):
  if result.statistic > cv:
    print(f"At {sl}% significance level, reject normality")
  else:
    print(f"At {sl}% significance level, fail to reject normality")


working with common distributions

normal distribution:
  from scipy import stats
  import numpy as np

  # generate samples
  samples = np.random.normal(loc=0, scale=1, size=1000)

  # calculate probability density
  x = np.linspace(-3, 3, 100)
  pdf = stats.norm.pdf(x, loc=0, scale=1)

  # calculate cumulative probability
  cdf = stats.norm.cdf(x, loc=0, scale=1)

  # percentile point
  percentile_95 = stats.norm.ppf(0.95, loc=0, scale=1)

  # survival function (1 - CDF)
  sf = stats.norm.sf(x, loc=0, scale=1)


binomial distribution:
  # probability of k successes in n trials
  n = 10
  p = 0.5
  k = 5

  prob = stats.binom.pmf(k, n, p)
  print(f"Probability of {k} successes: {prob:.4f}")

  # cumulative probability
  cum_prob = stats.binom.cdf(k, n, p)
  print(f"Probability of <= {k} successes: {cum_prob:.4f}")

  # generate samples
  samples = stats.binom.rvs(n, p, size=1000)


poisson distribution:
  # probability of k events with rate lambda
  lam = 5
  k = 3

  prob = stats.poisson.pmf(k, lam)
  print(f"Probability of {k} events: {prob:.4f}")

  # cumulative probability
  cum_prob = stats.poisson.cdf(k, lam)
  print(f"Probability of <= {k} events: {cum_prob:.4f}")

  # generate samples
  samples = stats.poisson.rvs(lam, size=1000)


exponential distribution:
  # time between events with rate lambda
  lam = 0.5

  # probability density
  x = np.linspace(0, 10, 100)
  pdf = stats.expon.pdf(x, scale=1/lam)

  # percentile
  percentile_90 = stats.expon.ppf(0.90, scale=1/lam)

  # generate samples
  samples = stats.expon.rvs(scale=1/lam, size=1000)


PHASE 3: HYPOTHESIS TESTING FRAMEWORK


the hypothesis testing workflow

always follow this systematic approach:

  1. formulate hypotheses
     - null hypothesis (H0): default position, no effect
     - alternative hypothesis (H1/Ha): what you want to prove

  2. choose significance level
     - typically alpha = 0.05 (5% risk of Type I error)
     - more stringent: alpha = 0.01
     - less stringent: alpha = 0.10

  3. select appropriate test
     - based on data type, distribution, sample size

  4. calculate test statistic
     - using appropriate statistical method

  5. determine p-value
     - probability of observing results if H0 is true

  6. make decision
     - p-value < alpha: reject H0
     - p-value >= alpha: fail to reject H0

  7. interpret in context
     - what does this mean for your data?

  8. report effect size
     - magnitude of the difference/relationship


types of errors

type I error (false positive):
  - rejecting H0 when it's actually true
  - controlled by significance level (alpha)
  - alpha = 0.05 means 5% chance of false positive

type II error (false negative):
  - failing to reject H0 when it's actually false
  - related to statistical power (1 - beta)
  - higher sample size reduces type II error

power analysis:
  from statsmodels.stats.power import TTestIndPower

  power_analysis = TTestIndPower()
  sample_size = power_analysis.solve_power(
      effect_size=0.5,     # medium effect
      alpha=0.05,
      power=0.8,           # 80% power
      alternative='two-sided'
  )
  print(f"Required sample size: {sample_size:.0f}")


one-sample t-test

test if sample mean differs from known value:
  from scipy import stats

  # known population value
  population_mean = 100

  # sample data
  sample_data = df['column']

  # perform test
  statistic, p_value = stats.ttest_1samp(
      sample_data,
      population_mean
  )

  print(f"T-statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  alpha = 0.05
  if p_value < alpha:
      print(f"Reject H0: mean differs from {population_mean}")
  else:
      print(f"Fail to reject H0: no evidence mean differs from {population_mean}")

  # calculate effect size (Cohen's d)
  effect_size = (sample_data.mean() - population_mean) / sample_data.std()
  print(f"Cohen's d: {effect_size:.4f}")

  interpretation:
    - |d| < 0.2: small effect
    - 0.2 <= |d| < 0.5: medium effect
    - |d| >= 0.5: large effect


two-sample t-test (independent)

compare means of two independent groups:
  from scipy import stats

  group1 = df[df['category'] == 'A']['value']
  group2 = df[df['category'] == 'B']['value']

  # check for equal variances first
  statistic, p_value_var = stats.levene(group1, group2)
  equal_var = p_value_var >= 0.05

  # perform appropriate t-test
  statistic, p_value = stats.ttest_ind(
      group1,
      group2,
      equal_var=equal_var
  )

  print(f"T-statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")
  print(f"Equal variances assumed: {equal_var}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: means are significantly different")
  else:
      print("Fail to reject H0: no evidence of difference in means")

  # calculate effect size (Cohen's d)
  pooled_std = np.sqrt(
      (group1.std()**2 + group2.std()**2) / 2
  )
  effect_size = (group1.mean() - group2.mean()) / pooled_std
  print(f"Cohen's d: {effect_size:.4f}")


paired t-test (dependent)

compare means of paired samples:
  from scipy import stats

  before = df['before']
  after = df['after']

  # perform paired t-test
  statistic, p_value = stats.ttest_rel(before, after)

  print(f"T-statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: significant difference between paired samples")
  else:
      print("Fail to reject H0: no evidence of difference")

  # calculate effect size (Cohen's d for paired)
  differences = after - before
  effect_size = differences.mean() / differences.std()
  print(f"Cohen's d: {effect_size:.4f}")


anova (analysis of variance)

compare means of three or more groups:
  from scipy import stats

  groups = [df[df['category'] == cat]['value'].values 
            for cat in df['category'].unique()]

  # perform one-way ANOVA
  statistic, p_value = stats.f_oneway(*groups)

  print(f"F-statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: at least one group mean differs")
      
      # post-hoc test to find which groups differ
      from statsmodels.stats.multicomp import pairwise_tukeyhsd
      
      tukey = pairwise_tukeyhsd(
          df['value'],
          df['category']
      )
      print(tukey)
  else:
      print("Fail to reject H0: no evidence of difference in group means")


PHASE 4: NON-PARAMETRIC TESTS


when to use non-parametric tests

use when:
  - data is not normally distributed
  - sample size is small
  - ordinal data (ranked categories)
  - presence of extreme outliers
  - variances are not homogeneous


mann-whitney u test (independent samples, non-parametric)

alternative to independent t-test:
  from scipy import stats

  group1 = df[df['category'] == 'A']['value']
  group2 = df[df['category'] == 'B']['value']

  statistic, p_value = stats.mannwhitneyu(
      group1,
      group2,
      alternative='two-sided'
  )

  print(f"Mann-Whitney U statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: distributions differ significantly")
  else:
      print("Fail to reject H0: no evidence of difference")


wilcoxon signed-rank test (paired samples, non-parametric)

alternative to paired t-test:
  from scipy import stats

  before = df['before']
  after = df['after']

  statistic, p_value = stats.wilcoxon(before, after)

  print(f"Wilcoxon statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: significant difference in paired samples")
  else:
      print("Fail to reject H0: no evidence of difference")


kruskal-wallis test (multiple groups, non-parametric)

alternative to one-way ANOVA:
  from scipy import stats

  groups = [df[df['category'] == cat]['value'].values 
            for cat in df['category'].unique()]

  statistic, p_value = stats.kruskal(*groups)

  print(f"Kruskal-Wallis statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: at least one group distribution differs")
      
      # post-hoc Dunn's test
      import scikit_posthocs as sp
      
      dunn = sp.posthoc_dunn(
          df,
          val_col='value',
          group_col='category',
          p_adjust='bonferroni'
      )
      print(dunn)
  else:
      print("Fail to reject H0: no evidence of difference")


chi-square test of independence

test association between categorical variables:
  from scipy import stats

  # create contingency table
  contingency_table = pd.crosstab(df['category1'], df['category2'])

  # perform chi-square test
  statistic, p_value, dof, expected = stats.chi2_contingency(
      contingency_table
  )

  print(f"Chi-square statistic: {statistic:.4f}")
  print(f"P-value: {p_value:.4f}")
  print(f"Degrees of freedom: {dof}")

  # interpret
  if p_value < 0.05:
      print("Reject H0: variables are associated")
  else:
      print("Fail to reject H0: no evidence of association")

  # check assumptions
  # expected frequencies should be >= 5
  print(f"\nExpected frequencies:")
  print(expected)

  if (expected < 5).any():
      print("Warning: some expected frequencies < 5")
      print("Consider combining categories or using Fisher's exact test")


PHASE 5: CORRELATION AND REGRESSION


pearson correlation (linear, parametric)

measure linear relationship between continuous variables:
  from scipy import stats

  x = df['column1']
  y = df['column2']

  # calculate correlation
  correlation, p_value = stats.pearsonr(x, y)

  print(f"Pearson correlation: {correlation:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret correlation strength
  abs_correlation = abs(correlation)
  if abs_correlation < 0.3:
      strength = "weak"
  elif abs_correlation < 0.7:
      strength = "moderate"
  else:
      strength = "strong"

  print(f"Correlation strength: {strength}")

  # interpret direction
  if correlation > 0:
      print(f"Direction: positive (as X increases, Y increases)")
  elif correlation < 0:
      print(f"Direction: negative (as X increases, Y decreases)")
  else:
      print(f"Direction: no linear relationship")

  # visualize
  import matplotlib.pyplot as plt

  plt.scatter(x, y, alpha=0.5)
  plt.xlabel('Column 1')
  plt.ylabel('Column 2')
  plt.title(f'Scatter Plot (r={correlation:.3f})')

  # add trend line
  z = np.polyfit(x, y, 1)
  p = np.poly1d(z)
  plt.plot(x, p(x), "r--")

  plt.show()


spearman correlation (rank-based, non-parametric)

measure monotonic relationship (not necessarily linear):
  from scipy import stats

  correlation, p_value = stats.spearmanr(
      df['column1'],
      df['column2']
  )

  print(f"Spearman correlation: {correlation:.4f}")
  print(f"P-value: {p_value:.4f}")

  # interpret
  if p_value < 0.05:
      print("Significant monotonic relationship")
  else:
      print("No evidence of monotonic relationship")


simple linear regression

model relationship between one predictor and one response:
  import statsmodels.api as sm

  X = df['predictor']
  y = df['response']

  # add constant for intercept
  X_with_const = sm.add_constant(X)

  # fit model
  model = sm.OLS(y, X_with_const).fit()

  # print results
  print(model.summary())

  # extract key metrics
  print(f"\nR-squared: {model.rsquared:.4f}")
  print(f"Adjusted R-squared: {model.rsquared_adj:.4f}")
  print(f"F-statistic: {model.fvalue:.4f}")
  print(f"F-statistic p-value: {model.f_pvalue:.4f}")

  # interpret coefficients
  intercept = model.params['const']
  slope = model.params['predictor']

  print(f"\nIntercept: {intercept:.4f}")
  print(f"Slope: {slope:.4f}")
  print(f"\nInterpretation: For each 1-unit increase in predictor,")
  print(f"response changes by {slope:.4f} units")

  # check assumptions
  # 1. linearity: residuals vs fitted plot
  # 2. normality: q-q plot of residuals
  # 3. homoscedasticity: residuals vs fitted plot
  # 4. independence: durbin-watson test (check in summary)

  # visualize
  import matplotlib.pyplot as plt

  plt.figure(figsize=(12, 4))

  # residuals vs fitted
  plt.subplot(1, 2, 1)
  plt.scatter(model.fittedvalues, model.resid, alpha=0.5)
  plt.xlabel('Fitted values')
  plt.ylabel('Residuals')
  plt.title('Residuals vs Fitted')
  plt.axhline(y=0, color='r', linestyle='--')

  # q-q plot
  plt.subplot(1, 2, 2)
  sm.qqplot(model.resid, line='s', fit=True)
  plt.title('Q-Q Plot')

  plt.tight_layout()
  plt.show()


multiple linear regression

model relationship with multiple predictors:
  import statsmodels.api as sm

  # define predictors
  X = df[['predictor1', 'predictor2', 'predictor3']]
  y = df['response']

  # add constant
  X_with_const = sm.add_constant(X)

  # fit model
  model = sm.OLS(y, X_with_const).fit()

  # print results
  print(model.summary())

  # interpret coefficients
  print("\nCoefficient interpretations:")
  for predictor in X.columns:
      coef = model.params[predictor]
      print(f"  {predictor}: {coef:.4f}")

  # check for multicollinearity
  from statsmodels.stats.outliers_influence import variance_inflation_factor

  print("\nVariance Inflation Factors (VIF):")
  vif_data = pd.DataFrame()
  vif_data["predictor"] = X.columns
  vif_data["VIF"] = [
      variance_inflation_factor(X_with_const.values, i)
      for i in range(1, len(X.columns) + 1)
  ]
  print(vif_data)

  # interpret VIF
  # VIF > 10: high multicollinearity
  # VIF > 5: moderate multicollinearity
  # VIF < 5: low multicollinearity


logistic regression

model binary outcomes:
  import statsmodels.api as sm

  X = df[['predictor1', 'predictor2', 'predictor3']]
  y = df['binary_outcome']

  # add constant
  X_with_const = sm.add_constant(X)

  # fit logistic regression
  model = sm.Logit(y, X_with_const).fit()

  # print results
  print(model.summary())

  # convert coefficients to odds ratios
  print("\nOdds Ratios:")
  odds_ratios = np.exp(model.params)
  print(odds_ratios)

  # interpret
  for predictor in X.columns:
      or_value = odds_ratios[predictor]
      print(f"\n  {predictor}: OR = {or_value:.4f}")
      if or_value > 1:
          print(f"    Each unit increase multiplies odds by {or_value:.2f}x")
      else:
          print(f"    Each unit increase divides odds by {1/or_value:.2f}x")


PHASE 6: ADVANCED STATISTICAL CONCEPTS


effect size calculation

cohen's d (two groups):
  from scipy import stats

  def cohen_d(group1, group2):
      pooled_std = np.sqrt(
          (group1.std()**2 + group2.std()**2) / 2
      )
      return (group1.mean() - group2.mean()) / pooled_std

  d = cohen_d(group1, group2)
  print(f"Cohen's d: {d:.4f}")

  interpretation:
    - |d| < 0.2: trivial effect
    - 0.2 <= |d| < 0.5: small effect
    - 0.5 <= |d| < 0.8: medium effect
    - |d| >= 0.8: large effect


eta-squared (ANOVA):
  # proportion of variance explained
  ss_between = sum(len(g) * (np.mean(g) - np.mean(df['value']))**2 
                  for g in groups)
  ss_total = sum((x - np.mean(df['value']))**2 for x in df['value'])
  
  eta_squared = ss_between / ss_total
  print(f"Eta-squared: {eta_squared:.4f}")

  interpretation:
    - 0.01: small effect
    - 0.06: medium effect
    - 0.14: large effect


phi coefficient (2x2 contingency table):
  # measure association in 2x2 table
  from scipy import stats

  # calculate chi-square
  chi2, p, dof, expected = stats.chi2_contingency(contingency_table)

  # calculate phi
  n = contingency_table.values.sum()
  phi = np.sqrt(chi2 / n)

  print(f"Phi coefficient: {phi:.4f}")

  interpretation same as correlation (-1 to +1)


confidence intervals

confidence interval for mean:
  from scipy import stats

  confidence_level = 0.95
  degrees_of_freedom = len(df['column']) - 1

  sample_mean = df['column'].mean()
  sample_std = df['column'].std()
  standard_error = sample_std / np.sqrt(len(df['column']))

  # calculate margin of error
  t_critical = stats.t.ppf(
      (1 + confidence_level) / 2,
      degrees_of_freedom
  )
  margin_of_error = t_critical * standard_error

  # calculate CI
  ci_lower = sample_mean - margin_of_error
  ci_upper = sample_mean + margin_of_error

  print(f"{confidence_level*100}% CI for mean: "
        f"({ci_lower:.4f}, {ci_upper:.4f})")


confidence interval for proportion:
  from statsmodels.stats.proportion import proportion_confint

  successes = df[df['outcome'] == 'success'].shape[0]
  total = df.shape[0]

  ci_lower, ci_upper = proportion_confint(
      successes,
      total,
      alpha=0.05,
      method='wilson'
  )

  print(f"95% CI for proportion: ({ci_lower:.4f}, {ci_upper:.4f})")


bootstrap confidence intervals

non-parametric confidence intervals:
  import numpy as np

  def bootstrap_mean(data, n_bootstrap=10000, ci=95):
      bootstrap_means = []
      for _ in range(n_bootstrap):
          sample = np.random.choice(data, size=len(data), replace=True)
          bootstrap_means.append(np.mean(sample))
      
      lower = np.percentile(bootstrap_means, (100 - ci) / 2)
      upper = np.percentile(bootstrap_means, 100 - (100 - ci) / 2)
      return lower, upper

  ci_lower, ci_upper = bootstrap_mean(df['column'])
  print(f"95% bootstrap CI: ({ci_lower:.4f}, {ci_upper:.4f})")


PHASE 7: TIME SERIES ANALYSIS


time series decomposition

decompose into trend, seasonal, and residual components:
  from statsmodels.tsa.seasonal import seasonal_decompose

  # ensure datetime index
  df['date'] = pd.to_datetime(df['date'])
  df.set_index('date', inplace=True)

  # decompose
  decomposition = seasonal_decompose(
      df['value'],
      model='additive',
      period=12  # seasonal period
  )

  # plot
  import matplotlib.pyplot as plt

  fig = decomposition.plot()
  plt.tight_layout()
  plt.show()


checking for stationarity

augmented dickey-fuller test:
  from statsmodels.tsa.stattools import adfuller

  result = adfuller(df['value'])

  print(f"ADF Statistic: {result[0]:.4f}")
  print(f"P-value: {result[1]:.4f}")
  print("Critical Values:")
  for key, value in result[4].items():
      print(f"  {key}: {value:.4f}")

  # interpret
  if result[1] < 0.05:
      print("Reject H0: time series is stationary")
  else:
      print("Fail to reject H0: time series is non-stationary")


autocorrelation and partial autocorrelation

autocorrelation function (ACF):
  from statsmodels.tsa.stattools import acf
  from statsmodels.graphics.tsaplots import plot_acf

  # calculate ACF
  acf_values = acf(df['value'], nlags=20)

  # plot
  plot_acf(df['value'], lags=20)
  plt.show()

  # interpret
  # significant spikes at lag k: correlation at lag k


partial autocorrelation function (PACF):
  from statsmodels.tsa.stattools import pacf
  from statsmodels.graphics.tsaplots import plot_pacf

  # calculate PACF
  pacf_values = pacf(df['value'], nlags=20)

  # plot
  plot_pacf(df['value'], lags=20)
  plt.show()


PHASE 8: STATISTICAL POWER AND SAMPLE SIZE


power analysis for t-tests

from statsmodels.stats.power import TTestIndPower

power_analysis = TTestIndPower()

# calculate required sample size
effect_size = 0.5  # medium effect
alpha = 0.05
power = 0.8  # 80% power
ratio = 1  # equal group sizes

sample_size = power_analysis.solve_power(
    effect_size=effect_size,
    alpha=alpha,
    power=power,
    ratio=ratio,
    alternative='two-sided'
)

print(f"Required sample size per group: {sample_size:.0f}")


power curve
effect_sizes = np.array([0.2, 0.5, 0.8])
sample_sizes = np.array(range(10, 500, 10))

power_analysis.plot_power(
    dep_var='nobs',
    nobs=sample_sizes,
    effect_size=effect_sizes,
    alpha=0.05
)

plt.show()


PHASE 9: STATISTICAL RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] CHECK ASSUMPTIONS before applying tests
      - normality for parametric tests
      - homogeneity of variances
      - independence of observations
      if assumptions violated, use non-parametric alternatives

  [2] ALWAYS CALCULATE EFFECT SIZES
      p-values alone are insufficient
      report both statistical significance and practical significance

  [3] VISUALIZE DATA before testing
      understanding the data distribution
      is crucial for selecting appropriate tests

  [4] PRE-SPECIFY HYPOTHESES
      avoid p-hacking by testing only what you planned
      control for multiple comparisons if testing many hypotheses

  [5] USE APPROPRIATE SAMPLE SIZE
      too small: low power, inconclusive results
      too large: detects trivial differences
      perform power analysis when possible

  [6] REPORT CONFIDENCE INTERVALS
      they provide more information than p-values
      show precision of estimates

  [7] INTERPRET IN CONTEXT
      statistical significance != practical importance
      consider domain knowledge when interpreting results

  [8] CHECK FOR OUTLIERS
      outliers can dramatically affect results
      investigate their cause before removing

  [9] DOCUMENT ANALYSIS DECISIONS
      why you chose specific tests
      what assumptions you checked
      any transformations applied

  [10] REPRODUCIBLE ANALYSIS
      set random seeds
      document code clearly
      use version control


FINAL REMINDERS


statistics is about uncertainty

not about proving things beyond doubt.
about quantifying uncertainty.
about making informed decisions.


p-values are not probabilities

p-value = P(data | H0)
NOT: P(H0 | data)
common misinterpretation to avoid.


correlation is not causation

just because two variables are related
doesn't mean one causes the other
consider confounding variables


the goal

not to find "significant" results.
to understand your data.
to quantify evidence.
to make informed decisions.

now go analyze that data.
