---
name: database-design
description: "Database Design skill - schema design and migrations"
---

database-design mode: DATA PERSISTENCE DONE RIGHT

when this skill is active, you follow disciplined database design practices.
this is a comprehensive guide to schema design and database migrations.


PHASE 0: ENVIRONMENT PREREQUISITES VERIFICATION

before designing ANY schema, verify your database environment is ready.


check database client

  <terminal>psql --version 2>/dev/null || echo "postgresql client not installed"</terminal>

  <terminal>sqlite3 --version 2>/dev/null || echo "sqlite3 not installed"</terminal>

  <terminal>mysql --version 2>/dev/null || echo "mysql client not installed"</terminal>

install based on your database:
  <terminal>brew install postgresql</terminal>  # macOS
  <terminal>apt-get install postgresql-client</terminal>  # ubuntu
  <terminal>pip install psycopg2-binary</terminal>  # python driver


check orm setup

  <terminal>python -c "import sqlalchemy; print(sqlalchemy.__version__)"</terminal>

if not installed:
  <terminal>pip install sqlalchemy</terminal>


check alembic for migrations

  <terminal>python -c "import alembic; print(alembic.__version__)"</terminal>

if not installed:
  <terminal>pip install alembic</terminal>


check database connection

  <terminal>cat .env 2>/dev/null | grep -i database || echo "no database config in .env"</terminal>

  <terminal>echo $DATABASE_URL 2>/dev/null || echo "DATABASE_URL not set"</terminal>


verify existing migrations

  <terminal>ls -la migrations/ 2>/dev/null || echo "no migrations directory"</terminal>

  <terminal>ls -la alembic/versions/ 2>/dev/null || echo "no alembic versions"</terminal>


check for schema visualization tools

  <terminal>which dbdiagram.io 2>/dev/null || echo "consider schema viz tools"</terminal>

  <terminal>pip install eralchemy 2>/dev/null || echo "eralchemy for schema diagrams"</terminal>


PHASE 1: DATA MODELING FUNDAMENTALS


understand the domain

before touching a database, answer these questions:

  [ ] what are the core entities?
  [ ] what are the relationships between them?
  [ ] what data needs to be persisted?
  [ ] what are the query patterns?
  [ ] what are the data volume estimates?
  [ ] what are the consistency requirements?

domain modeling checklist:
  - identify nouns as potential entities
  - identify verbs as relationships
  - identify adjectives as attributes
  - consider the lifecycle of each entity


normalize your data

normalization eliminates redundancy and prevents anomalies:

  first normal form (1NF):
    - eliminate repeating groups
    - each cell contains atomic values
    - each record is unique

  second normal form (2NF):
    - must be in 1NF
    - eliminate partial dependencies
    - all non-key attributes depend on the entire primary key

  third normal form (3NF):
    - must be in 2NF
    - eliminate transitive dependencies
    - non-key attributes depend only on the primary key

  boyce-codd normal form (BCNF):
    - stronger version of 3NF
    - every determinant is a candidate key


when to denormalize

denormalize for:
  - read-heavy workloads
  - reporting and analytics
  - frequently accessed data
  - reducing join complexity

denormalize sparingly and deliberately.
document every denormalization decision.


PHASE 2: IDENTIFYING ENTITIES AND ATTRIBUTES


entity definition

each entity should have:
  - a clear purpose
  - a primary key
  - a set of attributes
  - relationships to other entities

  <create>
  <file>src/models/user.py</file>
  <content>
  """User entity model."""
  from sqlalchemy import Column, Integer, String, DateTime, Boolean
  from sqlalchemy.orm import declarative_base
  from datetime import datetime

  Base = declarative_base()


  class User(Base):
      """User entity representing application users."""

      __tablename__ = "users"

      # primary key - always required
      id = Column(Integer, primary_key=True, autoincrement=True)

      # required attributes
      email = Column(String(255), nullable=False, unique=True, index=True)
      username = Column(String(50), nullable=False, unique=True, index=True)
      password_hash = Column(String(255), nullable=False)

      # optional attributes
      full_name = Column(String(100))
      avatar_url = Column(String(500))
      bio = Column(String(500))

      # status fields
      is_active = Column(Boolean, default=True, nullable=False)
      is_verified = Column(Boolean, default=False, nullable=False)

      # timestamps
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      last_login_at = Column(DateTime)

      def __repr__(self):
          return f"<User(id={self.id}, username={self.username})>"
  </content>
  </create>


