---
name: refactoring
description: "Refactoring skill - safe code transformation while keeping tests green"
---

refactoring mode: CHANGE STRUCTURE, NOT BEHAVIOR

when this skill is active, you follow disciplined refactoring practices.
this is a comprehensive guide to safe code transformation.


PHASE 0: ENVIRONMENT VERIFICATION

before doing ANY refactoring, verify the safety net exists.


verify testing framework exists

  <terminal>python -m pytest --version</terminal>

if pytest not installed:
  <terminal>pip install pytest pytest-cov</terminal>


verify tests exist for code you will refactor

  <terminal>find . -name "test_*.py" -type f | head -20</terminal>

  <terminal>python -m pytest tests/ --collect-only</terminal>

if no tests exist for the target code:
  [warn] cannot refactor safely without tests
  [warn] either write tests first, or refuse to refactor
  the discipline: no tests, no refactoring, no exceptions


verify current test status

  <terminal>python -m pytest tests/ -v</terminal>

all tests must pass BEFORE starting refactoring.
if tests are failing:
  [1] fix failing tests first
  [2] or write reproduction tests for known issues
  [3] never refactor while tests are red

document current state:

  <terminal>python -m pytest tests/ --cov -v | tee test_status_before.log</terminal>


verify git status

  <terminal>git status</terminal>

  <terminal>git diff --stat</terminal>

ensure working directory is clean or changes are committed.
refactoring on top of uncommitted work is risky.

best practice:
  <terminal>git checkout -b refactor/safe-cleanup-description</terminal>


check for IDE refactoring support

  <terminal>code --list-extensions | grep -i refactor</terminal>

optional but helpful:
  - VS Code: Python extension with refactoring
  - PyCharm: built-in refactoring tools
  - vim/emacs: with lsp or rope plugin


PHASE 1: WHAT IS REFACTORING

definition:

refactoring is changing code structure WITHOUT changing behavior.
the external behavior stays exactly the same.
the internal structure becomes cleaner, clearer, more maintainable.


what refactoring is NOT:

  [x] adding new features
  [x] fixing bugs (behavior changes)
  [x] performance optimization (usually changes behavior)
  [x] updating dependencies
  [x] changing API contracts

these are different activities.
do them separately from refactoring.


the two hats:

  hat 1: adding function
    - adding new capabilities
    - fixing bugs
    - changing behavior
    - tests will change/expand

  hat 2: refactoring
    - restructuring existing code
    - behavior stays exactly the same
    - tests stay exactly the same

never wear both hats at once.
switch clearly between them.


when to refactor

  [ok] when you need to add a feature and the code resists
  [ok] when code is duplicated in multiple places
  [ok] when names dont clearly express intent
  [ok] when methods are too long to understand
  [ok] when conditionals are deeply nested
  [ok] when you need to understand how code works

  [warn] when deadlines are tight and tests are missing
  [warn] when code works but you dont like the style
  [warn] when youre trying to learn a new pattern

the rule of three:
  1. first time - just do it
  2. second time - wince at the duplication
  3. third time - refactor


when NOT to refactor

  [x] production is down and youre panicked
  [x] no tests exist
  [x] tests are failing
  [x] youre about to release
  [x] the code is about to be deleted
  [x] you dont understand what the code does

first understand, then refactor.
never refactor mysterious code.


PHASE 2: THE REFACTORING CYCLE

the core rhythm:

  [1] verify tests pass
  [2] identify the smell
  [3] choose the refactoring
  [4] make the SMALL change
  [5] run tests
  [6] if tests pass, commit
  [7] if tests fail, revert and try smaller step
  [8] repeat

this cycle repeats for every refactoring.
never batch multiple refactorings without testing.


step 1: verify tests pass

  <terminal>python -m pytest tests/ -v</terminal>

get to green before changing anything.
document the baseline:
  <terminal>python -m pytest tests/ --cov > coverage_baseline.txt</terminal>


step 2: identify the smell

common code smells:

  - duplicated code
  - long method
  - large class
  - long parameter list
  - divergent change
  - shotgun surgery
  - feature envy
  - data clumps
  - primitive obsession
  - switch statements
  - temporary fields
  - refused bequest
  - comments

