"""Space shooter components for the full-screen framework.

Retro 80s arcade-style vertical space shooter. Player ship at the bottom,
enemies descending from the top, Galaga-style. Hold left/right (or A/D) to
move, space fires. Destroyed enemies drop power-ups: weapon upgrades
(double, spread, plasma), rapid fire, shields, and extra lives.
"""

import random
from typing import List, Optional

from kollabor_tui.visual_effects import ColorPalette


class Star:
    """A single star in the background starfield."""

    def __init__(self, x: int, y: int, width: int, height: int):
        """Initialize star.

        Args:
            x: X position
            y: Y position
            width: Terminal width (for wrapping)
            height: Terminal height (for wrapping)
        """
        self.x = x
        self.y = float(y)
        self.width = width
        self.height = height
        # Different star speeds create parallax effect
        self.layer = random.choice([1, 2, 3])  # 1=far, 3=close
        self.speed = self.layer * 12.0  # Faster layers = closer stars
        self.char = random.choice([".", ".", ".", "*", "+", "·", "°"])
        self.next_update = 0.0

    def update(self, time: float) -> bool:
        """Update star position (moves downward - ships flying up).

        Args:
            time: Current time

        Returns:
            True always (stars wrap around)
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)

        # Stars move downward (ships flying upward)
        self.y += 1

        # Wrap around at bottom
        if self.y >= self.height:
            self.y = 0
            self.x = random.randint(0, self.width - 1)

        return True

    def render(self, renderer):
        """Render star.

        Args:
            renderer: FullScreenRenderer instance
        """
        # Dimmer stars are further away
        if self.layer == 1:
            color = ColorPalette.DIM_GREY
        elif self.layer == 2:
            color = ColorPalette.GREY
        else:
            color = ColorPalette.BRIGHT_WHITE

        renderer.write_at(self.x, int(self.y), self.char, color)


class PlayerShip:
    """The player-controlled ship with banking animations and hold-to-move.

    Terminals deliver no key-up events, so holding is inferred from the OS
    key-repeat stream: the first press nudges the ship, and once repeat
    events start streaming in (a second same-direction press within
    STREAK_GAP), the ship switches to continuous velocity that stays alive
    while presses keep arriving within ENGAGE_REFRESH of each other.
    """

    SPRITE_WIDTH = 9
    SPRITE_HEIGHT = 4

    # Hold-to-move tuning
    STREAK_GAP = 0.5  # max gap between presses to still count as "held"
    ENGAGE_REFRESH = 0.25  # continuous motion window refreshed by each press
    HOLD_LEAD = 5  # how far ahead of the ship the target runs while held
    TAP_NUDGE = 3  # columns moved by a single tap

    # Ship sprites - pointing upward
    SPRITES = {
        "straight": [
            "    ▲    ",
            "   ▐█▌   ",
            "  ▟███▙  ",
            " █▀   ▀█ ",
        ],
        "bank_right": [
            "     ▲   ",
            "    ▐█▌  ",
            "   ▟███▙▄",
            "  █▀  ▀█ ",
        ],
        "bank_left": [
            "   ▲     ",
            "  ▐█▌    ",
            "▄▟███▙   ",
            " █▀  ▀█  ",
        ],
    }

    def __init__(self, start_x: int, start_y: int, width: int, height: int):
        """Initialize player ship.

        Args:
            start_x: Starting X position
            start_y: Starting Y position
            width: Terminal width
            height: Terminal height
        """
        self.x = float(start_x)
        self.y = float(start_y)
        self.width = width
        self.height = height
        self.state = "straight"
        self.target_x = float(start_x)
        self.next_update = 0.0
        self.invulnerable_until = 0.0

        # Hold-to-move state
        self.held_dir = 0
        self.press_streak = 0
        self.last_press_time = -1e9

        # Engine exhaust animation
        self.exhaust_frame = 0
        self.exhaust_chars = ["▒", "▓", "█", "▓"]

    @property
    def nose_x(self) -> int:
        """X position of the ship's nose (laser origin)."""
        return int(self.x) + 4

    def _clamp_x(self, x: float) -> float:
        """Clamp an X target inside the playfield."""
        return max(1.0, min(float(self.width - self.SPRITE_WIDTH - 1), x))

    def press(self, direction: int, now: float):
        """Register a left/right key event (tap or key-repeat).

        Args:
            direction: -1 for left, +1 for right
            now: Current game time
        """
        if direction == self.held_dir and now - self.last_press_time <= self.STREAK_GAP:
            self.press_streak += 1
        else:
            self.held_dir = direction
            self.press_streak = 1
            # First press of a streak gives an immediate responsive nudge
            self.target_x = self._clamp_x(self.target_x + direction * self.TAP_NUDGE)
        self.last_press_time = now

    def _hold_active(self, now: float) -> bool:
        """Whether the key-repeat stream indicates the key is still held."""
        return (
            self.press_streak >= 2
            and now - self.last_press_time <= self.ENGAGE_REFRESH
        )

    def is_invulnerable(self, time: float) -> bool:
        """Check whether the ship is in post-hit invulnerability."""
        return time < self.invulnerable_until

    def update(self, time: float) -> bool:
        """Update ship position and banking state.

        Args:
            time: Current time

        Returns:
            True to continue
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / 30.0)  # 30 FPS for smooth movement

        # While held, keep the target running ahead of the ship
        if self._hold_active(time):
            self.target_x = self._clamp_x(self.x + self.held_dir * self.HOLD_LEAD)

        # Glide towards target, banking in the direction of travel
        dx = self.target_x - self.x
        if abs(dx) > 0.5:
            step = min(2.0, abs(dx))
            if dx > 0:
                self.x += step
                self.state = "bank_right"
            else:
                self.x -= step
                self.state = "bank_left"
        else:
            self.state = "straight"

        # Update exhaust animation
        self.exhaust_frame = (self.exhaust_frame + 1) % len(self.exhaust_chars)

        return True

    def render(self, renderer, time: float, shield: bool = False):
        """Render ship (blinking while invulnerable).

        Args:
            renderer: FullScreenRenderer instance
            time: Current time (drives the invulnerability blink)
            shield: Whether the shield pickup is active
        """
        if self.is_invulnerable(time) and int(time / 0.12) % 2 == 0:
            return

        sprite = self.SPRITES.get(self.state, self.SPRITES["straight"])

        x = int(self.x)
        y = int(self.y)

        ship_color = ColorPalette.BRIGHT_CYAN

        for row_idx, row in enumerate(sprite):
            for col_idx, char in enumerate(row):
                if char != " ":
                    px = x + col_idx
                    py = y + row_idx
                    if 0 <= px < self.width and 0 <= py < self.height:
                        renderer.write_at(px, py, char, ship_color)

        # Shield brackets around the hull
        if shield:
            renderer.write_at(x - 1, y + 2, "⟨", ColorPalette.BRIGHT_BLUE)
            renderer.write_at(x + self.SPRITE_WIDTH, y + 2, "⟩", ColorPalette.BRIGHT_BLUE)

        # Render engine exhaust below ship (we're flying up)
        exhaust_positions = [(4, 4), (4, 5)]  # Two exhaust points below ship
        for ex_offset, ey_offset in exhaust_positions:
            exhaust_x = x + ex_offset
            exhaust_y = y + ey_offset
            if 0 <= exhaust_x < self.width and 0 <= exhaust_y < self.height:
                exhaust_char = self.exhaust_chars[self.exhaust_frame]
                renderer.write_at(
                    exhaust_x, exhaust_y, exhaust_char, ColorPalette.YELLOW
                )


class Enemy:
    """An enemy ship (invader or boss) coming from the top."""

    # Enemy sprites - pointing downward (coming at player)
    SPRITES = {
        "invader_f": [
            " ▀   ▀ ",
            "▄▀▀▄▀▀▄",
            "▐█▄▄▄█▌",
            " ▀▄▄▄▀ ",
        ],
        "boss_galaga": [
            " ▄▀▀▄▀▀▄ ",
            " █▄███▄█ ",
            " ▐█████▌ ",
            "  ▀███▀  ",
        ],
    }

    HITPOINTS = {"invader_f": 1, "boss_galaga": 3}
    SCORES = {"invader_f": 100, "boss_galaga": 400}

    def __init__(
        self,
        enemy_type: str,
        x: int,
        y: int,
        width: int,
        height: int,
        speed: float,
    ):
        """Initialize enemy.

        Args:
            enemy_type: Type of enemy ('invader_f', 'boss_galaga')
            x: X position
            y: Y position (starts negative, above screen)
            width: Terminal width
            height: Terminal height
            speed: Descent speed in rows per second
        """
        self.enemy_type = enemy_type
        self.x = float(x)
        self.y = float(y)
        self.width = width
        self.height = height
        self.speed = speed
        self.hp = self.HITPOINTS[enemy_type]
        self.score = self.SCORES[enemy_type]
        self.next_update = 0.0
        self.wobble = 0.0
        self.wobble_dir = 1
        self.hit_flash_until = 0.0

    @property
    def sprite_width(self) -> int:
        """Rendered sprite width in columns."""
        return len(self.SPRITES[self.enemy_type][0])

    @property
    def render_x(self) -> int:
        """Actual rendered X position (including wobble)."""
        return int(self.x + self.wobble)

    @property
    def gun_position(self) -> tuple:
        """(x, y) the enemy fires from - bottom center of the sprite."""
        return (self.render_x + self.sprite_width // 2, int(self.y) + 4)

    def take_hit(self, time: float, damage: int = 1) -> bool:
        """Apply laser damage.

        Args:
            time: Current time
            damage: Hitpoints to remove

        Returns:
            True if the enemy is destroyed
        """
        self.hp -= damage
        self.hit_flash_until = time + 0.15
        return self.hp <= 0

    def update(self, time: float) -> bool:
        """Update enemy position (moving downward).

        Args:
            time: Current time

        Returns:
            True if enemy is still on screen
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)

        # Move downward (towards player at bottom)
        self.y += 1

        # Wobble left and right
        self.wobble += self.wobble_dir * 0.4
        if abs(self.wobble) > 3:
            self.wobble_dir *= -1

        # Off screen at bottom
        if self.y > self.height + 5:
            return False

        return True

    def render(self, renderer, time: float):
        """Render enemy.

        Args:
            renderer: FullScreenRenderer instance
            time: Current time (drives the hit flash)
        """
        sprite = self.SPRITES.get(self.enemy_type, self.SPRITES["invader_f"])

        x = self.render_x
        y = int(self.y)

        # Enemy colors (flash white when hit)
        if time < self.hit_flash_until:
            color = ColorPalette.BRIGHT_WHITE
        elif self.enemy_type == "boss_galaga":
            color = ColorPalette.BRIGHT_YELLOW
        else:
            color = ColorPalette.BRIGHT_RED

        for row_idx, row in enumerate(sprite):
            for col_idx, char in enumerate(row):
                if char != " ":
                    px = x + col_idx
                    py = y + row_idx
                    if 0 <= px < self.width and 0 <= py < self.height:
                        renderer.write_at(px, py, char, color)