attribute types guide

  integers:
    - id fields: Integer or BigInteger
    - counts: Integer, default 0
    - enums: Integer with check constraint

  strings:
    - short names: String(50-100)
    - emails: String(255), add index
    - urls: String(500-2000)
    - rich text: Text or Text(n)

  dates:
    - created_at: DateTime, default=datetime.utcnow
    - updated_at: DateTime, onupdate=datetime.utcnow
    - dates only: Date

  decimals:
    - money: Numeric(10, 2) or Numeric(19, 4)
    - percentages: Numeric(5, 2)

  booleans:
    - flags: Boolean, default=False
    - nullable booleans for tri-state


naming conventions

be consistent:
  - table names: plural, snake_case (users, user_profiles)
  - column names: snake_case (created_at, is_active)
  - primary keys: id or {table}_id
  - foreign keys: {related_table}_id (user_id, organization_id)
  - indexes: idx_{table}_{columns} (idx_users_email)
  - unique constraints: uq_{table}_{columns}
  - foreign key constraints: fk_{table}_{ref_table}


PHASE 3: RELATIONSHIPS AND FOREIGN KEYS


one-to-many relationship

  <create>
  <file>src/models/post.py</file>
  <content>
  """Post entity with relationships."""
  from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
  from sqlalchemy.orm import relationship

  from .user import Base, User


  class Post(Base):
      """Blog post authored by a user."""

      __tablename__ = "posts"

      id = Column(Integer, primary_key=True, autoincrement=True)
      title = Column(String(200), nullable=False)
      slug = Column(String(200), nullable=False, unique=True, index=True)
      content = Column(Text, nullable=False)

      # foreign key to users (many-to-one)
      author_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

      # timestamps
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      published_at = Column(DateTime)

      # relationships
      author = relationship("User", back_populates="posts")

      def __repr__(self):
          return f"<Post(id={self.id}, title={self.title})>"
  </content>
  </create>


add back-reference on User

  <read><file>src/models/user.py</file></read>

  <edit>
  <file>src/models/user.py</file>
  <content>
  """User entity model."""
  from sqlalchemy import Column, Integer, String, DateTime, Boolean
  from sqlalchemy.orm import declarative_base, relationship
  from datetime import datetime

  Base = declarative_base()


  class User(Base):
      """User entity representing application users."""

      __tablename__ = "users"

      # primary key - always required
      id = Column(Integer, primary_key=True, autoincrement=True)

      # required attributes
      email = Column(String(255), nullable=False, unique=True, index=True)
      username = Column(String(50), nullable=False, unique=True, index=True)
      password_hash = Column(String(255), nullable=False)

      # optional attributes
      full_name = Column(String(100))
      avatar_url = Column(String(500))
      bio = Column(String(500))

      # status fields
      is_active = Column(Boolean, default=True, nullable=False)
      is_verified = Column(Boolean, default=False, nullable=False)

      # timestamps
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      last_login_at = Column(DateTime)

      # relationships
      posts = relationship("Post", back_populates="author")

      def __repr__(self):
          return f"<User(id={self.id}, username={self.username})>"
  </content>
  </edit>


many-to-many relationship

  <create>
  <file>src/models/tag.py</file>
  <content>
  """Tag entity and many-to-many relationship with posts."""
  from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Table
  from sqlalchemy.orm import relationship

  from .user import Base


  # association table for many-to-many relationship
  post_tags = Table(
      "post_tags",
      Base.metadata,
      Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
      Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
      Column("assigned_at", DateTime, default=datetime.utcnow)
  )


  class Tag(Base):
      """Tag entity for categorizing posts."""

      __tablename__ = "tags"

      id = Column(Integer, primary_key=True, autoincrement=True)
      name = Column(String(50), nullable=False, unique=True, index=True)
      slug = Column(String(50), nullable=False, unique=True)
      color = Column(String(7))  # hex color like #ff0000

      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

      # relationships
      posts = relationship("Post", secondary=post_tags, back_populates="tags")

      def __repr__(self):
          return f"<Tag(id={self.id}, name={self.name})>"
  </content>
  </edit>


