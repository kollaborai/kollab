"""Tests for the playable space shooter game logic.

Drives SpaceShooterRenderer headlessly with a fake renderer:
- player movement and edge clamping
- firing with cooldown
- laser vs enemy collisions, boss hitpoints, scoring
- enemy bullet vs player, invulnerability window
- game over and restart flow
"""

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add packages/kollabor-tui to path for imports
sys.path.insert(
    0,
    str(
        Path(__file__).parent.parent.parent.parent / "packages" / "kollabor-tui" / "src"
    ),
)

from kollabor_tui.fullscreen.components.space_shooter_components import (
    Enemy,
    EnemyBullet,
    PowerUp,
    SpaceShooterRenderer,
)

WIDTH = 80
HEIGHT = 40


class FakeRenderer:
    """Captures write_at calls instead of touching the terminal."""

    def __init__(self):
        self.writes = []

    def clear_screen(self):
        self.writes = []

    def write_at(self, x, y, text, color=None):
        self.writes.append((x, y, text))

    def rendered_text(self):
        return " ".join(str(w[2]) for w in self.writes)


def make_game():
    return SpaceShooterRenderer(WIDTH, HEIGHT)


class TestPlayerMovement(unittest.TestCase):
    def test_move_left_and_right(self):
        game = make_game()
        start_x = game.player.x
        game.move_left()
        # Glide toward target over a few ticks
        for i in range(10):
            game.update(0.1 * i)
        self.assertLess(game.player.x, start_x)

        x_after_left = game.player.x
        game.move_right()
        game.move_right()
        for i in range(10, 25):
            game.update(0.1 * i)
        self.assertGreater(game.player.x, x_after_left)

    def test_clamped_to_screen(self):
        game = make_game()
        for _ in range(100):
            game.move_left()
        self.assertGreaterEqual(game.player.target_x, 1.0)

        for _ in range(100):
            game.move_right()
        self.assertLessEqual(game.player.target_x, WIDTH - 10)

    def test_banking_state_follows_direction(self):
        game = make_game()
        game.move_left()
        game.update(0.0)
        game.update(0.1)
        self.assertEqual(game.player.state, "bank_left")


class TestFiring(unittest.TestCase):
    def test_fire_spawns_laser(self):
        game = make_game()
        game.fire()
        game.update(0.0)
        self.assertEqual(len(game.lasers), 1)
        self.assertEqual(game.lasers[0].x, game.player.nose_x)

    def test_fire_cooldown_limits_rate(self):
        game = make_game()
        game.fire()
        game.update(0.0)
        game.fire()
        game.update(0.05)  # within cooldown
        self.assertEqual(len(game.lasers), 1)
        game.fire()
        game.update(0.5)  # past cooldown
        self.assertEqual(len(game.lasers), 2)

    def test_fire_ignored_after_game_over(self):
        game = make_game()
        game.update(0.0)
        game.game_over = True
        game.fire()
        game.update(1.0)
        self.assertEqual(len(game.lasers), 0)


def make_quiet_game():
    """Game with background spawning and enemy fire disabled (deterministic)."""
    game = make_game()
    game._spawn_enemies = lambda t: None
    game._enemy_fire = lambda t: None
    return game


