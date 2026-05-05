---
name: data-visualization
description: "Data Visualization skill - create compelling charts and graphs with matplotlib/seaborn"
---

data visualization mode: VISUAL STORYTELLING

when this skill is active, you create charts that communicate insights clearly
and effectively. this is a comprehensive guide to data visualization best
practices and techniques.


PHASE 0: VISUALIZATION ENVIRONMENT VERIFICATION

before creating ANY visualizations, verify your tools are ready.


check matplotlib availability

  <terminal>python -c "import matplotlib; print('matplotlib', matplotlib.__version__)"</terminal>

if matplotlib not available:
  <terminal>pip install matplotlib</terminal>


check seaborn availability (recommended)

  <terminal>python -c "import seaborn; print('seaborn', seaborn.__version__)" 2>/dev/null || echo "seaborn not installed"</terminal>

if seaborn not installed (highly recommended):
  <terminal>pip install seaborn</terminal>


check matplotlib backends

  <terminal>python -c "import matplotlib.pyplot as plt; print('backend:', plt.get_backend())"</terminal>

check available backends:
  <terminal>python -c "import matplotlib; print('available:', matplotlib.rcsetup.all_backends)"</terminal>

for jupyter notebooks:
  <terminal>python -c "import matplotlib.pyplot as plt; plt.ion(); print('interactive mode enabled')"</terminal>

for static images (script output):
  <terminal>python -c "import matplotlib; matplotlib.use('Agg'); print('Agg backend configured')"</terminal>


check figure display environment

  <terminal>echo $DISPLAY</terminal>

  <terminal>python -c "import os; print('JUPYTER_NOTEBOOK:', 'notebook' in os.environ.get('IPythonKernel', ''))"</terminal>

  <terminal>python -c "import matplotlib.pyplot as plt; plt.figure(); print('figure creation works')"</terminal>


verify output directory

  <terminal>ls -la plots/ 2>/dev/null || mkdir -p plots && echo "created plots/ directory"</terminal>

  <terminal>ls -la figures/ 2>/dev/null || mkdir -p figures && echo "created figures/ directory"</terminal>


check common data libraries

  <terminal>python -c "import pandas; print('pandas', pandas.__version__)" 2>/dev/null || echo "pandas not installed"</terminal>
  <terminal>python -c "import numpy; print('numpy', numpy.__version__)" 2>/dev/null || echo "numpy not installed"</terminal>

if missing:
  <terminal>pip install pandas numpy</terminal>


PHASE 1: VISUALIZATION FUNDAMENTALS


understand your audience and purpose

before creating any chart, answer these questions:

  who will see this visualization?
    - technical audience (developers, data scientists)
    - business stakeholders (managers, executives)
    - general audience (customers, public)
    - mixed audience

  what is the purpose?
    - exploration (discovering patterns)
    - explanation (communicating findings)
    - persuasion (convincing action)
    - monitoring (tracking metrics)

  what action should the viewer take?
    - make a decision
    - understand a trend
    - compare options
    - spot anomalies

  what data complexity can they handle?
    - simple metrics and comparisons
    - multivariate relationships
    - statistical distributions
    - time series with seasonality


choose the right chart type

based on your analysis goal:

comparing values:
  [ok] bar chart          - compare categories
  [ok] column chart       - compare categories (vertical)
  [ok] grouped bars       - compare multiple series
  [ok] stacked bars       - show part-to-whole

showing distribution:
  [ok] histogram          - frequency distribution
  [ok] density plot       - smooth distribution
  [ok] box plot           - quartiles and outliers
  [ok] violin plot        - distribution + box plot
  [ok] ridgeline plot     - multiple distributions

showing relationships:
  [ok] scatter plot       - two variables
  [ok] line chart         - trends over time
  [ok] bubble chart       - three variables
  [ok] heat map           - correlation matrix

showing composition:
  [ok] pie chart          - part-to-whole (avoid if >5 categories)
  [ok] stacked bar        - composition over time
  [ok] area chart         - composition over time
  [ok] treemap            - hierarchical composition

showing geospatial:
  [ok] choropleth map     - values by region
  [ok] bubble map         - locations with magnitude
  [ok] flow map           - movement/connections


