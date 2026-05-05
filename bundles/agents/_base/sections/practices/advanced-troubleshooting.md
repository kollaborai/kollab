advanced troubleshooting

when everything seems broken:
  [1] verify basic assumptions
      <terminal>pwd</terminal>
      <terminal>which python</terminal>
      <terminal>git status</terminal>

  [2] check environment
      <terminal>echo $PATH</terminal>
      <terminal>env | grep -i python</terminal>
      <terminal>pip list | head -20</terminal>

  [3] isolate the problem
      - does it work in a fresh venv?
      - does it work on a different branch?
      - does it work with an older version?

  [4] search for similar issues
      <terminal>git log --all --grep="similar keyword"</terminal>
      <terminal>grep -r "error message" .</terminal>

  [5] minimal reproduction
      - create smallest possible example that shows the bug
      - remove unrelated code
      - test in isolation

system debugging:
  <terminal>ps aux | grep python</terminal>
  <terminal>lsof -i :8000</terminal>
  <terminal>df -h</terminal>
  <terminal>free -h</terminal>
  <terminal>tail -f logs/app.log</terminal>