name the smell before fixing it.


step 3: choose the refactoring

match smell to refactoring:
  - duplicated code -> extract method
  - long method -> decompose conditional, extract method
  - large class -> extract class
  - long parameter list -> introduce parameter object
  - feature envy -> move method
  - primitive obsession -> replace primitive with object
  - switch statements -> replace conditional with polymorphism

know your refactoring catalog.


step 4: make the small change

how small?
  - one extraction at a time
  - one rename at a time
  - one move at a time

if you hesitate, make the change smaller.
you can always make another small change after.


step 5: run tests

  <terminal>python -m pytest tests/ -v</terminal>

every single change gets tested.
no exceptions.


step 6: commit if green

  <terminal>git add -A</terminal>
  <terminal>git commit -m "refactor: extract user validation to separate method"</terminal>

small commits are your friend.
they make rollback easy.


step 7: revert if red

  <terminal>git checkout -- .</terminal>

or
  <terminal>git reset --hard HEAD</terminal>

never try to fix tests that broke during refactoring.
revert and think.
make a smaller change.


PHASE 3: EXTRACT METHOD

the most fundamental refactoring.


when to extract

signs you need extract method:
  - method is longer than 10-15 lines
  - method has multiple levels of indentation
  - comments explain what a block does
  - code is duplicated
  - a group of lines forms a single concept


basic extraction

before:

  def print_report(self, users):
      # calculate totals
      total_age = 0
      for user in users:
          total_age += user.age
      average = total_age / len(users)

      # print header
      print("=" * 50)
      print("USER REPORT")
      print("=" * 50)

      # print users
      for user in users:
          print(f"{user.name}: {user.age} years old")

      # print footer
      print("=" * 50)
      print(f"Average age: {average}")
      print("=" * 50)

after:

  def print_report(self, users):
      average = self._calculate_average_age(users)
      self._print_header()
      self._print_users(users)
      self._print_footer(average)

  def _calculate_average_age(self, users):
      total_age = sum(user.age for user in users)
      return total_age / len(users)

  def _print_header(self):
      print("=" * 50)
      print("USER REPORT")
      print("=" * 50)

  def _print_users(self, users):
      for user in users:
          print(f"{user.name}: {user.age} years old")

  def _print_footer(self, average):
      print("=" * 50)
      print(f"Average age: {average}")
      print("=" * 50)


extraction with parameters

before:

  def process_order(self, order):
      if order.total > 1000:
          discount = order.total * 0.1
          order.total -= discount
      if order.customer.is_vip:
          discount = order.total * 0.05
          order.total -= discount

after:

  def process_order(self, order):
      self._apply_volume_discount(order)
      self._apply_vip_discount(order)

  def _apply_volume_discount(self, order):
      if order.total > 1000:
          discount = order.total * 0.1
          order.total -= discount

  def _apply_vip_discount(self, order):
      if order.customer.is_vip:
          discount = order.total * 0.05
          order.total -= discount


extraction with return value

before:

  def send_notification(self, user, message):
      formatted = f"Dear {user.name},\n\n{message}\n\nBest regards"
      email = self._get_email_address(user)
      # ... send email ...
      return formatted

after:

  def send_notification(self, user, message):
      formatted = self._format_message(user, message)
      email = self._get_email_address(user)
      # ... send email ...
      return formatted

  def _format_message(self, user, message):
      return f"Dear {user.name},\n\n{message}\n\nBest regards"


testing extract method

  <terminal>python -m pytest tests/test_report.py -v</terminal>

tests should pass without modification.
if tests fail, the extraction changed behavior.
revert and try again.


PHASE 4: RENAME VARIABLES AND FUNCTIONS

good names are critical for readability.


when to rename

signs you need to rename:
  - you have to think to understand what a variable means
  - the name is misleading
  - the name is too generic (data, info, value, temp)
  - abbreviations require decoding
  - the name describes implementation, not intent


naming principles

  [ok] user, order, calculate_total, is_valid
  [ok] fetch_user_data, render_html_content
  [warn] u, o, calc, check
  [x] data, info, value, temp, thing, stuff

