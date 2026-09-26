# agent-commands

Guard a coding agent's shell commands: 38 it really ran, three yes/no questions each. The quickstart; about $0.001 to run.

```
commands.csv (38 rows: request, cwd, description, command, gold_destroys)
  └─► command_guard   destroys:        yes / no   (gold: gold_destroys, act 0.90)
                      reaches_outside: yes / no
                      sends_out:       yes / no
      a person looks first if any answer is yes
```

```sh
hunch compile .     # what it will ask and cost
hunch run .         # ask, cache, write the command_guard table
hunch test .        # accuracy against gold_destroys, the act dial, the examples
hunch review .      # rows where the model and the answer key disagree, plus spot checks
```

Replace `commands.csv` with your own rows and the questions with your own; the docs' quickstart walks through it. To run the guard in front of Claude Code or Codex, `hunch hook install` in your project.
