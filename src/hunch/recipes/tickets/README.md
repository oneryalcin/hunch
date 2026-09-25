# tickets

Route 40 made-up support tickets: which team, is it urgent, how frustrated (a choice, a yes/no and a score). About $0.001 to run.

```
tickets.csv (40 rows: subject, body, gold_department)
  └─► ticket_triage   department: billing / technical / sales   (gold: gold_department, act 0.80)
                      urgent:     yes / no
                      frustration: Calm … Very angry (score)
```

```sh
hunch compile .     # what it will ask and cost
hunch run .         # ask, cache, write the ticket_triage table
hunch test .        # accuracy against gold_department, calibration, the act dial
hunch review .      # rows where the model and the answer key disagree, plus spot checks
```

Replace `tickets.csv` with your own rows and the questions with your own; the docs' quickstart walks through it.