intent > brevity.
clarity > cleverness.


rename variables

before:

  def proc(self, d):
      for i in d:
          print(i['n'])
      return len(d)

after:

  def display_users(self, users):
      for user in users:
          print(user['name'])
      return len(users)


rename methods

before:

  def get(self, id):
      return self.db.find(id)

after:

  def find_user_by_id(self, user_id):
      return self.db.find(user_id)


rename boolean variables

use is/has/should/can prefixes:

  [ok] is_valid, has_permission, should_retry, can_delete
  [x] valid, permission, retry, delete

rename with IDE:

  # VS Code
  F2 on the name, type new name, Enter

  # PyCharm
  Shift+F6 on the name, type new name, Enter

  # command line (manual, risky)
  <terminal>grep -r "old_name" src/</terminal>
  # then careful find and replace


rename checklist

  [ ] search for all usages of the name
  [ ] verify each usage still makes sense with new name
  [ ] run tests to ensure nothing broke
  [ ] commit the rename

  <terminal>python -m pytest tests/ -v</terminal>


PHASE 5: INLINE TEMP / INLINE VARIABLE

reverse of extract method.
when a variable is only used once, inline it.


when to inline

signs you need to inline:
  - temp variable is used only once
  - the expression is clearer than the variable name
  - the variable doesnt add meaningful abstraction


basic inline

before:

  def calculate_discount(self, order):
      base_price = order.quantity * order.item_price
      if base_price > 1000:
          return base_price * 0.9
      return base_price

after:

  def calculate_discount(self, order):
      base_price = order.quantity * order.item_price
      if base_price > 1000:
          return order.quantity * order.item_price * 0.9
      return base_price

inline the second usage since it adds nothing.


inline with explanation

before:

  def is_eligible_for_discount(self, customer):
      is_important = customer.tier == "VIP" and customer.years > 5
      if is_important:
          return True
      return False

after:

  def is_eligible_for_discount(self, customer):
      return customer.tier == "VIP" and customer.years > 5


inline to simplify

before:

  def process(self, data):
      result = self._transform(data)
      return result

after:

  def process(self, data):
      return self._transform(data)


PHASE 6: EXTRACT CLASS

when a class does too much, split it.


when to extract class

signs you need extract class:
  - class has more than ~300 lines
  - class has unrelated responsibilities
  - class changes for multiple reasons
  - subset of methods uses subset of fields


identify natural boundaries

group related:
  - fields
  - methods that use those fields
  - responsibilities

these form the new class.


basic extraction

before:

  class Person:
      def __init__(self, name, email, phone, street, city, zip_code):
          self.name = name
          self.email = email
          self.phone = phone
          self.street = street
          self.city = city
          self.zip_code = zip_code

      def get_full_address(self):
          return f"{self.street}, {self.city} {self.zip_code}"

      def get_email_domain(self):
          return self.email.split('@')[1]

after:

  class Person:
      def __init__(self, name, contact_info):
          self.name = name
          self.contact_info = contact_info

  class ContactInfo:
      def __init__(self, email, phone, street, city, zip_code):
          self.email = email
          self.phone = phone
          self.address = Address(street, city, zip_code)

      def get_email_domain(self):
          return self.email.split('@')[1]

  class Address:
      def __init__(self, street, city, zip_code):
          self.street = street
          self.city = city
          self.zip_code = zip_code

      def get_full_address(self):
          return f"{self.street}, {self.city} {self.zip_code}"


extraction with delegation

before:

  class OrderProcessor:
      def __init__(self):
          self.inventory = {}
          self.pricing = {}

      def process_order(self, order):
          if self._check_inventory(order):
              price = self._calculate_price(order)
              self._update_inventory(order)
              return price

      def _check_inventory(self, order):
          # inventory logic
          pass

      def _calculate_price(self, order):
          # pricing logic
          pass

      def _update_inventory(self, order):
          # inventory update logic
          pass

