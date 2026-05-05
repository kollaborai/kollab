---
name: cli-pretty
description: "CLI Pretty skill - terminal UI styling with powerline, colors, and unicode"
---

cli-pretty mode: MAKE TERMINALS BEAUTIFUL

when this skill is active, you follow terminal aesthetic excellence.
this is a comprehensive guide to creating stunning CLI interfaces that
make terminal junkies absolutely lose their minds.


PHASE 0: UNDERSTAND THE KOLLAB COLOR SYSTEM

before styling ANY terminal output, understand the color infrastructure.

location of color utilities:
  kollabor/io/visual_effects.py

key imports you will use:
  from kollabor.io.visual_effects import (
      AgnosterSegment,
      AgnosterColors,
      ColorPalette,
      make_bg_color,
      make_fg_color,
      Powerline
  )

verify the file exists:
  <terminal>ls -la kollabor/io/visual_effects.py</terminal>

check available color classes:
  <terminal>grep -n "class.*Color" kollabor/io/visual_effects.py</terminal>


PHASE 1: ANSI ESCAPE CODE FUNDAMENTALS

ANSI codes control terminal styling. format: \033[<code>m

basic codes:
  BOLD = "\033[1m"           bold text
  DIM = "\033[2m"            dimmed text
  ITALIC = "\033[3m"         italic (not all terminals)
  UNDERLINE = "\033[4m"      underlined
  BLINK = "\033[5m"          blinking (avoid this)
  REVERSE = "\033[7m"        swap fg/bg
  HIDDEN = "\033[8m"         hidden text
  RESET = "\033[0m"          reset all formatting

foreground colors (30-37, 90-97 for bright):
  BLACK = "\033[30m"         BRIGHT_BLACK = "\033[90m"
  RED = "\033[31m"           BRIGHT_RED = "\033[91m"
  GREEN = "\033[32m"         BRIGHT_GREEN = "\033[92m"
  YELLOW = "\033[33m"        BRIGHT_YELLOW = "\033[93m"
  BLUE = "\033[34m"          BRIGHT_BLUE = "\033[94m"
  MAGENTA = "\033[35m"       BRIGHT_MAGENTA = "\033[95m"
  CYAN = "\033[36m"          BRIGHT_CYAN = "\033[96m"
  WHITE = "\033[37m"         BRIGHT_WHITE = "\033[97m"

background colors (40-47, 100-107 for bright):
  BG_BLACK = "\033[40m"      BG_BRIGHT_BLACK = "\033[100m"
  BG_RED = "\033[41m"        BG_BRIGHT_RED = "\033[101m"
  BG_GREEN = "\033[42m"      BG_BRIGHT_GREEN = "\033[102m"
  BG_YELLOW = "\033[43m"     BG_BRIGHT_YELLOW = "\033[103m"
  BG_BLUE = "\033[44m"       BG_BRIGHT_BLUE = "\033[104m"
  BG_MAGENTA = "\033[45m"    BG_BRIGHT_MAGENTA = "\033[105m"
  BG_CYAN = "\033[46m"       BG_BRIGHT_CYAN = "\033[106m"
  BG_WHITE = "\033[47m"      BG_BRIGHT_WHITE = "\033[107m"

combining codes (separate with semicolon):
  "\033[1;32m"               bold + green
  "\033[1;97;44m"            bold + white text + blue bg
  "\033[102;30;1m"           bright green bg + black text + bold

example usage:
  print(f"\033[1;32mSuccess!\033[0m")
  print(f"\033[41;97m ERROR \033[0m Something went wrong")

always RESET after styled text:
  WRONG: print(f"\033[1mBold text")
  RIGHT: print(f"\033[1mBold text\033[0m")


PHASE 2: RGB AND 256-COLOR MODE

for richer colors, use 256-color or true color (24-bit RGB).

256-color foreground: \033[38;5;<color>m
256-color background: \033[48;5;<color>m

  color ranges in 256 palette:
    0-7      standard colors
    8-15     bright colors
    16-231   216-color cube (6x6x6)
    232-255  grayscale (24 shades)

  example:
    "\033[38;5;208m"         orange text (color 208)
    "\033[48;5;17m"          dark blue background

true color (24-bit RGB):
  foreground: \033[38;2;<r>;<g>;<b>m
  background: \033[48;2;<r>;<g>;<b>m

  example:
    "\033[38;2;163;230;53m"   lime green text (rgb 163,230,53)
    "\033[48;2;6;182;212m"    cyan background (rgb 6,182,212)

kollabor helper functions:
  make_fg_color(r, g, b)     returns proper escape code for fg
  make_bg_color(r, g, b)     returns proper escape code for bg

  these automatically detect terminal color support and fall back
  to 256-color or basic colors if needed.

example with helpers:
  from kollabor.io.visual_effects import make_bg_color, make_fg_color

  lime_bg = make_bg_color(163, 230, 53)
  dark_text = make_fg_color(20, 20, 20)
  reset = "\033[0m"

  print(f"{lime_bg}{dark_text} SUCCESS {reset}")


PHASE 3: AGNOSTER COLORS - THE SIGNATURE PALETTE

kollabor uses a specific color palette for consistency.

class AgnosterColors:
  # lime palette (RGB tuples)
  LIME = (163, 230, 53)
  LIME_DARK = (132, 204, 22)
  LIME_DARKER = (100, 160, 15)

  # cyan palette
  CYAN = (6, 182, 212)
  CYAN_DARK = (8, 145, 178)
  CYAN_LIGHT = (34, 211, 238)

  # neutral backgrounds
  BG_DARK = (30, 30, 30)
  BG_MID = (50, 50, 50)
  BG_LIGHT = (70, 70, 70)

  # text colors
  TEXT_DARK = (20, 20, 20)
  TEXT_LIGHT = (240, 240, 240)

usage pattern:
  from kollabor.io.visual_effects import AgnosterColors, make_bg_color, make_fg_color

  # create color codes from RGB tuples
  lime_bg = make_bg_color(*AgnosterColors.LIME)
  lime_fg = make_fg_color(*AgnosterColors.LIME)
  dark_bg = make_bg_color(*AgnosterColors.BG_DARK)
  text_light = make_fg_color(*AgnosterColors.TEXT_LIGHT)

  # use the * operator to unpack RGB tuple
  # AgnosterColors.LIME = (163, 230, 53)
  # make_bg_color(*AgnosterColors.LIME) = make_bg_color(163, 230, 53)

color philosophy:
  - LIME for success, active, primary actions
  - CYAN for info, secondary, navigation
  - NEUTRAL for backgrounds, disabled, descriptions
  - high contrast: dark text on light bg, light text on dark bg


PHASE 4: POWERLINE CHARACTERS

powerline fonts provide special separator characters for slick transitions.

key powerline characters:
  PL_RIGHT = ""          right-pointing triangle (\ue0b0)
  PL_RIGHT_SOFT = ""     right-pointing thin (\ue0b1)
  PL_LEFT = ""           left-pointing triangle (\ue0b2)
  PL_LEFT_SOFT = ""      left-pointing thin (\ue0b3)

how powerline transitions work:
  [segment A bg][segment B bg color as fg][PL_RIGHT][segment B content]

  the trick: the separator character uses:
    - segment A background (continues the bg)
    - segment B background color AS FOREGROUND (creates the arrow)

example building a powerline segment:
  lime_bg = make_bg_color(*AgnosterColors.LIME)
  lime_fg = make_fg_color(*AgnosterColors.LIME)
  dark_bg = make_bg_color(*AgnosterColors.BG_DARK)
  text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
  reset = "\033[0m"

  # segment 1: lime background
  # separator: dark bg + lime fg (creates lime arrow into dark)
  # segment 2: dark background

  line = (
      f"{lime_bg}{text_dark} ICON {reset}"
      f"{dark_bg}{lime_fg}{PL_RIGHT}{reset}"
      f"{dark_bg}{text_light} content {reset}"
  )

visual result:
   ICON  content
         ^ powerline separator creates smooth transition

pro tip: to fade to terminal background, use just the fg color:
  f"{lime_fg}{PL_RIGHT}{reset}"
  this creates an arrow that fades into the default terminal bg


PHASE 5: AGNOSTER SEGMENT BUILDER

AgnosterSegment is a builder class for creating powerline-style segments.

basic usage:
  from kollabor.io.visual_effects import AgnosterSegment

  seg = AgnosterSegment()
  seg.add_lime("Success")
  seg.add_cyan("Info")
  seg.add_neutral("Details", "dark")
  result = seg.render()

available methods:
  seg.add_lime(text, variant)     variant: "normal", "dark", "darker"
  seg.add_cyan(text, variant)     variant: "normal", "dark", "light"
  seg.add_neutral(text, variant)  variant: "dark", "mid", "light"
  seg.add(text, bg_rgb, fg_rgb)   custom colors as RGB tuples
  seg.render(separator)           render with powerline separator

render options:
  seg.render()                    uses default PL_RIGHT separator
  seg.render(separator="")        no separator (for single segments)
  seg.render(separator=PL_LEFT)   use left-pointing separator

example - status line:
  seg = AgnosterSegment()
  seg.add_lime("OK", "dark")
  seg.add_cyan("3 items")
  seg.add_neutral("ready", "mid")
  print(seg.render())

  output:  OK  3 items  ready

example - selected item with glow:
  seg = AgnosterSegment()
  seg.add_lime(" * ")              glow indicator
  seg.add_cyan(f" /{name} ")       command name
  seg.add_neutral(description)     description fades out
  print(seg.render())


PHASE 6: UNICODE SYMBOLS AND ICONS

unicode provides thousands of symbols for visual enhancement.

arrows:
  ARROW_UP = "▲"        solid up triangle
  ARROW_DOWN = "▼"      solid down triangle
  ARROW_RIGHT = "▶"     solid right triangle
  ARROW_LEFT = "◀"      solid left triangle
  CHEVRON_RIGHT = "❯"   chevron (commonly used for selection)
  CHEVRON_LEFT = "❮"    chevron left

geometric shapes:
  DIAMOND = "◆"         solid diamond (great for "glow" effect)
  DIAMOND_OUTLINE = "◇" outline diamond
  CIRCLE = "●"          solid circle
  CIRCLE_OUTLINE = "○"  outline circle
  SQUARE = "■"          solid square
  SQUARE_OUTLINE = "□"  outline square

status indicators:
  CHECK = "✓"           checkmark
  CROSS = "✗"           x mark
  BULLET = "•"          bullet point
  DOT = "·"             middle dot (great for dot leaders)
  ELLIPSIS = "…"        horizontal ellipsis

category icons (used in kollabor menu):
  GEAR = "⚙"            system/settings
  COMMAND = "⌘"         command key (conversation)
  OPTION = "⌥"          option key (development)
  DIAMOND_ICON = "◈"    agent
  MENU = "☰"            hamburger menu (tasks)
  BARS = "≡"            three bars (files)
  PLUS_CIRCLE = "⊕"     circled plus (plugins)

special characters:
  STAR = "★"            solid star
  STAR_OUTLINE = "☆"    outline star
  HEART = "♥"           heart
  LIGHTNING = "⚡"       lightning bolt
  FIRE = "🔥"           fire (emoji - may not render in all terminals)

usage in menus:
  CATEGORY_CONFIG = {
      "system": {"name": "System", "icon": "⚙"},
      "conversation": {"name": "Chat", "icon": "⌘"},
      "plugins": {"name": "Plugins", "icon": "⊕"},
  }

  for cat, config in CATEGORY_CONFIG.items():
      print(f"  {config['icon']} {config['name']}")


PHASE 7: BOX DRAWING CHARACTERS

box drawing creates frames, tables, and structured layouts.

single-line box:
  TL = "┌"  horizontal = "─"  TR = "┐"
  vertical = "│"
  BL = "└"                    BR = "┘"

double-line box:
  TL = "╔"  horizontal = "═"  TR = "╗"
  vertical = "║"
  BL = "╚"                    BR = "╝"

heavy/bold box:
  TL = "┏"  horizontal = "━"  TR = "┓"
  vertical = "┃"
  BL = "┗"                    BR = "┛"

connectors (single):
  T_DOWN = "┬"    joins top edge to vertical down
  T_UP = "┴"      joins bottom edge to vertical up
  T_RIGHT = "├"   joins left edge to horizontal right
  T_LEFT = "┤"    joins right edge to horizontal left
  CROSS = "┼"     four-way intersection

connectors (heavy):
  T_DOWN = "┳"    T_UP = "┻"    T_RIGHT = "┣"
  T_LEFT = "┫"    CROSS = "╋"

example - simple box:
  width = 40
  print(f"┌{'─' * width}┐")
  print(f"│{'Content here'.center(width)}│")
  print(f"└{'─' * width}┘")

  output:
  ┌────────────────────────────────────────┐
  │             Content here               │
  └────────────────────────────────────────┘

example - table:
  print("┌──────────┬──────────┬──────────┐")
  print("│  Name    │  Status  │  Count   │")
  print("├──────────┼──────────┼──────────┤")
  print("│  Alpha   │  Active  │    42    │")
  print("│  Beta    │  Pending │    17    │")
  print("└──────────┴──────────┴──────────┘")


PHASE 8: DOT LEADERS AND ALIGNMENT

dot leaders connect labels to values across space.

basic dot leader:
  DOT = "·"

  label = "/help"
  description = "Show available commands"
  padding = 25 - len(label)
  dots = DOT * padding

  print(f"  {label} {dots} {description}")

  output:
    /help ··················· Show available commands

calculating padding:
  max_width = 25
  name = "/config"
  padding_len = max_width - len(name) - 2  # -2 for spaces
  dots = DOT * max(2, padding_len)         # minimum 2 dots

  line = f"  {name} {dots} {description}"

combining with colors:
  BOLD = "\033[1m"
  DIM = "\033[2m"
  RESET = "\033[0m"
  CYAN = "\033[36m"

  line = f"  {CYAN}{BOLD}{name}{RESET} {DIM}{dots} {description}{RESET}"

alignment techniques:
  # right-align numbers
  count = 42
  print(f"Items: {count:>5}")    # "Items:    42"

  # left-align with padding
  name = "test"
  print(f"{name:<10} | description")  # "test       | description"

  # center
  title = "MENU"
  print(f"{title:^20}")  # "        MENU        "


PHASE 9: SCROLL INDICATORS

scroll indicators show when content extends beyond visible area.

basic scroll indicator:
  ARROW_UP = "▲"
  ARROW_DOWN = "▼"

  if has_more_above:
      print(f"  {ARROW_UP} {count} more above")

  if has_more_below:
      print(f"  {ARROW_DOWN} {count} more below")

styled scroll indicator with powerline:
  def make_scroll_indicator(direction, count):
      arrow = "▲" if direction == "up" else "▼"

      cyan_bg = make_bg_color(*AgnosterColors.CYAN_DARK)
      cyan_fg = make_fg_color(*AgnosterColors.CYAN_DARK)
      text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
      reset = "\033[0m"
      bold = "\033[1m"

      return (
          f"{cyan_bg}{text_dark} {arrow} {bold}{count}{reset}"
          f"{cyan_bg} {reset}{cyan_fg}{PL_RIGHT}{reset}"
      )

  print(make_scroll_indicator("up", 3))
  print(make_scroll_indicator("down", 5))

scroll state management:
  scroll_offset = 0
  max_visible = 8
  total_items = 20

  has_more_above = scroll_offset > 0
  has_more_below = scroll_offset + max_visible < total_items

  items_above = scroll_offset
  items_below = total_items - (scroll_offset + max_visible)


PHASE 10: SELECTED ITEM HIGHLIGHTING

selected items need strong visual distinction.

simple highlight (reverse video):
  REVERSE = "\033[7m"
  RESET = "\033[0m"

  for i, item in enumerate(items):
      if i == selected_index:
          print(f"{REVERSE} {item} {RESET}")
      else:
          print(f"  {item}")

powerline highlight with glow:
  GLOW = "◆"

  def format_item(name, description, is_selected):
      if is_selected:
          lime_bg = make_bg_color(*AgnosterColors.LIME)
          cyan_bg = make_bg_color(*AgnosterColors.CYAN)
          cyan_fg = make_fg_color(*AgnosterColors.CYAN)
          lime_fg = make_fg_color(*AgnosterColors.LIME)
          dark_bg = make_bg_color(*AgnosterColors.BG_MID)
          dark_fg = make_fg_color(*AgnosterColors.BG_MID)
          text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
          text_light = make_fg_color(*AgnosterColors.TEXT_LIGHT)

          return (
              f"{lime_bg}{text_dark} {GLOW} {RESET}"
              f"{cyan_bg}{lime_fg}{PL_RIGHT}{RESET}"
              f"{cyan_bg}{text_dark}{BOLD} {name} {RESET}"
              f"{dark_bg}{cyan_fg}{PL_RIGHT}{RESET}"
              f"{dark_bg}{text_light} {description} {RESET}"
              f"{dark_fg}{PL_RIGHT}{RESET}"
          )
      else:
          return f"   {name}  {DIM}{description}{RESET}"

visual result:
   *  /selected  Description here        <- full powerline glow
      /other ······· Other description     <- subtle, dimmed


PHASE 11: CATEGORY HEADERS

category headers group related items with visual separation.

simple category header:
  def format_category(name, icon):
      return f"\n  {icon} {name}\n"

powerline category header:
  def format_category_header(category):
      config = {
          "system": {"icon": "⚙", "name": "System"},
          "plugins": {"icon": "⊕", "name": "Plugins"},
      }.get(category, {"icon": "?", "name": category})

      lime_bg = make_bg_color(*AgnosterColors.LIME)
      lime_fg = make_fg_color(*AgnosterColors.LIME)
      dark_bg = make_bg_color(*AgnosterColors.BG_DARK)
      text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
      text_light = make_fg_color(*AgnosterColors.TEXT_LIGHT)

      return (
          f"{lime_bg}{text_dark}{BOLD} {config['icon']} {RESET}"
          f"{dark_bg}{lime_fg}{PL_RIGHT}{RESET}"
          f"{dark_bg}{text_light} {config['name']} {RESET}"
          f"{lime_fg}{PL_RIGHT}{RESET}"
      )

visual result:
   ⚙  System                   <- lime icon, dark name, fade out
      /help ······ Show commands
      /config ···· Configuration
   ⊕  Plugins
      /matrix ···· Matrix rain effect


PHASE 12: PUTTING IT ALL TOGETHER - MENU EXAMPLE

complete menu implementation:

  from kollabor.io.visual_effects import (
      AgnosterSegment, AgnosterColors, ColorPalette,
      make_bg_color, make_fg_color
  )

  BOLD = "\033[1m"
  DIM = "\033[2m"
  RESET = ColorPalette.RESET
  PL_RIGHT = ""
  DOT = "·"
  ARROW_UP = "▲"
  ARROW_DOWN = "▼"
  GLOW = "◆"

  class MenuRenderer:
      def render_menu(self, commands, selected_idx, scroll_offset):
          lines = []

          # scroll up indicator
          if scroll_offset > 0:
              lines.append(self._scroll_indicator("up", scroll_offset))

          # visible items
          current_category = None
          for i, cmd in enumerate(commands[scroll_offset:scroll_offset+8]):
              real_idx = scroll_offset + i

              # category header
              if cmd["category"] != current_category:
                  current_category = cmd["category"]
                  lines.append(self._category_header(current_category))

              # command line
              is_selected = real_idx == selected_idx
              lines.append(self._command_line(cmd, is_selected))

          # scroll down indicator
          remaining = len(commands) - scroll_offset - 8
          if remaining > 0:
              lines.append(self._scroll_indicator("down", remaining))

          return "\n".join(lines)

      def _scroll_indicator(self, direction, count):
          arrow = ARROW_UP if direction == "up" else ARROW_DOWN
          seg = AgnosterSegment()
          seg.add_cyan(f" {arrow} {count} ", "dark")
          return seg.render(separator="")

      def _category_header(self, category):
          icons = {"system": "⚙", "plugins": "⊕"}
          icon = icons.get(category, "?")

          lime_bg = make_bg_color(*AgnosterColors.LIME)
          lime_fg = make_fg_color(*AgnosterColors.LIME)
          dark_bg = make_bg_color(*AgnosterColors.BG_DARK)
          text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
          text_light = make_fg_color(*AgnosterColors.TEXT_LIGHT)

          return (
              f"{lime_bg}{text_dark}{BOLD} {icon} {RESET}"
              f"{dark_bg}{lime_fg}{PL_RIGHT}{RESET}"
              f"{dark_bg}{text_light} {category.title()} {RESET}"
              f"{lime_fg}{PL_RIGHT}{RESET}"
          )

      def _command_line(self, cmd, is_selected):
          name = cmd["name"]
          desc = cmd.get("description", "")[:35]

          if is_selected:
              # full powerline glow
              lime_bg = make_bg_color(*AgnosterColors.LIME)
              cyan_bg = make_bg_color(*AgnosterColors.CYAN)
              cyan_fg = make_fg_color(*AgnosterColors.CYAN)
              lime_fg = make_fg_color(*AgnosterColors.LIME)
              dark_bg = make_bg_color(*AgnosterColors.BG_MID)
              dark_fg = make_fg_color(*AgnosterColors.BG_MID)
              text_dark = make_fg_color(*AgnosterColors.TEXT_DARK)
              text_light = make_fg_color(*AgnosterColors.TEXT_LIGHT)

              return (
                  f"{lime_bg}{text_dark}{BOLD} {GLOW} {RESET}"
                  f"{cyan_bg}{lime_fg}{PL_RIGHT}{RESET}"
                  f"{cyan_bg}{text_dark}{BOLD} /{name} {RESET}"
                  f"{dark_bg}{cyan_fg}{PL_RIGHT}{RESET}"
                  f"{dark_bg}{text_light} {desc} {RESET}"
                  f"{dark_fg}{PL_RIGHT}{RESET}"
              )
          else:
              # subtle with dot leaders
              dots = DOT * (18 - len(name))
              mid_bg = make_bg_color(*AgnosterColors.BG_DARK)
              mid_fg = make_fg_color(*AgnosterColors.BG_DARK)
              cyan_fg = make_fg_color(*AgnosterColors.CYAN)

              return (
                  f"   {mid_bg}{cyan_fg}{BOLD} /{name} {RESET}"
                  f"{mid_fg}{PL_RIGHT}{RESET}"
                  f" {DIM}{dots} {desc}{RESET}"
              )


PHASE 13: TERMINAL WIDTH AND RESPONSIVE DESIGN

adapt layouts to terminal width.

get terminal size:
  import shutil

  width, height = shutil.get_terminal_size()
  # or
  import os
  width = os.get_terminal_size().columns

responsive truncation:
  def truncate(text, max_len):
      if len(text) <= max_len:
          return text
      return text[:max_len-2] + ".."

  # use available width
  term_width = shutil.get_terminal_size().columns
  name_width = 20
  desc_width = term_width - name_width - 10  # padding
  description = truncate(full_description, desc_width)

responsive layouts:
  if term_width < 60:
      # compact mode: name only
      print(f"  /{name}")
  elif term_width < 100:
      # medium: name + short description
      print(f"  /{name:<15} {description[:30]}")
  else:
      # full: powerline with everything
      print(full_powerline_format)


PHASE 14: COLOR SUPPORT DETECTION

detect and adapt to terminal color capabilities.

kollabor color detection:
  from kollabor.io.visual_effects import get_color_support, ColorSupport

  support = get_color_support()

  if support == ColorSupport.TRUE_COLOR:
      # full 24-bit RGB
      color = make_fg_color(163, 230, 53)
  elif support == ColorSupport.EXTENDED:
      # 256 colors
      color = "\033[38;5;154m"  # approximate lime
  elif support == ColorSupport.BASIC:
      # 16 colors
      color = "\033[92m"  # bright green
  else:
      # no colors
      color = ""

environment override:
  KOLLAB_COLOR_MODE=truecolor    force 24-bit
  KOLLAB_COLOR_MODE=256          force 256-color
  KOLLAB_COLOR_MODE=none         disable colors
  NO_COLOR=1                       standard no-color flag

graceful degradation:
  make_fg_color() and make_bg_color() handle this automatically.
  they detect color support and fall back appropriately.


PHASE 15: RULES FOR CLI PRETTINESS

while this skill is active, these rules are MANDATORY:

  [1] ALWAYS RESET
      every styled string must end with \033[0m or RESET
      leaked styles corrupt all subsequent output

  [2] USE THE PALETTE
      stick to AgnosterColors for consistency
      LIME for primary/success, CYAN for secondary/info

  [3] CONTRAST IS KING
      dark text on light backgrounds
      light text on dark backgrounds
      never light-on-light or dark-on-dark

  [4] POWERLINE TRANSITIONS
      separator fg = previous segment bg color
      this creates the smooth arrow effect

  [5] TEST IN MULTIPLE TERMINALS
      iterm2, terminal.app, vscode, alacritty
      colors render differently everywhere

  [6] RESPECT NO_COLOR
      check for NO_COLOR env var
      provide plain text fallback

  [7] KEEP IT READABLE
      pretty > unreadable
      dim non-essential info
      highlight what matters

  [8] UNICODE FALLBACKS
      not all terminals support all unicode
      have ASCII alternatives ready
      PL_RIGHT "" might need ">" fallback


FINAL REMINDERS

beauty is not decoration. it is communication.
  - color draws attention to what matters
  - structure creates scannable hierarchy
  - contrast separates concerns

terminal junkies appreciate:
  - powerline transitions (not boxes everywhere)
  - consistent color language
  - subtle animations (not disco)
  - dense information display
  - keyboard-first interaction

the kollabor signature:
  - lime  for success, active, primary
  - cyan  for info, navigation, secondary
  - powerline separators between segments
  - dot leaders for alignment
  - unicode icons for categories

go make something beautiful.