add tags relationship to Post

  <read><file>src/models/post.py</file></read>

  <edit>
  <file>src/models/post.py</file>
  <find>
      """Post entity with relationships."""
      from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
      from sqlalchemy.orm import relationship

      from .user import Base, User
  </find>
  <replace>
      """Post entity with relationships."""
      from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
      from sqlalchemy.orm import relationship

      from .user import Base, User
      from .tag import post_tags
  </replace>
  </edit>

  <edit>
  <file>src/models/post.py</file>
  <find>
      # relationships
      author = relationship("User", back_populates="posts")
  </find>
  <replace>
      # relationships
      author = relationship("User", back_populates="posts")
      tags = relationship("Tag", secondary=post_tags, back_populates="posts")
  </replace>
  </edit>


one-to-one relationship

  <create>
  <file>src/models/profile.py</file>
  <content>
  """User profile - one-to-one with user."""
  from sqlalchemy import Column, Integer, String, Text, ForeignKey, Date
  from sqlalchemy.orm import relationship

  from .user import Base


  class UserProfile(Base):
      """Extended profile information for users."""

      __tablename__ = "user_profiles"

      # one-to-one: user_id is primary key and foreign key
      user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)

      # profile attributes
      phone = Column(String(20))
      address_line1 = Column(String(100))
      address_line2 = Column(String(100))
      city = Column(String(50))
      state = Column(String(50))
      postal_code = Column(String(20))
      country = Column(String(2))  # ISO country code

      birth_date = Column(Date)
      website = Column(String(200))
      linkedin_url = Column(String(200))
      github_url = Column(String(200))

      preferences = Column(Text)  # JSON string for flexible preferences

      # relationship
      user = relationship("User", back_populates="profile")

      def __repr__(self):
          return f"<UserProfile(user_id={self.user_id})>"
  </content>
  </edit>


add profile relationship to User

  <read><file>src/models/user.py</file></read>

  <edit>
  <file>src/models/user.py</file>
  <find>
      # relationships
      posts = relationship("Post", back_populates="author")
  </find>
  <replace>
      # relationships
      posts = relationship("Post", back_populates="author")
      profile = relationship("UserProfile", back_populates="user", uselist=False)
  </replace>
  </edit>


PHASE 4: INDEXING STRATEGY


primary indexes

every table needs a primary key:
  - most common: auto-increment integer
  - for distributed systems: uuid
  - for natural keys: composite key on unique columns


unique indexes

enforce uniqueness and speed lookups:

  <read><file>src/models/user.py</file></read>

  <edit>
  <file>src/models/user.py</file>
  <content>
  """User entity model with indexes."""
  from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index, UniqueConstraint
  from sqlalchemy.orm import declarative_base, relationship
  from datetime import datetime

  Base = declarative_base()


  class User(Base):
      """User entity representing application users."""

      __tablename__ = "users"

      # primary key - always required
      id = Column(Integer, primary_key=True, autoincrement=True)

      # required attributes with unique indexes
      email = Column(String(255), nullable=False)
      username = Column(String(50), nullable=False)
      password_hash = Column(String(255), nullable=False)

      # optional attributes
      full_name = Column(String(100))
      avatar_url = Column(String(500))
      bio = Column(String(500))

      # status fields
      is_active = Column(Boolean, default=True, nullable=False)
      is_verified = Column(Boolean, default=False, nullable=False)

      # timestamps
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      last_login_at = Column(DateTime)

      # relationships
      posts = relationship("Post", back_populates="author")
      profile = relationship("UserProfile", back_populates="user", uselist=False)

      # constraints
      __table_args__ = (
          UniqueConstraint("email", name="uq_users_email"),
          UniqueConstraint("username", name="uq_users_username"),
          Index("idx_users_email", "email"),
          Index("idx_users_username", "username"),
          Index("idx_users_is_active", "is_active"),
          Index("idx_users_created_at", "created_at"),
      )

      def __repr__(self):
          return f"<User(id={self.id}, username={self.username})>"
  </content>
  </edit>


composite indexes