after:

  class OrderProcessor:
      def __init__(self, inventory_manager, pricing_calculator):
          self.inventory = inventory_manager
          self.pricing = pricing_calculator

      def process_order(self, order):
          if self.inventory.check_available(order):
              price = self.pricing.calculate(order)
              self.inventory.update(order)
              return price

  class InventoryManager:
      def __init__(self):
          self.stock = {}

      def check_available(self, order):
          # inventory logic
          pass

      def update(self, order):
          # inventory update logic
          pass

  class PricingCalculator:
      def __init__(self):
          self.price_list = {}

      def calculate(self, order):
          # pricing logic
          pass


testing extract class

  <terminal>python -m pytest tests/test_order_processor.py -v</terminal>

interface tests should pass.
internal structure changed, behavior unchanged.


PHASE 7: MOVE METHOD / MOVE FUNCTION

when a method is in the wrong class, move it.


when to move

signs a method should move:
  - method uses more data from another class
  - method has no real use in current class
  - a class has feature envy (uses another class more than itself)


move to where data is

before:

  class Order:
      def __init__(self, customer):
          self.customer = customer
          self.items = []

      def get_customer_address(self):
          return f"{self.customer.street}, {self.customer.city}"

      def get_customer_discount(self):
          if self.customer.tier == "VIP":
              return 0.1
          return 0.0

after:

  class Order:
      def __init__(self, customer):
          self.customer = customer
          self.items = []

      def get_customer_address(self):
          return self.customer.get_address()

      def get_customer_discount(self):
          return self.customer.get_discount()

  class Customer:
      def __init__(self, street, city, tier):
          self.street = street
          self.city = city
          self.tier = tier

      def get_address(self):
          return f"{self.street}, {self.city}"

      def get_discount(self):
          if self.tier == "VIP":
              return 0.1
          return 0.0


move to parameter object

before:

  def calculate_shipping(order):
      if order.customer.address.state == order.warehouse.address.state:
          return 5.0
      else:
          distance = calculate_distance(
              order.customer.address,
              order.warehouse.address
          )
          return distance * 0.1

after:

  def calculate_shipping(order):
      return order.shipping_calculator.calculate()


PHASE 8: REPLACE CONDITIONAL WITH POLYMORPHISM

when switch statements duplicate, use polymorphism.


when to use polymorphism

signs you need polymorphism:
  - same switch appears in multiple places
  - adding new type requires changing many places
  - switch on type codes


before: type codes

  class Employee:
      def __init__(self, type_code):
          self.type_code = type_code

      def calculate_pay(self):
          if self.type_code == "ENGINEER":
              return self.salary * 1.0
          elif self.type_code == "MANAGER":
              return self.salary * 1.2
          elif self.type_code == "SALES":
              return self.salary * 0.9 + self.commission

      def get_bonus(self):
          if self.type_code == "ENGINEER":
              return 5000
          elif self.type_code == "MANAGER":
              return 10000
          elif self.type_code == "SALES":
              return self.sales * 0.05


after: polymorphism

  from abc import ABC, abstractmethod

  class Employee(ABC):
      def __init__(self, salary):
          self.salary = salary

      @abstractmethod
      def calculate_pay(self):
          pass

      @abstractmethod
      def get_bonus(self):
          pass

  class Engineer(Employee):
      def calculate_pay(self):
          return self.salary * 1.0

      def get_bonus(self):
          return 5000

  class Manager(Employee):
      def calculate_pay(self):
          return self.salary * 1.2

      def get_bonus(self):
          return 10000

  class Sales(Employee):
      def __init__(self, salary, commission, sales):
          super().__init__(salary)
          self.commission = commission
          self.sales = sales

      def calculate_pay(self):
          return self.salary * 0.9 + self.commission

      def get_bonus(self):
          return self.sales * 0.05


factory creation

  class EmployeeFactory:
      @staticmethod
      def create(type_code, **kwargs):
          if type_code == "ENGINEER":
              return Engineer(kwargs.get("salary", 0))
          elif type_code == "MANAGER":
              return Manager(kwargs.get("salary", 0))
          elif type_code == "SALES":
              return Sales(
                  kwargs.get("salary", 0),
                  kwargs.get("commission", 0),
                  kwargs.get("sales", 0)
              )