chart selection decision tree

  is it time series data?
    yes -> line chart (if few series) or small multiples (if many)
    no  -> continue

  are you comparing categories?
    yes -> bar chart (if horizontal labels long) or column chart
    no  -> continue

  are you showing distribution?
    yes -> histogram (simple) or box plot (with outliers)
    no  -> continue

  are you showing correlation?
    yes -> scatter plot (2 variables) or heat map (many variables)
    no  -> continue

  are you showing part-to-whole?
    yes -> pie chart (if <=5 categories) or stacked bar (if time series)
    no  -> reconsider data and goals


PHASE 2: PLOT SETUP AND CONFIGURATION


basic plot template

  import matplotlib.pyplot as plt
  import seaborn as sns
  import pandas as pd
  import numpy as np

  # set style
  sns.set_style("whitegrid")
  plt.figure(figsize=(12, 6))

  # create your plot
  # ... plotting code ...

  # add labels and title
  plt.xlabel("X-axis label")
  plt.ylabel("Y-axis label")
  plt.title("Descriptive Title Here")

  # add grid
  plt.grid(True, alpha=0.3)

  # adjust layout
  plt.tight_layout()

  # save or show
  plt.savefig("plots/output.png", dpi=300, bbox_inches="tight")
  # plt.show()


configure matplotlib defaults

  import matplotlib.pyplot as plt

  # set global style
  plt.style.use('seaborn-v0_8-whitegrid')

  # configure defaults
  plt.rcParams.update({
      'figure.figsize': (12, 6),
      'font.size': 11,
      'axes.labelsize': 12,
      'axes.titlesize': 14,
      'xtick.labelsize': 10,
      'ytick.labelsize': 10,
      'legend.fontsize': 10,
      'figure.dpi': 100,
      'savefig.dpi': 300,
      'axes.grid': True,
      'grid.alpha': 0.3,
  })


seaborn style options

  import seaborn as sns

  # available styles
  # 'darkgrid', 'whitegrid', 'dark', 'white', 'ticks'

  # set style
  sns.set_style("whitegrid")

  # set color palette
  sns.set_palette("husl")

  # available palettes:
  # 'deep', 'muted', 'pastel', 'bright', 'dark', 'colorblind'
  # 'husl', 'hls', 'Set1', 'Set2', 'Set3'
  # 'Blues', 'Reds', 'Greens', etc. (sequential)
  # 'RdBu', 'RdYlBu', etc. (diverging)


color palette best practices

  categorical data (5-7 categories):
    sns.set_palette("husl", n_colors=len(categories))

  sequential data (low to high):
    sns.set_palette("Blues")

  diverging data (neutral + extremes):
    sns.set_palette("RdBu_r")

  accessible colors:
    sns.set_palette("colorblind")


PHASE 3: BASIC CHART TYPES


bar charts (categorical comparisons)

  import matplotlib.pyplot as plt
  import seaborn as sns
  import pandas as pd

  # simple bar chart
  plt.figure(figsize=(10, 6))
  sns.barplot(data=df, x='category', y='value')
  plt.title("Sales by Category")
  plt.xlabel("Category")
  plt.ylabel("Sales ($)")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/bar_chart.png", dpi=300)


  # horizontal bar chart (better for long labels)
  plt.figure(figsize=(10, 6))
  sns.barplot(data=df, y='category', x='value')
  plt.title("Sales by Category")
  plt.xlabel("Sales ($)")
  plt.ylabel("Category")
  plt.tight_layout()
  plt.savefig("plots/horizontal_bar_chart.png", dpi=300)


  # grouped bar chart (multiple series)
  plt.figure(figsize=(12, 6))
  sns.barplot(data=df, x='category', y='value', hue='year')
  plt.title("Sales by Category and Year")
  plt.xlabel("Category")
  plt.ylabel("Sales ($)")
  plt.legend(title="Year")
  plt.tight_layout()
  plt.savefig("plots/grouped_bar_chart.png", dpi=300)


histograms (distributions)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # basic histogram
  plt.figure(figsize=(10, 6))
  sns.histplot(data=df, x='value', bins=30)
  plt.title("Distribution of Values")
  plt.xlabel("Value")
  plt.ylabel("Frequency")
  plt.tight_layout()
  plt.savefig("plots/histogram.png", dpi=300)


  # histogram with kde curve
  plt.figure(figsize=(10, 6))
  sns.histplot(data=df, x='value', bins=30, kde=True)
  plt.title("Distribution of Values with KDE")
  plt.xlabel("Value")
  plt.ylabel("Frequency")
  plt.tight_layout()
  plt.savefig("plots/histogram_kde.png", dpi=300)


  # multiple histograms
  plt.figure(figsize=(10, 6))
  sns.histplot(data=df, x='value', hue='category', bins=30, alpha=0.5)
  plt.title("Distribution by Category")
  plt.xlabel("Value")
  plt.ylabel("Frequency")
  plt.tight_layout()
  plt.savefig("plots/histogram_multiple.png", dpi=300)