for queries filtering on multiple columns:

  <create>
  <file>src/models/order.py</file>
  <content>
  """Order entity with composite indexes."""
  from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey, Index
  from sqlalchemy.orm import relationship

  from .user import Base


  class Order(Base):
      """Customer orders."""

      __tablename__ = "orders"

      id = Column(Integer, primary_key=True, autoincrement=True)
      order_number = Column(String(50), nullable=False, unique=True)

      # foreign key
      user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

      # order details
      status = Column(String(20), nullable=False, default="pending")
      total = Column(Numeric(10, 2), nullable=False)

      # timestamps
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

      # relationships
      user = relationship("User")

      # composite indexes for common query patterns
      __table_args__ = (
          # index for user's orders by status
          Index("idx_orders_user_status", "user_id", "status"),
          # index for user's orders by date
          Index("idx_orders_user_created", "user_id", "created_at"),
          # index for orders by status and date (dashboard query)
          Index("idx_orders_status_created", "status", "created_at"),
      )
  </content>
  </create>


when to index

index these columns:
  [x] primary keys (automatic)
  [x] foreign keys
  [x] columns in WHERE clauses
  [x] columns in JOIN conditions
  [x] columns in ORDER BY
  [x] columns frequently searched

dont index:
  [x] columns with low cardinality (boolean, small enums)
  [x] columns frequently updated
  [x] very wide columns (long text)
  [x] tables that are write-heavy and rarely read


PHASE 5: MIGRATION SETUP WITH ALEMBIC


initialize alembic

  <terminal>alembic init alembic</terminal>

this creates:
  - alembic/
  - alembic.ini
  - alembic/env.py


configure alembic.ini

  <read><file>alembic.ini</file></read>

  <edit>
  <file>alembic.ini</file>
  <find>
  sqlalchemy.url = driver://user:pass@localhost/dbname
  </find>
  <replace>
  sqlalchemy.url = postgresql://user:password@localhost:5432/dbname
  # or use environment variable:
  # sqlalchemy.url = ${DATABASE_URL}
  </replace>
  </edit>


configure env.py

  <read><file>alembic/env.py</file></read>

  <edit>
  <file>alembic/env.py</file>
  <find>
  from logging.config import fileConfig

  from sqlalchemy import engine_from_config
  from sqlalchemy import pool

  from alembic import context

  # this is the Alembic Config object
  config = context.config

  # add your model's MetaData object here
  # for 'autogenerate' support
  # target_metadata = mymodel.Base.metadata
  target_metadata = None
  </find>
  <replace>
  from logging.config import fileConfig

  from sqlalchemy import engine_from_config
  from sqlalchemy import pool

  from alembic import context

  # import your models
  import sys
  from pathlib import Path
  sys.path.append(str(Path(__file__).parent.parent))

  from src.models.user import Base
  from src.models.post import Post
  from src.models.tag import Tag
  from src.models.profile import UserProfile
  from src.models.order import Order

  # this is the Alembic Config object
  config = context.config

  # add your model's MetaData object here
  target_metadata = Base.metadata
  </replace>
  </edit>


PHASE 6: CREATING MIGRATIONS


initial migration

  <terminal>alembic revision --autogenerate -m "Initial schema"</terminal>

this generates a new migration file in alembic/versions/.


review generated migration

  <read><file>alembic/versions/001_initial_schema.py</file></read>

check that:
  [ ] all tables are created
  [ ] indexes are defined
  [ ] foreign keys have proper constraints
  [ ] no extra tables from imports


manual migration for control

  <terminal>alembic revision -m "Add user email verification"</terminal>

  <read><file>alembic/versions/002_add_user_email_verification.py</file></read>

  <edit>
  <file>alembic/versions/002_add_user_email_verification.py</file>
  <find>
  """${message}

  Revision ID: ${up_revision}
  Revises: ${down_revision}
  Create Date: ${create_date}

  """
  from alembic import op
  import sqlalchemy as sa
  ${imports if imports else ""}

  # revision identifiers, used by Alembic.
  revision = ${repr(up_revision)}
  down_revision = ${repr(down_revision)}
  branch_labels = ${repr(branch_labels)}
  depends_on = ${repr(depends_on)}

  def upgrade() -> None:
      ${upgrades if upgrades else "pass"}

  def downgrade() -> None:
      ${downgrades if downgrades else "pass"}
  </find>
  <replace>
  """Add user email verification token and timestamp

  Revision ID: 002_add_verification
  Revises: 001_initial_schema
  Create Date: 2024-01-15

  """
  from alembic import op
  import sqlalchemy as sa

  # revision identifiers, used by Alembic.
  revision = "002_add_verification"
  down_revision = "001_initial_schema"
  branch_labels = None
  depends_on = None

  def upgrade() -> None:
      # add verification_token column
      op.add_column(
          "users",
          sa.Column("verification_token", sa.String(255), nullable=True)
      )

      # add verified_at column
      op.add_column(
          "users",
          sa.Column("verified_at", sa.DateTime(), nullable=True)
      )

      # create index for token lookup
      op.create_index(
          "idx_users_verification_token",
          "users",
          ["verification_token"]
      )

  def downgrade() -> None:
      # remove index
      op.drop_index("idx_users_verification_token", "users")

      # remove columns
      op.drop_column("users", "verified_at")
      op.drop_column("users", "verification_token")
  </replace>
  </edit>


