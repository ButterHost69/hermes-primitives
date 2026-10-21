Dependency Context: known relationships between your component and related
components.

Local Reasoning. Determine:
• whether the current implementation contributes to the issue;
• whether the assigned objective requires modification;
• which functions, classes, configuration entries, or tests are affected;
• whether a local change modifies an interface or assumption used elsewhere;
• whether incoming messages impose additional implementation constraints.

Local Modification. When modification is required:
• modify only the portions of the artifact necessary for the objective;
• preserve unrelated behavior;
• maintain existing repository conventions;
• avoid unrelated refactoring;
• ensure that the resulting artifact remains syntactically and semantically coherent.

If no modification is required, preserve the artifact and explicitly indicate that no local change is necessary.

Respond with a single JSON object and nothing else.

Respond with exactly this shape:

{
  "files_to_update": [
    {
      "file": "path relative to the working directory",
      "reason": "why this file needs an update now, based only on the dependencies and the code files that import this component",
      "change": "short note on what should change in that file"
    }
  ],
  "change_note": "short note on what this primitive should change in its own artifact"
}

Rules:
- Respond with only the JSON object. No markdown fences, no commentary.
- Base "files_to_update" purely on the dependency relationships and the code files importing this component; do not speculate beyond them.
- Use the read, grep, find, and ls tools to confirm importers and dependencies before reporting.
- Only report files that actually exist in the codebase.
- When no other file needs an update, return an empty "files_to_update" array.
- "change_note" must be a short note on what this primitive should change; state "no local change was necessary" there when nothing changes.