class Laser:
    """A player projectile firing upward.

    Supports the weapon system: lateral drift (spread shots), damage
    (plasma hits harder), and piercing (plasma passes through kills).
    """

    def __init__(
        self,
        x: float,
        y: float,
        width: int,
        height: int,
        dx: float = 0.0,
        damage: int = 1,
        pierce_hits: int = 1,
        plasma: bool = False,
    ):
        """Initialize laser.

        Args:
            x: X position
            y: Y position
            width: Terminal width (for off-screen culling)
            height: Terminal height
            dx: Lateral drift per row of travel (spread shots)
            damage: Hitpoints removed per enemy hit
            pierce_hits: How many enemies this shot can hit before dying
            plasma: Render as a heavy plasma bolt
        """
        self.x = float(x)
        self.y = float(y)
        self.width = width
        self.height = height
        self.dx = dx
        self.damage = damage
        self.pierce_hits = pierce_hits
        self.plasma = plasma
        self.hit_ids: set = set()
        self.speed = 50.0
        self.next_update = 0.0
        self.chars = ["█", "▓"] if plasma else ["│", "║", "┃", "║"]
        self.frame = 0

    def update(self, time: float) -> bool:
        """Update laser position (moving upward, drifting sideways).

        Args:
            time: Current time

        Returns:
            True if laser is still on screen
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)
        self.y -= 1  # Move upward
        self.x += self.dx
        self.frame = (self.frame + 1) % len(self.chars)

        return self.y >= 0 and -2 <= self.x <= self.width + 2

    def render(self, renderer):
        """Render laser.

        Args:
            renderer: FullScreenRenderer instance
        """
        x = int(self.x)
        y = int(self.y)
        char = self.chars[self.frame]

        if self.plasma:
            head_colors = [
                ColorPalette.BRIGHT_MAGENTA,
                ColorPalette.MAGENTA,
                ColorPalette.DIM_MAGENTA,
            ]
        else:
            head_colors = [
                ColorPalette.BRIGHT_WHITE,
                ColorPalette.BRIGHT_CYAN,
                ColorPalette.CYAN,
            ]

        # Draw laser trail (vertical)
        for i in range(3):
            py = y + i  # Trail below the head
            if 0 <= py < self.height:
                renderer.write_at(x, py, char, head_colors[i])


class EnemyBullet:
    """An enemy projectile falling towards the player."""

    def __init__(self, x: int, y: int, height: int, speed: float):
        """Initialize enemy bullet.

        Args:
            x: X position
            y: Y position
            height: Terminal height
            speed: Fall speed in rows per second
        """
        self.x = x
        self.y = float(y)
        self.height = height
        self.speed = speed
        self.next_update = 0.0

    def update(self, time: float) -> bool:
        """Update bullet position (moving downward).

        Args:
            time: Current time

        Returns:
            True if bullet is still on screen
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)
        self.y += 1

        return self.y < self.height

    def render(self, renderer):
        """Render bullet.

        Args:
            renderer: FullScreenRenderer instance
        """
        y = int(self.y)
        if 0 <= y < self.height:
            renderer.write_at(self.x, y, "▼", ColorPalette.BRIGHT_MAGENTA)