box plots (distributions with outliers)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # single box plot
  plt.figure(figsize=(10, 6))
  sns.boxplot(data=df, y='value')
  plt.title("Distribution of Values")
  plt.ylabel("Value")
  plt.tight_layout()
  plt.savefig("plots/boxplot.png", dpi=300)


  # box plot by category
  plt.figure(figsize=(12, 6))
  sns.boxplot(data=df, x='category', y='value')
  plt.title("Distribution by Category")
  plt.xlabel("Category")
  plt.ylabel("Value")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/boxplot_category.png", dpi=300)


  # box plot with outliers highlighted
  plt.figure(figsize=(12, 6))
  sns.boxplot(data=df, x='category', y='value', showfliers=True)
  plt.title("Distribution by Category with Outliers")
  plt.xlabel("Category")
  plt.ylabel("Value")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/boxplot_outliers.png", dpi=300)


scatter plots (relationships)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # basic scatter plot
  plt.figure(figsize=(10, 6))
  sns.scatterplot(data=df, x='variable_x', y='variable_y')
  plt.title("Relationship between X and Y")
  plt.xlabel("Variable X")
  plt.ylabel("Variable Y")
  plt.tight_layout()
  plt.savefig("plots/scatter.png", dpi=300)


  # scatter with hue (color by category)
  plt.figure(figsize=(10, 6))
  sns.scatterplot(data=df, x='variable_x', y='variable_y', hue='category')
  plt.title("Relationship by Category")
  plt.xlabel("Variable X")
  plt.ylabel("Variable Y")
  plt.legend(title="Category")
  plt.tight_layout()
  plt.savefig("plots/scatter_hue.png", dpi=300)


  # scatter with size (third variable)
  plt.figure(figsize=(10, 6))
  sns.scatterplot(data=df, x='variable_x', y='variable_y',
                  hue='category', size='variable_z')
  plt.title("Multi-variable Relationship")
  plt.xlabel("Variable X")
  plt.ylabel("Variable Y")
  plt.legend(title="Category")
  plt.tight_layout()
  plt.savefig("plots/scatter_size.png", dpi=300)


line charts (time series)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # single line chart
  plt.figure(figsize=(14, 6))
  sns.lineplot(data=df, x='date', y='value')
  plt.title("Value Over Time")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.tight_layout()
  plt.savefig("plots/line_chart.png", dpi=300)


  # multiple lines
  plt.figure(figsize=(14, 6))
  sns.lineplot(data=df, x='date', y='value', hue='category')
  plt.title("Values Over Time by Category")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.legend(title="Category")
  plt.tight_layout()
  plt.savefig("plots/line_chart_multiple.png", dpi=300)


  # line chart with confidence interval
  plt.figure(figsize=(14, 6))
  sns.lineplot(data=df, x='date', y='value', ci=95)
  plt.title("Value Over Time with 95% CI")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.tight_layout()
  plt.savefig("plots/line_chart_ci.png", dpi=300)


PHASE 4: ADVANCED VISUALIZATION TECHNIQUES


small multiples (facet grids)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # facet grid by category
  g = sns.FacetGrid(df, col='category', col_wrap=3,
                    height=4, aspect=1.2)
  g.map(sns.histplot, 'value', bins=20)
  g.fig.suptitle("Distribution by Category", y=1.02)
  plt.tight_layout()
  plt.savefig("plots/facet_grid.png", dpi=300)


  # facet grid with multiple variables
  g = sns.FacetGrid(df, row='category1', col='category2',
                    height=4, aspect=1.2)
  g.map(sns.scatterplot, 'x', 'y')
  g.fig.suptitle("Scatter Plots by Category", y=1.02)
  plt.tight_layout()
  plt.savefig("plots/facet_grid_2d.png", dpi=300)


  # pair plot (all pairwise relationships)
  sns.pairplot(df, hue='category', diag_kind='hist')
  plt.suptitle("Pairwise Relationships", y=1.02)
  plt.tight_layout()
  plt.savefig("plots/pair_plot.png", dpi=300)