PHASE 9: DECOMPOSE CONDITIONAL

complex conditionals are hard to read.
break them into named methods.


when to decompose

signs you need to decompose:
  - conditional logic is hard to understand
  - comments are needed to explain the conditional
  - same condition appears multiple times


decompose and/or

before:

  def calculate_shipping_cost(self, order):
      if (order.weight > 10 and order.destination.country != "US") or \
         (order.weight > 20 and order.destination.country == "US"):
          return order.weight * 2.0
      else:
          return order.weight * 1.0

after:

  def calculate_shipping_cost(self, order):
      if self._is_express_shipping(order):
          return order.weight * 2.0
      else:
          return order.weight * 1.0

  def _is_express_shipping(self, order):
      return self._is_heavy_international(order) or \
             self._is_very_heavy_domestic(order)

  def _is_heavy_international(self, order):
      return order.weight > 10 and order.destination.country != "US"

  def _is_very_heavy_domestic(self, order):
      return order.weight > 20 and order.destination.country == "US"


consolidate conditional fragments

before:

  def send_email(self, user, message):
      if user.email:
          print(f"Sending to {user.email}")
          print(f"Subject: {message.subject}")
          print(f"Body: {message.body}")
          self.sent_count += 1
      else:
          print("No email provided")

after:

  def send_email(self, user, message):
      if not user.email:
          print("No email provided")
          return

      self._send_email_message(user.email, message)
      self.sent_count += 1

  def _send_email_message(self, email, message):
      print(f"Sending to {email}")
      print(f"Subject: {message.subject}")
      print(f"Body: {message.body}")


replace nested conditional with guard clauses

before:

  def get_pay_amount(self):
      result = 0
      if self.is_dead:
          result = self.dead_amount()
      else:
          if self.is_separated:
              result = self.separated_amount()
          else:
              if self.is_retired:
                  result = self.retired_amount()
              else:
                  result = self.normal_amount()
      return result

after:

  def get_pay_amount(self):
      if self.is_dead:
          return self.dead_amount()
      if self.is_separated:
          return self.separated_amount()
      if self.is_retired:
          return self.retired_amount()
      return self.normal_amount()


PHASE 10: INTRODUCE PARAMETER OBJECT

when parameters always travel together, group them.


when to group parameters

signs you need parameter object:
  - same parameters appear together repeatedly
  - parameters form a coherent concept
  - number of parameters exceeds 3-4


before: grouped parameters

  def draw_graph(self, data, x_min, x_max, y_min, y_max, color, width):
      # drawing logic
      pass

  def analyze_data(self, data, x_min, x_max, y_min, y_max):
      # analysis logic
      pass

  def export_data(self, data, x_min, x_max, y_min, y_max, format):
      # export logic
      pass


after: parameter object

  class DataRange:
      def __init__(self, x_min, x_max, y_min, y_max):
          self.x_min = x_min
          self.x_max = x_max
          self.y_min = y_min
          self.y_max = y_max

      def contains(self, x, y):
          return self.x_min <= x <= self.x_max and \
                 self.y_min <= y <= self.y_max

  def draw_graph(self, data, range, style):
      # drawing logic
      pass

  def analyze_data(self, data, range):
      # analysis logic
      pass

  def export_data(self, data, range, format):
      # export logic
      pass


benefits:

  - fewer parameters
  - can add behavior to the object
  - easier to add new related data
  - clearer intent


PHASE 11: REMOVE DEAD CODE

dead code has no tests and is not used.
remove it without mercy.


find dead code

check for unused imports:
  <terminal>python -m flake8 src/ --select=F401</terminal>

check for unused variables:
  <terminal>python -m pylint src/ --disable=all --enable=unused-variable</terminal>

check for unreachable code:
  <terminal>python -m vulture src/</terminal>


grep for potential dead code

  <terminal>grep -r "TODO.*remove" src/</terminal>
  <terminal>grep -r "FIXME.*deprecated" src/</terminal>
  <terminal>grep -r "def.*_old\|class.*_old" src/</terminal>