PHASE 7: RUNNING AND MANAGING MIGRATIONS


apply migrations

  <terminal>alembic upgrade head</terminal>

this runs all pending migrations.


check current version

  <terminal>alembic current</terminal>

view migration history:
  <terminal>alembic history</terminal>


rollback one migration

  <terminal>alembic downgrade -1</terminal>


rollback to specific version

  <terminal>alembic downgrade 001_initial_schema</terminal>


redo last migration

useful during development when testing migrations:

  <terminal>alembic downgrade -1 && alembic upgrade head</terminal>


PHASE 8: DATA MIGRATIONS


migrating existing data

sometimes you need to transform data during migration:

  <create>
  <file>alembic/versions/003_migrate_user_status.py</file>
  <content>
  """Migrate user status to separate columns

  Revision ID: 003_migrate_user_status
  Revises: 002_add_verification
  Create Date: 2024-01-16

  """
  from alembic import op
  import sqlalchemy as sa

  revision = "003_migrate_user_status"
  down_revision = "002_add_verification"
  branch_labels = None
  depends_on = None

  def upgrade() -> None:
      # step 1: add new columns
      op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=True))
      op.add_column("users", sa.Column("is_banned", sa.Boolean(), nullable=True))

      # step 2: migrate data from old status column
      connection = op.get_bind()

      # set is_active based on status
      connection.execute(
          sa.text("""
              UPDATE users
              SET is_active = (status = 'active')
          """)
      )

      # set is_banned based on status
      connection.execute(
          sa.text("""
              UPDATE users
              SET is_banned = (status = 'banned')
          """)
      )

      # step 3: make new columns non-nullable with defaults
      op.alter_column("users", "is_active", nullable=False, server_default="true")
      op.alter_column("users", "is_banned", nullable=False, server_default="false")

      # step 4: remove old status column
      op.drop_column("users", "status")

  def downgrade() -> None:
      # step 1: add back old status column
      op.add_column("users", sa.Column("status", sa.String(20), nullable=True))

      # step 2: migrate data back
      connection = op.get_bind()

      connection.execute(
          sa.text("""
              UPDATE users
              SET status = CASE
                  WHEN is_banned THEN 'banned'
                  WHEN NOT is_active THEN 'inactive'
                  ELSE 'active'
              END
          """)
      )

      # step 3: make status non-nullable
      op.alter_column("users", "status", nullable=False)

      # step 4: remove new columns
      op.drop_column("users", "is_banned")
      op.drop_column("users", "is_active")
  </content>
  </create>


PHASE 9: DATABASE CONSTRAINTS


check constraints

enforce data rules at database level:

  <create>
  <file>src/models/constraints.py</file>
  <content>
  """Models with database constraints."""
  from sqlalchemy import Column, Integer, String, DateTime, Numeric, CheckConstraint
  from sqlalchemy.orm import declarative_base
  from datetime import datetime

  Base = declarative_base()


  class Product(Base):
      """Product with validation constraints."""

      __tablename__ = "products"

      id = Column(Integer, primary_key=True)
      name = Column(String(100), nullable=False)
      price = Column(Numeric(10, 2), nullable=False)
      quantity = Column(Integer, nullable=False)
      discount_percent = Column(Integer, default=0)

      created_at = Column(DateTime, default=datetime.utcnow)

      # constraints
      __table_args__ = (
          # price must be positive
          CheckConstraint("price > 0", name="check_positive_price"),
          # quantity cannot be negative
          CheckConstraint("quantity >= 0", name="check_nonnegative_quantity"),
          # discount between 0 and 100
          CheckConstraint("discount_percent >= 0 AND discount_percent <= 100",
                         name="check_valid_discount"),
          # final price after discount must be positive
          CheckConstraint("price * (1 - discount_percent / 100.0) > 0",
                         name="check_positive_final_price"),
      )
  </content>
  </create>