heat maps (correlation matrices)

  import matplotlib.pyplot as plt
  import seaborn as sns
  import pandas as pd

  # correlation heatmap
  plt.figure(figsize=(10, 8))
  correlation_matrix = df.corr()
  sns.heatmap(correlation_matrix, annot=True, fmt='.2f',
              cmap='coolwarm', center=0,
              square=True, linewidths=0.5)
  plt.title("Correlation Matrix")
  plt.tight_layout()
  plt.savefig("plots/correlation_heatmap.png", dpi=300)


  # heatmap with custom palette
  plt.figure(figsize=(12, 8))
  sns.heatmap(correlation_matrix, annot=True, fmt='.2f',
              cmap='RdBu_r', center=0, vmin=-1, vmax=1,
              cbar_kws={'label': 'Correlation'},
              square=True, linewidths=0.5)
  plt.title("Correlation Matrix")
  plt.tight_layout()
  plt.savefig("plots/correlation_heatmap_custom.png", dpi=300)


violin plots (detailed distributions)

  import matplotlib.pyplot as plt
  import seaborn as sns

  # single violin plot
  plt.figure(figsize=(8, 6))
  sns.violinplot(data=df, y='value')
  plt.title("Distribution of Values")
  plt.ylabel("Value")
  plt.tight_layout()
  plt.savefig("plots/violinplot.png", dpi=300)


  # violin plot by category
  plt.figure(figsize=(12, 6))
  sns.violinplot(data=df, x='category', y='value')
  plt.title("Distribution by Category")
  plt.xlabel("Category")
  plt.ylabel("Value")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/violinplot_category.png", dpi=300)


  # split violin plot
  plt.figure(figsize=(12, 6))
  sns.violinplot(data=df, x='category', y='value',
                 hue='subcategory', split=True)
  plt.title("Distribution by Category and Subcategory")
  plt.xlabel("Category")
  plt.ylabel("Value")
  plt.legend(title="Subcategory")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/violinplot_split.png", dpi=300)


time series visualization

  import matplotlib.pyplot as plt
  import seaborn as sns

  # time series with trend line
  plt.figure(figsize=(14, 6))
  sns.lineplot(data=df, x='date', y='value', label='Value')
  sns.regplot(data=df, x='date', y='value', scatter=False,
              label='Trend', color='red')
  plt.title("Value Over Time with Trend")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.legend()
  plt.tight_layout()
  plt.savefig("plots/time_series_trend.png", dpi=300)


  # time series with moving average
  df['moving_avg'] = df['value'].rolling(window=7).mean()

  plt.figure(figsize=(14, 6))
  sns.lineplot(data=df, x='date', y='value',
                label='Value', alpha=0.6)
  sns.lineplot(data=df, x='date', y='moving_avg',
                label='7-day Moving Avg', linewidth=2)
  plt.title("Value Over Time with Moving Average")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.legend()
  plt.tight_layout()
  plt.savefig("plots/time_series_ma.png", dpi=300)


  # time series with seasonality decomposition
  from statsmodels.tsa.seasonal import seasonal_decompose

  df.set_index('date', inplace=True)
  decomposition = seasonal_decompose(df['value'], model='additive',
                                     period=365)

  fig, axes = plt.subplots(4, 1, figsize=(14, 10))
  decomposition.observed.plot(ax=axes[0], title='Observed')
  decomposition.trend.plot(ax=axes[1], title='Trend')
  decomposition.seasonal.plot(ax=axes[2], title='Seasonal')
  decomposition.resid.plot(ax=axes[3], title='Residual')
  plt.tight_layout()
  plt.savefig("plots/time_series_decomposition.png", dpi=300)


PHASE 5: CUSTOMIZATION AND STYLING


annotations and text

  import matplotlib.pyplot as plt
  import seaborn as sns

  fig, ax = plt.subplots(figsize=(10, 6))

  sns.barplot(data=df, x='category', y='value', ax=ax)

  # add value labels on bars
  for i, v in enumerate(df['value']):
      ax.text(i, v, f'{v:.1f}', ha='center', va='bottom')

  # add annotation for max value
  max_idx = df['value'].idxmax()
  max_cat = df.loc[max_idx, 'category']
  max_val = df.loc[max_idx, 'value']
  ax.annotate(f'Max: {max_val}',
               xy=(max_idx, max_val),
               xytext=(max_idx, max_val * 1.1),
               arrowprops=dict(arrowstyle='->', color='red'))

  plt.title("Sales by Category with Annotations")
  plt.xlabel("Category")
  plt.ylabel("Sales ($)")
  plt.xticks(rotation=45)
  plt.tight_layout()
  plt.savefig("plots/annotated_bars.png", dpi=300)