class PowerUp:
    """A falling pickup dropped by a destroyed enemy."""

    # type -> (token letter, color attr name, HUD label)
    TYPES = {
        "double": ("D", "BRIGHT_CYAN", "DOUBLE"),
        "spread": ("S", "BRIGHT_YELLOW", "SPREAD"),
        "plasma": ("P", "BRIGHT_MAGENTA", "PLASMA"),
        "rapid": ("R", "BRIGHT_GREEN", "RAPID"),
        "shield": ("B", "BRIGHT_BLUE", "SHIELD"),
        "life": ("L", "BRIGHT_RED", "LIFE"),
    }

    def __init__(self, kind: str, x: int, y: int, height: int):
        """Initialize power-up.

        Args:
            kind: One of TYPES keys
            x: X position (token center)
            y: Y position
            height: Terminal height
        """
        self.kind = kind
        self.x = x
        self.y = float(y)
        self.height = height
        self.speed = 4.0
        self.next_update = 0.0

    def update(self, time: float) -> bool:
        """Update power-up position (drifting down).

        Args:
            time: Current time

        Returns:
            True if still on screen
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)
        self.y += 1

        return self.y < self.height

    def render(self, renderer):
        """Render power-up token like [D].

        Args:
            renderer: FullScreenRenderer instance
        """
        letter, color_name, _label = self.TYPES[self.kind]
        color = getattr(ColorPalette, color_name)
        y = int(self.y)
        if 0 <= y < self.height:
            renderer.write_at(self.x - 1, y, f"[{letter}]", color)


class Explosion:
    """An explosion effect."""

    FRAMES = [
        ["*"],
        [" * ", "*+*", " * "],
        ["  *  ", " *** ", "**+**", " *** ", "  *  "],
        [" . . ", ". + .", " . . "],
        ["  .  ", " . . ", "  .  "],
        [".   .", "  .  ", ".   ."],
    ]

    def __init__(self, x: int, y: int):
        """Initialize explosion.

        Args:
            x: X position
            y: Y position
        """
        self.x = x
        self.y = y
        self.frame = 0
        self.next_update = 0.0
        self.speed = 12.0

    def update(self, time: float) -> bool:
        """Update explosion animation.

        Args:
            time: Current time

        Returns:
            True if explosion is still animating
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)
        self.frame += 1

        return self.frame < len(self.FRAMES)

    def render(self, renderer):
        """Render explosion.

        Args:
            renderer: FullScreenRenderer instance
        """
        if self.frame >= len(self.FRAMES):
            return

        frame = self.FRAMES[self.frame]
        colors = [
            ColorPalette.BRIGHT_WHITE,
            ColorPalette.BRIGHT_YELLOW,
            ColorPalette.YELLOW,
            ColorPalette.RED,
            ColorPalette.DIM_RED,
            ColorPalette.DIM_GREY,
        ]
        color = colors[min(self.frame, len(colors) - 1)]

        for row_idx, row in enumerate(frame):
            for col_idx, char in enumerate(row):
                if char != " ":
                    px = self.x + col_idx - len(row) // 2
                    py = self.y + row_idx - len(frame) // 2
                    renderer.write_at(px, py, char, color)


