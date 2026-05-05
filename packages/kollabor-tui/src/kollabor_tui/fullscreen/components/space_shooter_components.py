"""Space shooter components for the full-screen framework.

Retro 80s arcade-style vertical space shooter demo with ships flying upward through starfield.
Classic Galaga-style vertical scrolling.
"""

import random
from typing import List

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


class Ship:
    """A player ship with banking animations for vertical flight."""

    # Ship sprites - pointing upward
    SPRITES = {
        "viper": {
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
        },
        "falcon": {
            "straight": [
                "    █    ",
                "   ▟█▙   ",
                "  ▟███▙  ",
                " █▀███▀█ ",
            ],
            "bank_right": [
                "     █   ",
                "    ▟█▙  ",
                "   ▟███▙▄",
                "  █▀███▀█",
            ],
            "bank_left": [
                "   █     ",
                "  ▟█▙    ",
                "▄▟███▙   ",
                "█▀███▀█  ",
            ],
        },
        "interceptor": {
            "straight": [
                "    █    ",
                "   ▟█▙   ",
                "  █████  ",
                " █▀ █ ▀█ ",
            ],
            "bank_right": [
                "     █   ",
                "    ▟█▙  ",
                "   █████▄",
                "  █▀ █ ▀█",
            ],
            "bank_left": [
                "   █     ",
                "  ▟█▙    ",
                "▄█████   ",
                "█▀ █ ▀█  ",
            ],
        },
    }

    def __init__(
        self, ship_type: str, start_x: int, start_y: int, width: int, height: int
    ):
        """Initialize ship.

        Args:
            ship_type: Type of ship ('viper', 'falcon', 'interceptor')
            start_x: Starting X position
            start_y: Starting Y position
            width: Terminal width
            height: Terminal height
        """
        self.ship_type = ship_type
        self.x = float(start_x)
        self.y = float(start_y)
        self.width = width
        self.height = height
        self.state = "straight"
        self.target_x = float(start_x)
        self.next_update = 0.0
        self.maneuver_timer = 0.0
        self.maneuver_duration = 0.0

        # Engine exhaust animation
        self.exhaust_frame = 0
        self.exhaust_chars = ["▒", "▓", "█", "▓"]

    def start_maneuver(self, time: float):
        """Start a random maneuver (dodge left or right).

        Args:
            time: Current time
        """
        maneuver = random.choice(["dodge_left", "dodge_right", "straight", "straight"])

        if maneuver == "dodge_left":
            self.target_x = max(2, self.x - random.randint(5, 15))
            self.state = "bank_left"
            self.maneuver_duration = 1.2
        elif maneuver == "dodge_right":
            self.target_x = min(self.width - 12, self.x + random.randint(5, 15))
            self.state = "bank_right"
            self.maneuver_duration = 1.2
        else:
            self.state = "straight"
            self.maneuver_duration = 0.8

        self.maneuver_timer = time + self.maneuver_duration

    def update(self, time: float) -> bool:
        """Update ship position and state.

        Args:
            time: Current time

        Returns:
            True to continue
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / 30.0)  # 30 FPS for smooth movement

        # Move horizontally towards target
        if abs(self.x - self.target_x) > 0.5:
            if self.x < self.target_x:
                self.x += 0.4
            else:
                self.x -= 0.4
        else:
            # Reached target, straighten out
            if self.state != "straight" and time > self.maneuver_timer:
                self.state = "straight"

        # Check if maneuver is done
        if time > self.maneuver_timer:
            # Random chance to start new maneuver
            if random.random() < 0.025:
                self.start_maneuver(time)

        # Update exhaust animation
        self.exhaust_frame = (self.exhaust_frame + 1) % len(self.exhaust_chars)

        return True

    def render(self, renderer):
        """Render ship.

        Args:
            renderer: FullScreenRenderer instance
        """
        sprites = self.SPRITES.get(self.ship_type, self.SPRITES["viper"])
        sprite = sprites.get(self.state, sprites["straight"])

        x = int(self.x)
        y = int(self.y)

        # Ship colors
        ship_color = ColorPalette.BRIGHT_CYAN

        for row_idx, row in enumerate(sprite):
            for col_idx, char in enumerate(row):
                if char != " ":
                    px = x + col_idx
                    py = y + row_idx
                    if 0 <= px < self.width and 0 <= py < self.height:
                        renderer.write_at(px, py, char, ship_color)

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

    def __init__(self, enemy_type: str, x: int, y: int, width: int, height: int):
        """Initialize enemy.

        Args:
            enemy_type: Type of enemy ('invader_f', 'boss_galaga')
            x: X position
            y: Y position (starts negative, above screen)
            width: Terminal width
            height: Terminal height
        """
        self.enemy_type = enemy_type
        self.x = float(x)
        self.y = float(y)
        self.width = width
        self.height = height
        self.speed = random.uniform(6.0, 12.0)
        self.next_update = 0.0
        self.wobble = 0.0
        self.wobble_dir = 1

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

    def render(self, renderer):
        """Render enemy.

        Args:
            renderer: FullScreenRenderer instance
        """
        sprite = self.SPRITES.get(self.enemy_type, self.SPRITES["invader_f"])

        x = int(self.x + self.wobble)
        y = int(self.y)

        # Enemy colors
        if self.enemy_type == "boss_galaga":
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
    """A laser projectile firing upward."""

    def __init__(self, x: int, y: int, height: int):
        """Initialize laser.

        Args:
            x: X position
            y: Y position
            height: Terminal height
        """
        self.x = x
        self.y = float(y)
        self.height = height
        self.speed = 50.0
        self.next_update = 0.0
        self.chars = ["│", "║", "┃", "║"]
        self.frame = 0

    def update(self, time: float) -> bool:
        """Update laser position (moving upward).

        Args:
            time: Current time

        Returns:
            True if laser is still on screen
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)
        self.y -= 1  # Move upward
        self.frame = (self.frame + 1) % len(self.chars)

        return self.y >= 0

    def render(self, renderer):
        """Render laser.

        Args:
            renderer: FullScreenRenderer instance
        """
        y = int(self.y)
        char = self.chars[self.frame]

        # Draw laser trail (vertical)
        for i in range(3):
            py = y + i  # Trail below the head
            if 0 <= py < self.height:
                if i == 0:
                    color = ColorPalette.BRIGHT_WHITE
                elif i == 1:
                    color = ColorPalette.BRIGHT_CYAN
                else:
                    color = ColorPalette.CYAN
                renderer.write_at(self.x, py, char, color)


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
    """Renders the complete vertical space shooter demo."""

    def __init__(self, terminal_width: int, terminal_height: int):
        """Initialize space shooter renderer.

        Args:
            terminal_width: Terminal width in columns
            terminal_height: Terminal height in rows
        """
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height
        self.stars: List[Star] = []
        self.ships: List[Ship] = []
        self.enemies: List[Enemy] = []
        self.lasers: List[Laser] = []
        self.explosions: List[Explosion] = []
        self.start_time = 0.0
        self.last_enemy_spawn = 0.0
        self.last_laser_time = 0.0
        self.score = 0

        self._create_starfield()
        self._create_ships()

    def _create_starfield(self):
        """Create initial starfield."""
        self.stars = []
        num_stars = (self.terminal_width * self.terminal_height) // 25

        for _ in range(num_stars):
            x = random.randint(0, self.terminal_width - 1)
            y = random.randint(0, self.terminal_height - 1)
            self.stars.append(Star(x, y, self.terminal_width, self.terminal_height))

    def _create_ships(self):
        """Create the three player ships at the bottom."""
        self.ships = []
        ship_types = ["viper", "falcon", "interceptor"]

        # Ships positioned at bottom, spread horizontally
        ship_y = self.terminal_height - 8  # Near bottom

        for i, ship_type in enumerate(ship_types):
            # Spread ships across the width
            x = (self.terminal_width // 4) * (i + 1) - 5
            ship = Ship(ship_type, x, ship_y, self.terminal_width, self.terminal_height)
            self.ships.append(ship)

    def update(self, current_time: float):
        """Update all game objects.

        Args:
            current_time: Current time for animation
        """
        # Update stars
        for star in self.stars:
            star.update(current_time)

        # Update ships
        for ship in self.ships:
            ship.update(current_time)

        # Update enemies
        active_enemies = []
        for enemy in self.enemies:
            if enemy.update(current_time):
                active_enemies.append(enemy)
        self.enemies = active_enemies

        # Update lasers
        active_lasers = []
        for laser in self.lasers:
            if laser.update(current_time):
                active_lasers.append(laser)
        self.lasers = active_lasers

        # Update explosions
        active_explosions = []
        for explosion in self.explosions:
            if explosion.update(current_time):
                active_explosions.append(explosion)
        self.explosions = active_explosions

        # Spawn enemies from top
        if current_time - self.last_enemy_spawn > 2.0 and len(self.enemies) < 8:
            if random.random() < 0.04:
                enemy_type = random.choice(["invader_f", "invader_f", "boss_galaga"])
                x = random.randint(5, self.terminal_width - 15)
                enemy = Enemy(
                    enemy_type,
                    x,
                    -5,  # Start above screen
                    self.terminal_width,
                    self.terminal_height,
                )
                self.enemies.append(enemy)
                self.last_enemy_spawn = current_time

        # Ships fire lasers upward
        if current_time - self.last_laser_time > 0.3:
            for ship in self.ships:
                if random.random() < 0.05:
                    # Fire from top center of ship
                    laser = Laser(
                        int(ship.x) + 4, int(ship.y) - 1, self.terminal_height
                    )
                    self.lasers.append(laser)
                    self.last_laser_time = current_time

        # Check for laser-enemy collisions
        for laser in self.lasers[:]:
            for enemy in self.enemies[:]:
                if abs(laser.x - (enemy.x + 4)) < 4 and abs(laser.y - enemy.y) < 4:
                    # Explosion!
                    self.explosions.append(
                        Explosion(int(enemy.x) + 4, int(enemy.y) + 2)
                    )
                    if laser in self.lasers:
                        self.lasers.remove(laser)
                    if enemy in self.enemies:
                        self.enemies.remove(enemy)
                    self.score += 100 if enemy.enemy_type == "invader_f" else 250
                    break

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
            enemy.render(renderer)

        # Render lasers
        for laser in self.lasers:
            laser.render(renderer)

        # Render ships (at bottom)
        for ship in self.ships:
            ship.render(renderer)

        # Render explosions
        for explosion in self.explosions:
            explosion.render(renderer)

        # Render HUD
        self._render_hud(renderer)

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

        # Score
        score_text = f"SCORE: {self.score:06d}"
        renderer.write_at(2, 0, score_text, ColorPalette.BRIGHT_GREEN)

        # Instructions
        instructions = "Press Q or ESC to exit"
        renderer.write_at(
            self.terminal_width // 2 - len(instructions) // 2,
            self.terminal_height - 1,
            instructions,
            ColorPalette.DIM_GREY,
        )

    def reset(self):
        """Reset the space shooter demo."""
        self._create_starfield()
        self._create_ships()
        self.enemies = []
        self.lasers = []
        self.explosions = []
        self.start_time = 0
        self.last_enemy_spawn = 0
        self.last_laser_time = 0
        self.score = 0