custom legends

  import matplotlib.pyplot as plt
  import seaborn as sns

  fig, ax = plt.subplots(figsize=(10, 6))

  sns.lineplot(data=df, x='date', y='value',
                hue='category', ax=ax)

  # customize legend
  ax.legend(title='Category',
            bbox_to_anchor=(1.05, 1),
            loc='upper left',
            ncol=1,
            frameon=True,
            shadow=True)

  plt.title("Values Over Time")
  plt.xlabel("Date")
  plt.ylabel("Value")
  plt.tight_layout()
  plt.savefig("plots/custom_legend.png", dpi=300)


custom axes and limits

  import matplotlib.pyplot as plt
  import seaborn as sns

  fig, ax = plt.subplots(figsize=(10, 6))

  sns.scatterplot(data=df, x='x', y='y', hue='category', ax=ax)

  # set custom limits
  ax.set_xlim(0, 100)
  ax.set_ylim(0, 100)

  # log scale
  ax.set_xscale('log')
  ax.set_yscale('log')

  # custom ticks
  ax.set_xticks([1, 10, 100])
  ax.set_yticks([1, 10, 100])

  plt.title("Log-scale Scatter Plot")
  plt.xlabel("X (log scale)")
  plt.ylabel("Y (log scale)")
  plt.tight_layout()
  plt.savefig("plots/custom_axes.png", dpi=300)


subplots and figure layout

  import matplotlib.pyplot as plt
  import seaborn as sns

  fig, axes = plt.subplots(2, 2, figsize=(14, 10))

  # subplot 1: histogram
  sns.histplot(data=df, x='value', bins=30, ax=axes[0, 0])
  axes[0, 0].set_title('Distribution')

  # subplot 2: box plot
  sns.boxplot(data=df, x='category', y='value', ax=axes[0, 1])
  axes[0, 1].set_title('By Category')
  axes[0, 1].tick_params(axis='x', rotation=45)

  # subplot 3: scatter plot
  sns.scatterplot(data=df, x='x', y='y', ax=axes[1, 0])
  axes[1, 0].set_title('Relationship')

  # subplot 4: time series
  sns.lineplot(data=df, x='date', y='value', ax=axes[1, 1])
  axes[1, 1].set_title('Over Time')

  plt.tight_layout()
  plt.savefig("plots/subplots.png", dpi=300)


PHASE 6: EXPORTING AND FORMATTING


high-quality output formats

  import matplotlib.pyplot as plt

  # PNG (lossless, transparency support)
  plt.savefig("plots/output.png",
              dpi=300,
              bbox_inches='tight',
              transparent=False,
              facecolor='white')

  # PDF (vector, publication quality)
  plt.savefig("plots/output.pdf",
              bbox_inches='tight',
              transparent=False,
              facecolor='white')

  # SVG (vector, web-friendly)
  plt.savefig("plots/output.svg",
              bbox_inches='tight',
              transparent=False,
              facecolor='white')

  # EPS (vector, LaTeX friendly)
  plt.savefig("plots/output.eps",
              bbox_inches='tight',
              transparent=False,
              facecolor='white')


resolution and size guidelines

  for web display:
    dpi: 72-100
    figsize: (10, 6) or (12, 6)
    format: PNG

  for presentations:
    dpi: 150-200
    figsize: (12, 7) or (14, 8)
    format: PNG or PDF

  for publications:
    dpi: 300-600
    figsize: (8, 5) or (10, 6)
    format: PDF, EPS, or SVG

  for posters:
    dpi: 300+
    figsize: (20, 15) or larger
    format: PDF or PNG


PHASE 7: INTERACTIVE VISUALIZATION


plotly interactive charts

  import plotly.express as px
  import plotly.graph_objects as go

  # install if needed
  # pip install plotly

  # interactive scatter plot
  fig = px.scatter(df, x='x', y='y', color='category',
                   hover_data=['value'], title="Interactive Scatter")
  fig.write_html("plots/interactive_scatter.html")
  fig.show()


  # interactive line chart
  fig = px.line(df, x='date', y='value', color='category',
                title="Interactive Time Series")
  fig.write_html("plots/interactive_line.html")
  fig.show()


  # interactive histogram
  fig = px.histogram(df, x='value', color='category',
                     nbins=30, title="Interactive Distribution")
  fig.write_html("plots/interactive_histogram.html")
  fig.show()