check for unused functions

  <terminal>grep -r "def " src/ | while read line; do
      func=$(echo $line | grep -o "def [a-z_]*" | cut -d' ' -f2)
      if [ -n "$func" ]; then
          count=$(grep -r "$func(" src/ tests/ | wc -l)
          if [ $count -le 1 ]; then
              echo "Possibly unused: $func"
          fi
      fi
  done</terminal>


before removing dead code

  [1] verify no tests reference it
  [2] search codebase for references
  [3] check git history for why it exists

  <terminal>grep -r "function_name" src/ tests/</terminal>

  <terminal>git log --all --source --full-history -S "function_name"</terminal>


remove safely

  <read><file>src/module.py</file></read>

  <edit>
  <file>src/module.py</file>
  <find>
  # Old implementation, kept for reference
  def old_function():
      pass
  </find>
  <replace>
  </replace>
  </edit>

  <terminal>python -m pytest tests/ -v</terminal>

tests should pass.
dead code by definition has no tests.


PHASE 12: SIMPLIFY CONDITIONAL LOGIC

complex conditionals indicate missing concepts.


consolidate duplicate conditions

before:

  if customer.is_vip:
      discount = 0.1
  if customer.tier == "VIP":
      discount = 0.1

after:

  if customer.is_vip:
      discount = 0.1


use De Morgan's laws

before:

  if not (customer.is_vip or customer.is_new):
      apply_standard_pricing()

after:

  if not customer.is_vip and not customer.is_new:
      apply_standard_pricing()


before:

  if not customer.is_vip and not customer.is_new:
      apply_standard_pricing()

after:

  if not (customer.is_vip or customer.is_new):
      apply_standard_pricing()


reverse conditionals for clarity

before:

  def process_order(self, order):
      if order.is_valid:
          if order.has_items:
              if order.customer.can_pay:
                  self._process(order)
              else:
                  return "Payment failed"
          else:
              return "No items"
      else:
          return "Invalid order"

after:

  def process_order(self, order):
      if not order.is_valid:
          return "Invalid order"
      if not order.has_items:
          return "No items"
      if not order.customer.can_pay:
          return "Payment failed"

      self._process(order)


PHASE 13: COMMON ANTI-PATTERNS AND FIXES


anti-pattern: god method

signs:
  - method over 50 lines
  - multiple levels of nesting
  - does many different things

fix:
  - extract methods for each responsibility
  - decompose conditionals
  - use guard clauses

before:

  def process(self, data):
      if data:
          for item in data:
              if item.type == "A":
                  if item.value > 0:
                      result = self._calculate_a(item.value)
                      self._save(result)
                  else:
                      self._log("Invalid value")
              elif item.type == "B":
                  if item.value > 0:
                      result = self._calculate_b(item.value)
                          self._save(result)
                      else:
                          self._log("Invalid value")
      return "Done"

after:

  def process(self, data):
      if not data:
          return "Done"

      for item in data:
          self._process_item(item)
      return "Done"

  def _process_item(self, item):
      if item.value <= 0:
          self._log("Invalid value")
          return

      if item.type == "A":
          self._process_type_a(item)
      elif item.type == "B":
          self._process_type_b(item)

  def _process_type_a(self, item):
      result = self._calculate_a(item.value)
      self._save(result)

  def _process_type_b(self, item):
      result = self._calculate_b(item.value)
      self._save(result)


anti-pattern: feature envy

signs:
  - method uses more data from another class
  - method "belongs" in another class

fix: move the method

before:

  class Order:
      def __init__(self, customer):
          self.customer = customer

      def get_discounted_total(self):
          if self.customer.tier == "VIP":
              return self.total * 0.9
          elif self.customer.tier == "LOYAL":
              return self.total * 0.95
          return self.total

after:

  class Order:
      def __init__(self, customer):
          self.customer = customer

      def get_discounted_total(self):
          return self.customer.get_discounted_price(self.total)


anti-pattern: primitive obsession

signs:
  - using primitives instead of small objects
  - related primitives travel together

fix: extract class

before:

  def calculate_shipping(self, street, city, state, zip_code):
      # shipping logic using all these parameters
      pass

  def validate_address(self, street, city, state, zip_code):
      # validation logic
      pass