enum constraints

  <create>
  <file>src/models/enums.py</file>
  <content>
  """Models with enum constraints."""
  from sqlalchemy import Column, Integer, String, DateTime, Enum
  from sqlalchemy.orm import declarative_base
  from enum import Enum as PyEnum
  from datetime import datetime

  Base = declarative_base()


  class OrderStatus(PyEnum):
      """Order status enumeration."""
      PENDING = "pending"
      PROCESSING = "processing"
      SHIPPED = "shipped"
      DELIVERED = "delivered"
      CANCELLED = "cancelled"
      REFUNDED = "refunded"


  class Order(Base):
      """Order with enum status."""

      __tablename__ = "orders"

      id = Column(Integer, primary_key=True)
      status = Column(
          Enum(OrderStatus),
          default=OrderStatus.PENDING,
          nullable=False
      )

      created_at = Column(DateTime, default=datetime.utcnow)
  </content>
  </create>


PHASE 10: QUERY OPTIMIZATION


avoid N+1 queries

the classic anti-pattern:

  # bad: N+1 query
  users = session.query(User).all()
  for user in users:
      print(user.posts)  # each iteration triggers a query!

the fix: eager loading

  from sqlalchemy.orm import selectinload, joinedload

  # good: single query with join
  users = session.query(User).options(selectinload(User.posts)).all()
  for user in users:
      print(user.posts)  # already loaded!


select the right join type

  selectinload - good for one-to-many, separate queries
    users = session.query(User).options(selectinload(User.posts)).all()

  joinedload - good for one-to-one, single query with join
    users = session.query(User).options(joinedload(User.profile)).all()

  subqueryload - for nested relationships
    users = session.query(User).options(
        subqueryload(User.posts).selectinload(Post.tags)
    ).all()


only select what you need

  # bad: fetches all columns
  users = session.query(User).all()

  # good: only needed columns
  user_names = session.query(User.username, User.email).all()

  # good: use entities
  users = session.query(User.username, User.email).all()


use pagination

  <create>
  <file>src/repository/pagination.py</file>
  <content>
  """Pagination utilities."""
  from typing import Generic, TypeVar, List
  from dataclasses import dataclass
  from sqlalchemy.orm import Query

  T = TypeVar("T")


  @dataclass
  class PaginatedResult(Generic[T]):
      """Result of paginated query."""
      items: List[T]
      total: int
      page: int
      page_size: int
      has_more: bool

      @property
      def total_pages(self) -> int:
          """Calculate total pages."""
          return (self.total + self.page_size - 1) // self.page_size


  def paginate(query: Query, page: int = 1, page_size: int = 20) -> PaginatedResult:
      """Apply pagination to a SQLAlchemy query."""
      # count total
      total = query.count()

      # calculate offset
      offset = (page - 1) * page_size

      # fetch page
      items = query.offset(offset).limit(page_size).all()

      return PaginatedResult(
          items=items,
          total=total,
          page=page,
          page_size=page_size,
          has_more=offset + page_size < total
      )
  </content>
  </create>


PHASE 11: TRANSACTION MANAGEMENT


transaction basics

  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker

  engine = create_engine("postgresql://...")
  Session = sessionmaker(bind=engine)


  # explicit transaction
  session = Session()
  try:
      user = User(username="alice", email="alice@example.com")
      session.add(user)
      session.commit()
  except Exception as e:
      session.rollback()
      raise
  finally:
      session.close()


context manager for transactions

  <create>
  <file>src/database/transaction.py</file>
  <content>
  """Transaction management utilities."""
  from contextlib import contextmanager
  from typing import Generator
from sqlalchemy.orm import Session


  @contextmanager
  def transaction(session: Session) -> Generator[Session, None, None]:
      """Context manager for database transactions.

      Usage:
          with transaction(session) as s:
              user = User(username="alice")
              s.add(user)
              # automatically commits on success, rolls back on error
      """
      try:
          yield session
          session.commit()
      except Exception:
          session.rollback()
          raise


  @contextmanager
  def nested_transaction(session: Session) -> Generator[Session, None, None]:
      """Context manager for nested (savepoint) transactions.

      Useful for tests or when you need to rollback inner transactions
      while keeping outer ones.
      """
      try:
          nested = session.begin_nested()
          yield session
          nested.commit()
      except Exception:
          nested.rollback()
          raise
  </content>
  </create>