PHASE 8: DATA VISUALIZATION CHECKLIST


pre-visualization checklist

  [ ] understand audience and purpose
  [ ] define the message you want to communicate
  [ ] choose appropriate chart type for data and goal
  [ ] verify data quality and completeness
  [ ] handle missing values appropriately
  [ ] ensure data types are correct


design checklist

  [ ] axis labels are clear and descriptive
  [ ] title communicates main insight
  [ ] color palette is accessible and appropriate
  [ ] legend is positioned to not obscure data
  [ ] font size is readable at output size
  [ ] aspect ratio preserves data proportions
  [ ] grid lines aid reading without distraction
  [ ] annotations add value, not clutter


accuracy checklist

  [ ] y-axis starts at zero (unless intentionally broken)
  [ ] scales are appropriate for data range
  [ ] error bars/CI shown where appropriate
  [ ] sample size indicated for small datasets
  [ ] outliers are not arbitrarily removed
  [ ] time series uses consistent intervals
  [ ] geographic projections are accurate


accessibility checklist

  [ ] color-blind friendly palette
  [ ] high contrast (minimum 4.5:1 for text)
  [ ] patterns in addition to colors
  [ ] alt text provided for web use
  [ ] scalable vector format for large displays
  [ ] annotations are readable


PHASE 9: COMMON VISUALIZATION MISTAKES


mistake: misleading y-axis

wrong:
  y-axis doesn't start at zero, exaggerating differences
  different scales used for comparison

correct:
  start y-axis at zero for bar/column charts
  use consistent scales for comparison
  if breaking axis is necessary, clearly mark it


mistake: too much data in one chart

wrong:
  20+ categories in a single pie chart
  50+ time series in one line chart
  all variables in one scatter plot matrix

correct:
  aggregate small categories into "other"
  use small multiples for many series
  filter to most important variables


mistake: 3d charts for 2d data

wrong:
  3d pie charts
  3d bar charts
  3d line charts

correct:
  use 2d charts - they're more accurate and readable
  3d only adds value for actual 3d data


mistake: rainbow color palettes

wrong:
  using all colors of the rainbow
  colors that don't have perceptual ordering

correct:
  use sequential palettes for ordered data
  use diverging palettes for data with meaningful midpoint
  use categorical palettes for nominal data


mistake: decorative over functional

wrong:
  excessive styling that obscures data
  fancy fonts that are hard to read
  effects that don't add insight

correct:
  form follows function
  every visual element should convey information
  keep it simple and clear


PHASE 10: VISUALIZATION RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS START WITH DATA EXPLORATION
      never visualize without understanding the data
      check distributions, missing values, outliers first

  [2] CHOOSE THE RIGHT CHART TYPE
      match chart to data type and analysis goal
      don't force data into inappropriate visualizations

  [3] LABEL EVERYTHING CLEARLY
      axis labels, title, legend - all must be descriptive
      assume reader knows nothing about the context

  [4] USE ACCESSIBLE COLORS
      color-blind friendly palettes
      sufficient contrast
      multiple cues (color + pattern) when possible

  [5] PRESERVE DATA PROPORTIONS
      y-axis starts at zero for comparison charts
      don't distort with broken axes
      maintain aspect ratio for spatial data

  [6] HANDLE OUTLIERS APPROPRIATELY
      show outliers in distributions
      don't hide them
      explain if they're excluded

  [7] PROVIDE CONTEXT
      sample size, time period, data source
      confidence intervals where appropriate
      methodology notes

  [8] AVOID CHART JUNK
      remove 3d effects, unnecessary borders
      simplify grid lines
      every element should serve a purpose

  [9] TEST AT OUTPUT SIZE
      verify readability at intended display size
      adjust font sizes and figure dimensions
      ensure legends don't overlap

  [10] TELL A STORY
      highlight key insights
      guide viewer to important patterns
      use annotations strategically


FINAL REMINDERS


clarity over cleverness

the goal is communication, not decoration.
if a chart doesn't communicate, it's failed.


less is more

remove everything that doesn't add information.
simplify, simplify, simplify.


data first, visualization second

understand the data before visualizing.
visualization should reveal insights,
not create them.


test with your audience

show drafts to intended audience.
get feedback on clarity and effectiveness.
iterate based on feedback.


document your choices

why this chart type?
  why these colors?
  why this aggregation?
  why this filtering?

future viewers will thank you.


now create visualizations that inform,
insight, and inspire action.
