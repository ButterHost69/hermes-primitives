class Task:
    """A unit of work to be scheduled and run by a Scheduler."""

    def __init__(self, name, priority, payload=None):
        self.name = name
        self.priority = priority
        self.payload = payload

    def __lt__(self, other):
        # Lower priority number = higher priority.
        return self.priority < other.priority

    def __repr__(self):
        return "Task(name={!r}, priority={!r})".format(self.name, self.priority)