class TestCollisions(unittest.TestCase):
    def test_laser_destroys_invader_and_scores(self):
        game = make_quiet_game()
        game.update(0.0)
        enemy = Enemy("invader_f", 30, 10, WIDTH, HEIGHT, speed=6.0)
        enemy.wobble = 0
        game.enemies = [enemy]
        # Place a laser inside the enemy sprite box
        game.fire()
        game.update(0.01)
        game.lasers[0].x = 33
        game.lasers[0].y = 11.0
        game.update(0.02)
        self.assertEqual(len(game.enemies), 0)
        self.assertEqual(game.score, 100)
        self.assertEqual(len(game.explosions), 1)
        self.assertEqual(len(game.lasers), 0)

    def test_boss_takes_three_hits(self):
        game = make_quiet_game()
        game.update(0.0)
        boss = Enemy("boss_galaga", 30, 10, WIDTH, HEIGHT, speed=0.001)
        boss.wobble = 0
        game.enemies = [boss]

        for hit, expected_alive in [(1, 1), (2, 1), (3, 0)]:
            game.fire()
            game.update(hit * 1.0)
            self.assertEqual(len(game.lasers), 1, f"laser missing for hit {hit}")
            game.lasers[0].x = 33
            game.lasers[0].y = 11.0
            game.update(hit * 1.0 + 0.01)
            self.assertEqual(len(game.enemies), expected_alive, f"after hit {hit}")

        self.assertEqual(game.score, 400)

    def test_enemy_bullet_hits_player(self):
        game = make_quiet_game()
        game.update(0.0)
        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.1)
        self.assertEqual(game.lives, 2)
        self.assertTrue(game.player.is_invulnerable(0.2))
        self.assertEqual(len(game.enemy_bullets), 0)

    def test_invulnerability_prevents_chain_hits(self):
        game = make_quiet_game()
        game.update(0.0)
        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.1)
        self.assertEqual(game.lives, 2)
        # Second bullet lands during the invulnerability window
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.2)
        self.assertEqual(game.lives, 2)

    def test_ramming_enemy_costs_life_and_destroys_enemy(self):
        game = make_quiet_game()
        game.update(0.0)
        enemy = Enemy(
            "invader_f", int(game.player.x), int(game.player.y), WIDTH, HEIGHT,
            speed=0.001,
        )
        enemy.wobble = 0
        game.enemies = [enemy]
        game.update(0.1)
        self.assertEqual(game.lives, 2)
        self.assertEqual(len(game.enemies), 0)


class TestGameOverAndRestart(unittest.TestCase):
    def _kill_player(self, game, at_time):
        game.player.invulnerable_until = 0.0
        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(at_time)

    def test_three_hits_end_the_game(self):
        game = make_quiet_game()
        game.update(0.0)
        for i in range(3):
            self._kill_player(game, 1.0 + i)
        self.assertTrue(game.game_over)
        self.assertEqual(game.lives, 0)

    def test_restart_resets_state(self):
        game = make_quiet_game()
        game.update(0.0)
        game.score = 1200
        for i in range(3):
            self._kill_player(game, 1.0 + i)
        self.assertTrue(game.game_over)

        game.restart()
        self.assertFalse(game.game_over)
        self.assertEqual(game.score, 0)
        self.assertEqual(game.lives, 3)
        self.assertEqual(game.enemies, [])
        # Wave clock restarts from the next update tick
        game.update(100.0)
        self.assertEqual(game.wave, 1)

    def test_restart_ignored_mid_game(self):
        game = make_game()
        game.update(0.0)
        game.score = 500
        game.restart()
        self.assertEqual(game.score, 500)


class TestSpawningAndWaves(unittest.TestCase):
    def test_enemies_spawn_over_time(self):
        random.seed(1234)
        game = make_game()
        t = 0.0
        for _ in range(200):
            t += 0.05
            game.update(t)
        self.assertGreater(len(game.enemies), 0)

    def test_enemy_cap_respected(self):
        random.seed(1234)
        game = make_game()
        t = 0.0
        for _ in range(2000):
            t += 0.05
            game.update(t)
            self.assertLessEqual(len(game.enemies), game.MAX_ENEMIES)

    def test_wave_advances_with_time(self):
        game = make_game()
        game.update(0.0)
        game.update(game.WAVE_SECONDS + 1.0)
        self.assertEqual(game.wave, 2)


