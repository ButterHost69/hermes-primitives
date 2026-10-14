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

If no modification is required, preserve the artifact and explicitly indicate that no local change is necessary