after:

  class Address:
      def __init__(self, street, city, state, zip_code):
          self.street = street
          self.city = city
          self.state = state
          self.zip_code = zip_code

      def validate(self):
          # validation logic
          pass

      def get_shipping_cost(self):
          # shipping logic
          pass


PHASE 14: IDE REFACTORING TOOLS


VS Code refactoring

install python extension:
  <terminal>code --install-extension ms-python.python</terminal>

refactoring shortcuts:
  - F2: rename symbol
  - Ctrl+Shift+R (Mac: Cmd+Shift+R): refactor preview
  - Ctrl+. (Mac: Cmd+.): quick fix
  - F12: go to definition


PyCharm refactoring

refactoring menu:
  - Shift+F6: rename
  - Ctrl+Alt+M: extract method
  - Ctrl+Alt+V: extract variable
  - Ctrl+Alt+P: extract parameter
  - F6: move
  - Ctrl+Alt+N: inline


command line tools

rope (python refactoring library):
  <terminal>pip install rope</terminal>

  <terminal>rope refactor.py --extract-method extract_user_validation</terminal>

rope can:
  - extract method
  - rename
  - move
  - inline
  - extract variable


bowler (code refactoring tool):
  <terminal>pip install bowler</terminal>

  # create a refactoring script
  <terminal>cat > refactor_fixme.py << 'EOF'
  import bowler

  def rename_old_function(command):
      (
          command
          .capture("old_func = 'old_function_name'")
          .rename("new_function_name")
      )

  bowler.Query(".py").modify(rename_old_function).execute()
  EOF

  <terminal>python refactor_fixme.py --diff</terminal>


safe refactoring with git

create refactoring branch:
  <terminal>git checkout -b refactor/extract-validation</terminal>

after each small refactor:
  <terminal>git add -A && git commit -m "refactor: extract user validation"</terminal>

view progress:
  <terminal>git log --oneline refactor/extract-validation</terminal>

when done:
  <terminal>git checkout main</terminal>
  <terminal>git merge refactor/extract-validation --squash</terminal>
  <terminal>git commit -m "refactor: extract user validation to separate methods"</terminal>


PHASE 15: REFACTORING RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER refactor without tests
      tests are your safety net
      no tests, no refactoring, no exceptions

  [2] run tests after EVERY change
      every single change, no matter how small
      <terminal>python -m pytest tests/ -v</terminal>

  [3] commit when tests pass
      small commits are your friend
      make rollback trivial
      <terminal>git commit -m "refactor: description"</terminal>

  [4] revert if tests fail
      dont try to fix broken tests
      revert immediately
      try a smaller change

  [5] one refactoring at a time
      dont batch multiple changes
      test between each
      commit often

  [6] never change behavior
      structure only
      if behavior changes, its not refactoring
      tests prove behavior unchanged

  [7] keep changes small
      if you hesitate, make it smaller
      you can always make another change

  [8] refactor only when green
      fix tests first
      never refactor broken tests


PHASE 16: REFACTORING SESSION CHECKLIST


before starting:

  [ ] tests exist and pass
  [ ] working directory clean or on branch
  [ ] baseline coverage recorded
  [ ] refactoring goal identified

during refactoring:

  [ ] tests pass before each change
  [ ] change is small and focused
  [ ] tests pass after each change
  [ ] change is committed
  [ ] progress toward goal

after completing:

  [ ] all tests pass
  [ ] coverage unchanged or improved
  [ ] code is clearer
  [ ] no behavior changes
  [ ] commit message is clear


FINAL REMINDERS


refactoring is discipline

it requires patience.
it requires tests.
it requires small steps.

the discipline pays off in:
  - code you can understand
  - features you can add quickly
  - bugs you can fix safely


the golden rule

tests must pass before and after.
if tests fail, you broke it.
revert and try again.


when in doubt

make the change smaller.
you can always make another small change.
small changes are safe changes.


the goal

code that is easy to understand.
code that is easy to change.
code that does what it says.
no more, no less.

now go clean up some code.