class TestRendering(unittest.TestCase):
    def test_render_playing_draws_hud_and_player(self):
        game = make_game()
        game.update(0.0)
        fake = FakeRenderer()
        game.render(fake)
        text = fake.rendered_text()
        self.assertIn("SCORE: 000000", text)
        self.assertIn("SPACE SQUADRON", text)
        self.assertIn("LIVES", text)

    def test_render_game_over_overlay(self):
        game = make_game()
        game.update(0.0)
        game.game_over = True
        fake = FakeRenderer()
        game.render(fake)
        text = fake.rendered_text()
        self.assertIn("G A M E   O V E R", text)
        self.assertIn("PRESS R TO RESTART", text)

    def test_long_session_render_stability(self):
        game = make_game()
        fake = FakeRenderer()
        t = 0.0
        for _ in range(500):
            t += 0.033
            game.update(t)
            game.render(fake)


class TestHoldToMove(unittest.TestCase):
    def test_tap_nudges_fixed_distance(self):
        game = make_quiet_game()
        game.update(0.0)
        start_x = game.player.x
        game.move_left()
        t = 0.0
        while t < 1.5:
            t += 0.03
            game.update(t)
        moved = start_x - game.player.x
        self.assertGreaterEqual(moved, 2.0)
        self.assertLessEqual(moved, 4.0)

    def test_hold_engages_continuous_movement(self):
        game = make_quiet_game()
        game.update(0.0)
        start_x = game.player.x
        # Simulate OS key-repeat: a press every 30ms while held
        t = 0.0
        while t < 0.8:
            t += 0.03
            game.move_left()
            game.update(t)
        self.assertLess(game.player.x, start_x - 12)

    def test_hold_stops_when_repeats_stop(self):
        game = make_quiet_game()
        game.update(0.0)
        # Hold for 0.3s
        t = 0.0
        while t < 0.3:
            t += 0.03
            game.move_right()
            game.update(t)
        # Release: no more presses, let the glide settle
        while t < 1.0:
            t += 0.03
            game.update(t)
        settled_x = game.player.x
        # Keep updating: position must not drift further
        while t < 2.0:
            t += 0.03
            game.update(t)
        self.assertEqual(game.player.x, settled_x)