class SpaceShooterRenderer:
    """Runs and renders the playable vertical space shooter."""

    STARTING_LIVES = 3
    MAX_LIVES = 5
    INVULNERABLE_SECONDS = 2.5
    SHIELD_HIT_INVULN = 1.2
    MAX_ENEMIES = 10
    WAVE_SECONDS = 15.0
    RAPID_SECONDS = 10.0
    POWERUP_SCORE = 50

    # Weapon -> fire cooldown in seconds
    WEAPON_COOLDOWNS = {
        "single": 0.16,
        "double": 0.18,
        "spread": 0.22,
        "plasma": 0.28,
    }

    # Drop chances per enemy type, and weighted kind distribution
    DROP_CHANCE = {"invader_f": 0.12, "boss_galaga": 0.45}
    DROP_KINDS = ["double", "spread", "plasma", "rapid", "shield", "life"]
    DROP_WEIGHTS = [24, 20, 15, 20, 15, 6]

    def __init__(self, terminal_width: int, terminal_height: int):
        """Initialize space shooter game.

        Args:
            terminal_width: Terminal width in columns
            terminal_height: Terminal height in rows
        """
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height
        self.stars: List[Star] = []
        self.player: Optional[PlayerShip] = None
        self.enemies: List[Enemy] = []
        self.lasers: List[Laser] = []
        self.enemy_bullets: List[EnemyBullet] = []
        self.powerups: List[PowerUp] = []
        self.explosions: List[Explosion] = []

        self.score = 0
        self.lives = self.STARTING_LIVES
        self.wave = 1
        self.game_over = False

        # Weapon / pickup state
        self.weapon = "single"
        self.rapid_until = 0.0
        self.shield = False

        # Absolute-time bookkeeping (session time keeps running across restarts,
        # so wave progress is measured from game_start_time, set lazily)
        self.game_start_time: Optional[float] = None
        self.last_enemy_spawn = 0.0
        self.last_fire_time = -1e9
        self.fire_requested = False
        self.now = 0.0

        self._create_starfield()
        self._create_player()

    def _create_starfield(self):
        """Create initial starfield."""
        self.stars = []
        num_stars = (self.terminal_width * self.terminal_height) // 25

        for _ in range(num_stars):
            x = random.randint(0, self.terminal_width - 1)
            y = random.randint(0, self.terminal_height - 1)
            self.stars.append(Star(x, y, self.terminal_width, self.terminal_height))

    def _create_player(self):
        """Create the player ship centered near the bottom."""
        start_x = self.terminal_width // 2 - PlayerShip.SPRITE_WIDTH // 2
        start_y = self.terminal_height - 8
        self.player = PlayerShip(
            start_x, start_y, self.terminal_width, self.terminal_height
        )

    # ------------------------------------------------------------------
    # Input API (called from the plugin's handle_input)
    # ------------------------------------------------------------------

    def move_left(self):
        """Register a left key event (tap or key-repeat while held)."""
        if not self.game_over and self.player:
            self.player.press(-1, self.now)

    def move_right(self):
        """Register a right key event (tap or key-repeat while held)."""
        if not self.game_over and self.player:
            self.player.press(1, self.now)

    def fire(self):
        """Request a shot (rate-limited per weapon in update)."""
        if not self.game_over:
            self.fire_requested = True

    def restart(self):
        """Restart the game after game over."""
        if self.game_over:
            self.reset()

    # ------------------------------------------------------------------
    # Game loop
    # ------------------------------------------------------------------

    def update(self, current_time: float):
        """Update all game objects.

        Args:
            current_time: Current time for animation
        """
        self.now = current_time
        if self.game_start_time is None:
            self.game_start_time = current_time
            self.last_enemy_spawn = current_time

        # Update stars
        for star in self.stars:
            star.update(current_time)

        # Update player + wave difficulty only while playing
        if not self.game_over:
            elapsed = current_time - self.game_start_time
            self.wave = 1 + int(elapsed / self.WAVE_SECONDS)

            if self.player:
                self.player.update(current_time)

            self._handle_fire(current_time)
            self._spawn_enemies(current_time)
            self._enemy_fire(current_time)

        # Update enemies
        self.enemies = [e for e in self.enemies if e.update(current_time)]

        # Update projectiles and pickups
        self.lasers = [laser for laser in self.lasers if laser.update(current_time)]
        self.enemy_bullets = [
            b for b in self.enemy_bullets if b.update(current_time)
        ]
        self.powerups = [p for p in self.powerups if p.update(current_time)]

        # Update explosions
        self.explosions = [
            ex for ex in self.explosions if ex.update(current_time)
        ]

        # Collisions
        self._collide_lasers_with_enemies(current_time)
        if not self.game_over:
            self._collect_powerups(current_time)
            self._collide_player(current_time)

    def _fire_cooldown(self, current_time: float) -> float:
        """Current fire cooldown (weapon base, halved while rapid is live)."""
        cooldown = self.WEAPON_COOLDOWNS[self.weapon]
        if current_time < self.rapid_until:
            cooldown *= 0.5
        return cooldown

    def _handle_fire(self, current_time: float):
        """Fire the current weapon if requested and off cooldown."""
        if not self.fire_requested:
            return
        self.fire_requested = False

        if current_time - self.last_fire_time < self._fire_cooldown(current_time):
            return
        if not self.player:
            return

        nose_x = self.player.nose_x
        nose_y = int(self.player.y) - 1
        w, h = self.terminal_width, self.terminal_height

        if self.weapon == "double":
            self.lasers.append(Laser(nose_x - 2, nose_y, w, h))
            self.lasers.append(Laser(nose_x + 2, nose_y, w, h))
        elif self.weapon == "spread":
            self.lasers.append(Laser(nose_x, nose_y, w, h, dx=-0.5))
            self.lasers.append(Laser(nose_x, nose_y, w, h))
            self.lasers.append(Laser(nose_x, nose_y, w, h, dx=0.5))
        elif self.weapon == "plasma":
            self.lasers.append(
                Laser(nose_x, nose_y, w, h, damage=2, pierce_hits=3, plasma=True)
            )
        else:  # single
            self.lasers.append(Laser(nose_x, nose_y, w, h))

        self.last_fire_time = current_time

    def _spawn_enemies(self, current_time: float):
        """Spawn enemies from the top, faster as waves progress."""
        spawn_interval = max(0.4, 1.4 - 0.12 * (self.wave - 1))
        if (
            current_time - self.last_enemy_spawn < spawn_interval
            or len(self.enemies) >= self.MAX_ENEMIES
        ):
            return

        enemy_type = random.choice(["invader_f", "invader_f", "invader_f", "boss_galaga"])
        x = random.randint(5, max(6, self.terminal_width - 15))
        speed = min(18.0, random.uniform(5.0 + 0.5 * self.wave, 9.0 + 0.9 * self.wave))
        self.enemies.append(
            Enemy(
                enemy_type,
                x,
                -5,  # Start above screen
                self.terminal_width,
                self.terminal_height,
                speed,
            )
        )
        self.last_enemy_spawn = current_time

    def _enemy_fire(self, current_time: float):
        """Let enemies in the upper part of the screen shoot at the player."""
        fire_chance = min(0.012, 0.003 + 0.0012 * (self.wave - 1))
        bullet_speed = min(22.0, 13.0 + self.wave)

        for enemy in self.enemies:
            if enemy.y < 0 or enemy.y > self.terminal_height * 0.6:
                continue
            chance = fire_chance * (2.0 if enemy.enemy_type == "boss_galaga" else 1.0)
            if random.random() < chance:
                gx, gy = enemy.gun_position
                self.enemy_bullets.append(
                    EnemyBullet(gx, gy, self.terminal_height, bullet_speed)
                )

    def _maybe_drop_powerup(self, enemy: Enemy):
        """Roll a power-up drop where an enemy died."""
        if random.random() >= self.DROP_CHANCE[enemy.enemy_type]:
            return
        kind = random.choices(self.DROP_KINDS, weights=self.DROP_WEIGHTS, k=1)[0]
        self.powerups.append(
            PowerUp(
                kind,
                enemy.render_x + enemy.sprite_width // 2,
                int(enemy.y) + 2,
                self.terminal_height,
            )
        )

    def _destroy_enemy(self, enemy: Enemy):
        """Remove an enemy: explosion, score, and a possible drop."""
        self.enemies.remove(enemy)
        self.explosions.append(
            Explosion(enemy.render_x + enemy.sprite_width // 2, int(enemy.y) + 2)
        )
        self.score += enemy.score
        self._maybe_drop_powerup(enemy)

    def _collide_lasers_with_enemies(self, current_time: float):
        """Player lasers vs enemies (plasma pierces through)."""
        for laser in self.lasers[:]:
            lx = int(laser.x)
            for enemy in self.enemies[:]:
                if id(enemy) in laser.hit_ids:
                    continue
                ex = enemy.render_x
                if (
                    ex <= lx < ex + enemy.sprite_width
                    and enemy.y <= laser.y < enemy.y + 4
                ):
                    laser.hit_ids.add(id(enemy))
                    laser.pierce_hits -= 1
                    if enemy.take_hit(current_time, laser.damage):
                        self._destroy_enemy(enemy)
                    if laser.pierce_hits <= 0:
                        if laser in self.lasers:
                            self.lasers.remove(laser)
                        break

    def _collect_powerups(self, current_time: float):
        """Catch falling power-ups with the player ship."""
        if not self.player:
            return

        px0 = int(self.player.x)
        px1 = int(self.player.x) + PlayerShip.SPRITE_WIDTH - 1
        py0 = int(self.player.y)
        py1 = int(self.player.y) + PlayerShip.SPRITE_HEIGHT - 1

        for powerup in self.powerups[:]:
            if px0 <= powerup.x <= px1 and py0 <= int(powerup.y) <= py1:
                self.powerups.remove(powerup)
                self._apply_powerup(powerup.kind, current_time)

    def _apply_powerup(self, kind: str, current_time: float):
        """Apply a caught power-up."""
        if kind in ("double", "spread", "plasma"):
            self.weapon = kind
        elif kind == "rapid":
            self.rapid_until = current_time + self.RAPID_SECONDS
        elif kind == "shield":
            self.shield = True
        elif kind == "life":
            self.lives = min(self.MAX_LIVES, self.lives + 1)
        self.score += self.POWERUP_SCORE

    def _collide_player(self, current_time: float):
        """Enemy bullets and enemy ships vs the player."""
        if not self.player or self.player.is_invulnerable(current_time):
            return

        # Player core hitbox (slightly smaller than the sprite for fairness)
        px0 = int(self.player.x) + 1
        px1 = int(self.player.x) + 7
        py0 = int(self.player.y)
        py1 = int(self.player.y) + 3

        # Enemy bullets
        for bullet in self.enemy_bullets[:]:
            if px0 <= bullet.x <= px1 and py0 <= int(bullet.y) <= py1:
                self.enemy_bullets.remove(bullet)
                self._player_hit(current_time)
                return

        # Enemy ships (ramming)
        for enemy in self.enemies[:]:
            ex0 = enemy.render_x
            ex1 = ex0 + enemy.sprite_width - 1
            ey0 = int(enemy.y)
            ey1 = ey0 + 3
            if px0 <= ex1 and ex0 <= px1 and py0 <= ey1 and ey0 <= py1:
                self.enemies.remove(enemy)
                self.explosions.append(
                    Explosion(ex0 + enemy.sprite_width // 2, ey0 + 2)
                )
                self._player_hit(current_time)
                return

    def _player_hit(self, current_time: float):
        """Handle the player taking a hit (shield absorbs one)."""
        if not self.player:
            return

        if self.shield:
            self.shield = False
            self.player.invulnerable_until = current_time + self.SHIELD_HIT_INVULN
            return

        self.explosions.append(
            Explosion(self.player.nose_x, int(self.player.y) + 2)
        )
        self.lives -= 1

        # Losing the ship loses its upgrades
        self.weapon = "single"
        self.rapid_until = 0.0

        if self.lives <= 0:
            self.game_over = True
        else:
            self.player.invulnerable_until = (
                current_time + self.INVULNERABLE_SECONDS
            )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, renderer):
        """Render all game objects.

        Args:
            renderer: FullScreenRenderer instance
        """
        # Clear screen
        renderer.clear_screen()

        # Render stars (background)
        for star in self.stars:
            star.render(renderer)

        # Render enemies (from top)
        for enemy in self.enemies:
            enemy.render(renderer, self.now)

        # Render projectiles and pickups
        for laser in self.lasers:
            laser.render(renderer)
        for bullet in self.enemy_bullets:
            bullet.render(renderer)
        for powerup in self.powerups:
            powerup.render(renderer)

        # Render player (at bottom)
        if self.player and not self.game_over:
            self.player.render(renderer, self.now, shield=self.shield)

        # Render explosions
        for explosion in self.explosions:
            explosion.render(renderer)

        # Render HUD
        self._render_hud(renderer)

        if self.game_over:
            self._render_game_over(renderer)

    def _render_hud(self, renderer):
        """Render heads-up display.

        Args:
            renderer: FullScreenRenderer instance
        """
        # Title
        title = "SPACE SQUADRON"
        renderer.write_at(
            self.terminal_width // 2 - len(title) // 2,
            0,
            title,
            ColorPalette.BRIGHT_CYAN,
        )

        # Score + wave
        score_text = f"SCORE: {self.score:06d}  WAVE {self.wave}"
        renderer.write_at(2, 0, score_text, ColorPalette.BRIGHT_GREEN)

        # Lives
        lives_text = "LIVES " + "▲" * max(0, self.lives)
        renderer.write_at(
            self.terminal_width - len(lives_text) - 2,
            0,
            lives_text,
            ColorPalette.BRIGHT_YELLOW,
        )

        # Bottom row: weapon (left), controls (center), pickups (right)
        bottom = self.terminal_height - 1

        weapon_colors = {
            "single": ColorPalette.BRIGHT_WHITE,
            "double": ColorPalette.BRIGHT_CYAN,
            "spread": ColorPalette.BRIGHT_YELLOW,
            "plasma": ColorPalette.BRIGHT_MAGENTA,
        }
        weapon_text = f"WPN {self.weapon.upper()}"
        renderer.write_at(2, bottom, weapon_text, weapon_colors[self.weapon])

        instructions = "HOLD ◀ ▶ MOVE   SPACE FIRE   Q QUIT"
        renderer.write_at(
            self.terminal_width // 2 - len(instructions) // 2,
            bottom,
            instructions,
            ColorPalette.DIM_GREY,
        )

        status_bits = []
        if self.now < self.rapid_until:
            status_bits.append(f"RAPID {max(0, int(self.rapid_until - self.now))}s")
        if self.shield:
            status_bits.append("SHIELD")
        if status_bits:
            status_text = "  ".join(status_bits)
            renderer.write_at(
                self.terminal_width - len(status_text) - 2,
                bottom,
                status_text,
                ColorPalette.BRIGHT_BLUE,
            )

    def _render_game_over(self, renderer):
        """Render the game over overlay.

        Args:
            renderer: FullScreenRenderer instance
        """
        center_y = self.terminal_height // 2

        lines = [
            ("G A M E   O V E R", ColorPalette.BRIGHT_RED),
            (f"FINAL SCORE: {self.score:06d}   WAVE {self.wave}", ColorPalette.BRIGHT_YELLOW),
            ("PRESS R TO RESTART - Q TO QUIT", ColorPalette.GREY),
        ]

        for offset, (text, color) in enumerate(lines):
            renderer.write_at(
                self.terminal_width // 2 - len(text) // 2,
                center_y - 1 + offset * 2,
                text,
                color,
            )

    def reset(self):
        """Reset to a fresh game."""
        self._create_starfield()
        self._create_player()
        self.enemies = []
        self.lasers = []
        self.enemy_bullets = []
        self.powerups = []
        self.explosions = []
        self.score = 0
        self.lives = self.STARTING_LIVES
        self.wave = 1
        self.game_over = False
        self.weapon = "single"
        self.rapid_until = 0.0
        self.shield = False
        self.game_start_time = None
        self.last_enemy_spawn = 0.0
        self.last_fire_time = -1e9
        self.fire_requested = False