PHASE 12: SOFT DELETES


implementing soft deletes

  <create>
  <file>src/models/soft_delete.py</file>
  <content>
  """Soft delete mixin for models."""
  from sqlalchemy import Column, DateTime, Boolean, event
  from sqlalchemy.orm import declarative_mixin
  from datetime import datetime


  @declarative_mixin
  class SoftDeleteMixin:
      """Add soft delete functionality to a model."""

      deleted_at = Column(DateTime, nullable=True, index=True)
      is_deleted = Column(Boolean, default=False, nullable=False, index=True)

      def soft_delete(self):
          """Mark record as deleted without removing from database."""
          self.is_deleted = True
          self.deleted_at = datetime.utcnow()

      def restore(self):
          """Restore soft-deleted record."""
          self.is_deleted = False
          self.deleted_at = None


  class SoftDeleteQuery:
      """Query mixin for filtering soft-deleted records."""

      def _with_deleted(self):
          """Include soft-deleted records."""
          return self.enable_assertions(False).filter()

      def without_deleted(self):
          """Exclude soft-deleted records."""
          return self.filter_by(is_deleted=False)
  </content>
  </create>


using soft deletes in models

  from sqlalchemy import Column, Integer, String
  from sqlalchemy.orm import declarative_base

  Base = declarative_base()


  class User(SoftDeleteMixin, Base):
      """User with soft delete."""
      __tablename__ = "users"

      id = Column(Integer, primary_key=True)
      username = Column(String(50), nullable=False)
      email = Column(String(255), nullable=False)


PHASE 13: AUDIT COLUMNS AND HISTORY


audit columns

  <create>
  <file>src/models/audit.py</file>
  <content>
  """Audit columns for tracking record changes."""
  from sqlalchemy import Column, DateTime, Integer, ForeignKey, event
  from sqlalchemy.orm import declarative_mixin, relationship
  from datetime import datetime


  @declarative_mixin
  class AuditMixin:
      """Add audit columns to track record lifecycle."""

      created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
      updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

      # relationships
      created_by = relationship("User", foreign_keys=[created_by_id])
      updated_by = relationship("User", foreign_keys=[updated_by_id])


  @declarative_mixin
  class FullAuditMixin(AuditMixin):
      """Extended audit with deletion tracking."""

      deleted_at = Column(DateTime, nullable=True)
      deleted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

      # relationship
      deleted_by = relationship("User", foreign_keys=[deleted_by_id])
  </content>
  </create>


audit table for history tracking

  <create>
  <file>src/models/audit_log.py</file>
  <content>
  """Audit log table for tracking changes."""
  from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, JSON
  from sqlalchemy.orm import declarative_base, relationship
  from datetime import datetime

  Base = declarative_base()


  class AuditLog(Base):
      """Record of all changes to tracked entities."""

      __tablename__ = "audit_logs"

      id = Column(Integer, primary_key=True, autoincrement=True)

      # what changed
      table_name = Column(String(100), nullable=False, index=True)
      record_id = Column(Integer, nullable=False, index=True)
      action = Column(String(20), nullable=False)  # INSERT, UPDATE, DELETE

      # who changed it
      user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
      user = relationship("User", foreign_keys=[user_id])

      # what changed
      old_values = Column(JSON)  # {"field": "old_value"}
      new_values = Column(JSON)  # {"field": "new_value"}
      changed_fields = Column(JSON)  # ["field1", "field2"]

      # metadata
      ip_address = Column(String(45))  # IPv6 support
      user_agent = Column(String(500))
      created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

      def __repr__(self):
          return f"<AuditLog(table={self.table_name}, record_id={self.record_id}, action={self.action})>"
  </content>
  </create>


PHASE 14: DATABASE TESTING