class TestWeapons(unittest.TestCase):
    def test_double_fires_two_parallel(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "double"
        game.fire()
        game.update(0.01)
        self.assertEqual(len(game.lasers), 2)
        xs = sorted(laser.x for laser in game.lasers)
        nose = game.player.nose_x
        self.assertEqual(xs, [nose - 2, nose + 2])

    def test_spread_fires_three_with_drift(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "spread"
        game.fire()
        game.update(0.01)
        self.assertEqual(len(game.lasers), 3)
        self.assertEqual(
            sorted(laser.dx for laser in game.lasers), [-0.5, 0.0, 0.5]
        )

    def test_plasma_pierces_stacked_enemies(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "plasma"
        low = Enemy("invader_f", 30, 10, WIDTH, HEIGHT, speed=0.001)
        high = Enemy("invader_f", 30, 6, WIDTH, HEIGHT, speed=0.001)
        low.wobble = high.wobble = 0
        game.enemies = [low, high]

        game.fire()
        game.update(0.01)
        # Enemies take one free move on their first tick (10->11, 6->7), and
        # the laser ascends one row per tick, so start it below the low enemy
        game.lasers[0].x = 33.0
        game.lasers[0].y = 12.0
        t = 0.01
        for _ in range(20):
            t += 0.03
            game.update(t)
        self.assertEqual(len(game.enemies), 0)
        self.assertEqual(game.score, 200)

    def test_plasma_hits_boss_for_two(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "plasma"
        boss = Enemy("boss_galaga", 30, 10, WIDTH, HEIGHT, speed=0.001)
        boss.wobble = 0
        game.enemies = [boss]

        game.fire()
        game.update(0.01)
        game.lasers[0].x = 33.0
        game.lasers[0].y = 11.0
        game.update(0.02)
        self.assertEqual(len(game.enemies), 1)
        self.assertEqual(boss.hp, 1)

    def test_rapid_halves_cooldown(self):
        game = make_quiet_game()
        game.update(0.0)
        game.rapid_until = 1e9
        game.fire()
        game.update(1.0)
        game.fire()
        game.update(1.09)  # 0.09s later: allowed at 0.08 rapid cooldown
        self.assertEqual(len(game.lasers), 2)

    def test_single_cooldown_without_rapid(self):
        game = make_quiet_game()
        game.update(0.0)
        game.fire()
        game.update(1.0)
        game.fire()
        game.update(1.09)  # 0.09s later: blocked at 0.16 base cooldown
        self.assertEqual(len(game.lasers), 1)


class TestPowerUps(unittest.TestCase):
    def _drop_on_player(self, game, kind):
        px = int(game.player.x) + 4
        py = int(game.player.y) + 1
        game.powerups = [PowerUp(kind, px, py, HEIGHT)]

    def test_catch_weapon_powerup(self):
        game = make_quiet_game()
        game.update(0.0)
        self._drop_on_player(game, "spread")
        game.update(0.1)
        self.assertEqual(game.weapon, "spread")
        self.assertEqual(game.score, game.POWERUP_SCORE)
        self.assertEqual(game.powerups, [])

    def test_shield_absorbs_one_hit(self):
        game = make_quiet_game()
        game.update(0.0)
        self._drop_on_player(game, "shield")
        game.update(0.1)
        self.assertTrue(game.shield)

        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.2)
        self.assertEqual(game.lives, 3)
        self.assertFalse(game.shield)
        self.assertTrue(game.player.is_invulnerable(0.3))

    def test_life_powerup_caps_at_max(self):
        game = make_quiet_game()
        game.update(0.0)
        game.lives = game.MAX_LIVES
        self._drop_on_player(game, "life")
        game.update(0.1)
        self.assertEqual(game.lives, game.MAX_LIVES)

    def test_life_powerup_adds_life(self):
        game = make_quiet_game()
        game.update(0.0)
        self._drop_on_player(game, "life")
        game.update(0.1)
        self.assertEqual(game.lives, 4)

    def test_hit_resets_weapon_and_rapid(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "plasma"
        game.rapid_until = 1e9
        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.1)
        self.assertEqual(game.lives, 2)
        self.assertEqual(game.weapon, "single")
        self.assertEqual(game.rapid_until, 0.0)

    def test_kill_drops_powerup_when_roll_hits(self):
        game = make_quiet_game()
        game.update(0.0)
        enemy = Enemy("invader_f", 30, 10, WIDTH, HEIGHT, speed=6.0)
        enemy.wobble = 0
        game.enemies = [enemy]
        game.fire()
        game.update(0.01)
        game.lasers[0].x = 33.0
        game.lasers[0].y = 11.0
        with mock.patch("random.random", return_value=0.0), mock.patch(
            "random.choices", return_value=["double"]
        ):
            game.update(0.02)
        self.assertEqual(len(game.enemies), 0)
        self.assertEqual(len(game.powerups), 1)
        self.assertEqual(game.powerups[0].kind, "double")

    def test_restart_clears_powerup_state(self):
        game = make_quiet_game()
        game.update(0.0)
        game.weapon = "spread"
        game.shield = True
        game.rapid_until = 1e9
        game.powerups = [PowerUp("life", 10, 10, HEIGHT)]
        game.lives = 1
        px = int(game.player.x) + 3
        py = int(game.player.y) + 1
        game.player.invulnerable_until = 0.0
        game.shield = False
        game.enemy_bullets = [EnemyBullet(px, py, HEIGHT, speed=0.001)]
        game.update(0.1)
        self.assertTrue(game.game_over)

        game.restart()
        self.assertEqual(game.weapon, "single")
        self.assertFalse(game.shield)
        self.assertEqual(game.rapid_until, 0.0)
        self.assertEqual(game.powerups, [])

    def test_hud_shows_weapon_name(self):
        game = make_quiet_game()
        game.update(0.0)
        fake = FakeRenderer()
        game.render(fake)
        self.assertIn("WPN SINGLE", fake.rendered_text())

        game.weapon = "spread"
        game.render(fake)
        self.assertIn("WPN SPREAD", fake.rendered_text())


if __name__ == "__main__":
    unittest.main()
