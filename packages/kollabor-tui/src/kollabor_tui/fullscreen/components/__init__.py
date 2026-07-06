"""Full-screen plugin components and utilities."""

from .animation import AnimationFramework
from .drawing import DrawingPrimitives
from .matrix_components import MatrixColumn, MatrixRenderer
from .space_shooter_components import (
    Enemy,
    EnemyBullet,
    Explosion,
    Laser,
    PlayerShip,
    PowerUp,
    SpaceShooterRenderer,
    Star,
)

__all__ = [
    "DrawingPrimitives",
    "AnimationFramework",
    "MatrixColumn",
    "MatrixRenderer",
    "Star",
    "PlayerShip",
    "Enemy",
    "EnemyBullet",
    "Laser",
    "Explosion",
    "PowerUp",
    "SpaceShooterRenderer",
]
