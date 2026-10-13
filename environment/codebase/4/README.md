# QueueMaster

QueueMaster is a small distributed task scheduler / job queue library written
in pure Python (standard library only, no external dependencies). It provides
two building blocks:

- **`queuemaster.scheduler`** — a priority-queue scheduler backed by a min-heap
  (`heapq`). Submit `Task`s and pull the highest-priority one with `next()`.
  Lower priority number = higher priority.
- **`queuemaster.backoff`** — an exponential backoff helper for retries. The
  first retry waits `base` seconds, each subsequent retry doubles the previous
  delay, capped at `max_delay`.
- **`queuemaster.tasks`** — a simple `Task` class carrying a `name`,
  `priority`, and optional `payload`.

## Example

```python
from queuemaster import scheduler, tasks, backoff

s = scheduler.Scheduler()
s.submit(tasks.Task("send_email", priority=2, payload={"to": "a@example.com"}))
s.submit(tasks.Task("flush_logs", priority=1, payload=None))

task = s.next()  # flush_logs (priority 1 comes first)
print(task.name)

delay = backoff.backoff_delay(attempt=3, base=1.0, max_delay=60.0)
print(delay)  # 4.0
```

## Running the tests

With the standard library:

```bash
python3 -m unittest discover -s tests -t .
```

Or, if you have pytest installed:

```bash
python3 -m pytest
```
