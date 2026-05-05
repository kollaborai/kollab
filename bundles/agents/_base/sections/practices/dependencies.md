dependency management

check dependencies:
  <terminal>pip list</terminal>
  <terminal>pip show package_name</terminal>
  <read><file>requirements.txt</file></read>

install dependencies:
  <terminal>pip install -r requirements.txt</terminal>
  <terminal>pip install package_name</terminal>
  <terminal>pip install -e .</terminal>

update dependencies:
  <terminal>pip list --outdated</terminal>
  <terminal>pip install --upgrade package_name</terminal>
  <terminal>pip freeze > requirements.txt</terminal>

virtual environments:

check if in venv:
  <terminal>which python</terminal>
  <terminal>echo $VIRTUAL_ENV</terminal>

if not in venv, recommend:
  <terminal>python -m venv venv</terminal>
  <terminal>source venv/bin/activate</terminal>  # mac/linux
  <terminal>venv\\Scripts\\activate</terminal>  # windows

dependency conflicts:
  symptom: "ERROR: package-a requires package-b>=2.0 but you have 1.5"
  fix:
    <terminal>pip install --upgrade package-b</terminal>
    <terminal>pip install -r requirements.txt</terminal>
