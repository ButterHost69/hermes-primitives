You are fixing a real GitHub issue in the repository at your working directory.

Repository: __REPO__
Base commit: __BASE_COMMIT__
Task: __INSTANCE_ID__

Read the repository and make the source change that resolves the issue. The
hidden oracle tests are applied and run afterwards, in the project's own
environment.

Rules:
- Change only source files; do not modify tests (the oracle supplies them).
- Keep the fix minimal and consistent with the project's conventions.
- Do not use `assert` for input validation or a user-facing error; raise an
  explicit exception such as `ValueError` so callers can catch it. Apply the
  same treatment to the related validation paths when the issue is about
  validation.
- Stay inside this repository. Never search or read `/`, `$HOME`, or other
  paths outside the working tree; use repository-relative paths.
- Do not install packages and do not access the network. This checkout may
  not have the project's dependencies; reason from the source instead.
- If a command fails because a dependency is missing, continue from the source
  rather than installing it.
- When done, reply with a short summary of what you changed.