test database fixtures

  <create>
  <file>tests/conftest.py</file>
  <content>
  """Pytest fixtures for database testing."""
  import pytest
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker
  from tempfile import NamedTemporaryFile
  import os

  from src.models.user import Base
  from src.models.post import Post
  from src.models.tag import Tag


  @pytest.fixture(scope="function")
  def test_db():
      """Create an in-memory SQLite database for testing."""
      engine = create_engine("sqlite:///:memory:")

      # create all tables
      Base.metadata.create_all(engine)

      yield engine

      # cleanup
      Base.metadata.drop_all(engine)


  @pytest.fixture(scope="function")
  def db_session(test_db):
      """Create a database session for testing."""
      Session = sessionmaker(bind=test_db)
      session = Session()

      yield session

      session.close()


  @pytest.fixture
  def sample_user(db_session):
      """Create a sample user for tests."""
      user = User(
          username="testuser",
          email="test@example.com",
          password_hash="hashed_password"
      )
      db_session.add(user)
      db_session.commit()
      return user
  </content>
  </create>


example tests

  <create>
  <file>tests/test_models.py</file>
  <content>
  """Tests for database models."""
  import pytest
  from sqlalchemy.exc import IntegrityError

  from src.models.user import User
  from src.models.post import Post


  def test_create_user_succeeds(db_session):
      """Test creating a user."""
      user = User(
          username="alice",
          email="alice@example.com",
          password_hash="hashed"
      )
      db_session.add(user)
      db_session.commit()

      assert user.id is not None
      assert user.username == "alice"
      assert user.is_active is True


  def test_unique_email_enforced(db_session):
      """Test that duplicate emails are rejected."""
      user1 = User(username="user1", email="same@example.com", password_hash="hash1")
      user2 = User(username="user2", email="same@example.com", password_hash="hash2")

      db_session.add(user1)
      db_session.commit()

      db_session.add(user2)
      with pytest.raises(IntegrityError):
          db_session.commit()


  def test_user_post_relationship(db_session):
      """Test that users can have posts."""
      user = User(username="alice", email="alice@example.com", password_hash="hashed")
      post = Post(title="Test", slug="test", content="Content", author_id=1)

      db_session.add(user)
      db_session.commit()

      post.author_id = user.id
      db_session.add(post)
      db_session.commit()

      assert post.author_id == user.id
      assert len(user.posts) == 1
      assert user.posts[0].title == "Test"


  def test_soft_delete(db_session):
      """Test soft delete functionality."""
      user = User(username="alice", email="alice@example.com", password_hash="hashed")
      db_session.add(user)
      db_session.commit()

      user_id = user.id
      user.soft_delete()
      db_session.commit()

      # verify soft deleted
      retrieved = db_session.query(User).filter_by(id=user_id).first()
      assert retrieved.is_deleted is True
      assert retrieved.deleted_at is not None
  </content>
  </create>


PHASE 15: DATABASE RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS use migrations for schema changes
      never modify database schema directly
      every change must be reproducible

  [2] NEVER use reserved words as identifiers
      avoid: user, order, group, select, where
      use: users, orders, groups, or prefix: app_user

  [3] EVERY table needs a primary key
      no exceptions
      prefer auto-increment integers for new tables

  [4] FOREIGN KEYS are not optional
      enforce referential integrity at database level
      add proper indexes on foreign keys

  [5] TIMESTAMPS on every table
      created_at is mandatory
      updated_at for mutable data
      deleted_at for soft deletes

  [6] WRITE both upgrade and downgrade
      migrations must be reversible
      never leave a migration half-written

  [7] TEST migrations on sample data
      verify upgrade works
      verify downgrade works
      verify data migration is correct

  [8] USE appropriate data types
      money: Numeric, not Float
      enums: CHECK constraint or ENUM type
      json: JSON/JSONB type, not Text

  [9] INDEX before you need it
      add indexes for foreign keys
      add indexes for query patterns
      review index usage periodically

  [10] DOCUMENT your schema
      explain non-obvious relationships
      document denormalization decisions
      keep ER diagrams up to date


FINAL REMINDERS


database design is foundational

get it wrong and everything suffers.
migrations are painful. refactoring is harder.
think carefully before creating tables.


the database is source of truth

code can change. database persists.
ensure the schema reflects reality.
constraints and validations belong in the database.


normalize first, optimize later

start with normalized design.
measure performance.
denormalize only when needed.


migrations are code

treat them with same care as application code.
review them. test them. version them.


when in doubt

add a timestamp.
add an index.
write a migration.
measure twice, alter once.

now go design some schemas.
